import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import numpy as np
from PIL import Image, PngImagePlugin
import torch
from safetensors.torch import load_file, save_file

import comfy.model_management as mm
import folder_paths
from comfy_api.latest._util.geometry_types import SPLAT
from comfy_execution.graph import ExecutionBlocker
from .grid_nodes import checkpoint_path, source_hash, file_digest, new_target, commit_manifest
from .scene_geometry import assemble_scene, apply_viewer_transform, viewer_camera, raster_camera, fit_subject_camera, native_proportion_camera

IDENTITY_MODEL = {"position": {"x": 0., "y": 0., "z": 0.},
                  "quaternion": {"x": 0., "y": 0., "z": 0., "w": 1.},
                  "scale": {"x": 1., "y": 1., "z": 1.}}


def read_sequence(value, original=None):
    path = checkpoint_path(value)
    m = json.loads(path.read_text(encoding="utf-8"))
    if (m.get("status") != "complete" or m.get("kind") != "orbit_grid"
            or m.get("type") != "output" or m.get("schema_version") != 2
            or m.get("frame_count") not in (73, 124) or 0 not in m.get("elevations", [])
            or value != m.get("subfolder", "") + "/manifest.json"):
        raise ValueError("固定3Dには、完成した水平周回の再利用IDが必要です。")
    if original is not None and m.get("source_sha256") != source_hash(original):
        raise ValueError("周回データと入力画像が異なります。新規生成へ切り替えるか、同じ画像の周回IDを指定してください。")
    row = m["elevations"].index(0)
    indices = [int(i * (m["frame_count"] - 1) / 12) for i in range(12)]
    files = [(path.parent / f"row_{row:02d}" / f"frame_{i:03d}.png").resolve() for i in indices]
    for image in files:
        if not image.is_relative_to(path.parent):
            raise ValueError("周回画像の参照先が不正です。")
        with Image.open(image) as im:
            if im.size != (m["width"], m["height"]):
                raise ValueError("周回画像のサイズが一致しません。")
            im.verify()
    return m, files


def splat_from_arrays(values):
    return SPLAT(values["means"][None], values["scales"][None], values["quats"][None],
                 values["opacities"][None, :, None], values["sh"][None])


def read_scene(value, original=None):
    path = checkpoint_path(value)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("kind") != "rigid_scene" or manifest.get("status") != "complete"
            or value != manifest.get("subfolder", "") + "/manifest.json"):
        raise ValueError("完成した固定3DのIDを指定してください。")
    if original is not None and manifest.get("source_sha256") != source_hash(original):
        raise ValueError("固定3Dと入力画像が異なります。同じ元画像を選んでください。")
    scene = (path.parent / "scene.safetensors").resolve()
    if not scene.is_relative_to(path.parent) or file_digest(scene) != manifest.get("scene_sha256"):
        raise ValueError("固定3Dの保存データが変更・破損しています。")
    for name,digest in manifest.get('texture_sha256',{}).items():
        texture=(path.parent/name).resolve()
        if not texture.is_relative_to(path.parent) or file_digest(texture)!=digest:raise ValueError('固定3Dの背景画像が変更・破損しています。')
    return dict(manifest, camera=viewer_camera(manifest), camera_fov_axis="vertical"), scene


class H3OrbitRigidScene:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "mode": ("STRING", {"forceInput": True}), "original": ("IMAGE",),
            "orbit_manifest": ("STRING", {"forceInput": True, "lazy": True}),
            "subject": ("SPLAT", {"lazy": True}), "subject_camera": ("LOAD3D_CAMERA", {"lazy": True}),
            "background_removal": ("BACKGROUND_REMOVAL", {"lazy": True}),
            "checkpoint": ("STRING", {"default": "", "tooltip": "再構成モードで使う周回結果の H3_ORB360/.../manifest.json。入力画像が一致する場合だけ使用できます。"}),
        }, "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}}

    RETURN_TYPES = ("SPLAT", "LOAD3D_CAMERA", "INT", "INT", "STRING", "LOAD3D_MODEL_INFO")
    RETURN_NAMES = ("scene", "camera", "width", "height", "filename_prefix", "model_placement")
    FUNCTION = "build"
    CATEGORY = "MiniMax H3/Orbit"

    def check_lazy_status(self, mode, original, checkpoint, orbit_manifest=None, subject=None,
                          subject_camera=None, background_removal=None, prompt=None, extra_pnginfo=None):
        if not mode.startswith("固定3D"):
            return []
        if mode == "固定3Dを開く（3D ID指定）":
            read_scene(checkpoint.strip(), original)
            return []
        if mode == "固定3Dを再構成（周回ID指定）":
            if not checkpoint.strip():
                raise ValueError("再構成する周回IDを指定してください。新しい入力なら「固定3Dを新規生成」を選びます。")
            read_sequence(checkpoint.strip(), original)
        elif orbit_manifest is None:
            return ["orbit_manifest"]
        else:
            read_sequence(orbit_manifest, original)
        return [name for name, value in [("subject", subject), ("subject_camera", subject_camera),
                                         ("background_removal", background_removal)] if value is None]

    @classmethod
    def IS_CHANGED(cls, mode, checkpoint, **kwargs):
        if mode == "固定3Dを開く（3D ID指定）" and checkpoint.strip():
            manifest, scene = read_scene(checkpoint.strip())
            return file_digest(checkpoint_path(checkpoint.strip())) + manifest["scene_sha256"]
        if mode == "固定3Dを再構成（周回ID指定）" and checkpoint.strip():
            _, files = read_sequence(checkpoint.strip())
            return file_digest(checkpoint_path(checkpoint.strip())) + "".join(file_digest(f) for f in files)
        return ""

    def build(self, mode, original, checkpoint, orbit_manifest=None, subject=None, subject_camera=None,
              background_removal=None, prompt=None, extra_pnginfo=None):
        if not mode.startswith("固定3D"):
            return tuple(ExecutionBlocker(None) for _ in range(6))
        if mode == "固定3Dを開く（3D ID指定）":
            metadata, scene = read_scene(checkpoint.strip(), original)
            values = load_file(str(scene), device="cpu")
            from .scene_layers import prepare_viewer_layers
            prepare_viewer_layers(scene, metadata, values)
            camera = fit_subject_camera(values, metadata['camera'], metadata.get('alignment', {}).get('components'),
                                        metadata['width'], metadata['height'])
            if metadata.get('projection')!='unified_textured_scene':camera = native_proportion_camera(camera,metadata['width'],metadata['height'])
            return {"ui": {"h3_scene": [{"width": metadata["width"], "height": metadata["height"]}]},
                    "result": (splat_from_arrays(values), camera, metadata["width"], metadata["height"],
                               metadata["subfolder"] + "/scene", [IDENTITY_MODEL])}
        sequence = checkpoint.strip() if mode == "固定3Dを再構成（周回ID指定）" else orbit_manifest
        manifest, files = read_sequence(sequence, original)
        model_path = Path(folder_paths.models_dir) / "worldmirror"
        if not (model_path / "model.safetensors").is_file():
            raise ValueError("models/worldmirror にWorldMirrorのモデルがありません。")
        # Snapshot CPU tensors before releasing all generation models from the single GPU.
        subject = SPLAT(*(v.detach().cpu() for v in [subject.positions, subject.scales, subject.rotations,
                                                   subject.opacities, subject.sh]),
                        counts=subject.counts.detach().cpu() if subject.counts is not None else None)
        scale = 518 / max(manifest["width"], manifest["height"])
        width = max(14, round(manifest["width"] * scale / 14) * 14)
        height = max(14, round(manifest["height"] * scale / 14) * 14)
        images = torch.stack([torch.from_numpy(np.asarray(Image.open(p).convert("RGB").resize(
            (width, height), Image.Resampling.LANCZOS)).copy()).float() / 255 for p in files])
        mm.unload_all_models()
        mm.soft_empty_cache()
        with torch.inference_mode():
            masks = background_removal.encode_image(images).detach().float().cpu()
        if any(int((mask > .5).sum()) < 50 for mask in masks):
            raise ValueError("人物を分離できない周回視点がありました。前景と背景の区別が明確な画像が必要です。")
        mm.unload_all_models()
        mm.soft_empty_cache()
        scratch_root = Path(folder_paths.base_path).parent / "Codex/work/h3-orbit-runtime"
        scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="scene_", dir=scratch_root) as scratch:
            work = Path(scratch)
            np.save(work / "masks.npy", masks.numpy())
            request = {"files": [str(p) for p in files], "size": [width, height],
                       "model": str(model_path), "masks": str(work / "masks.npy"),
                       "result": str(work / "prediction.pt")}
            (work / "request.json").write_text(json.dumps(request))
            with (work / "worker.log").open("w") as log:
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("scene_worker.py")),
                                            str(work / "request.json")], stdout=log, stderr=subprocess.STDOUT)
                try:
                    started = time.monotonic()
                    while process.poll() is None:
                        mm.throw_exception_if_processing_interrupted()
                        if time.monotonic() - started > 600:
                            raise RuntimeError("背景3Dの再構成が10分以内に完了しませんでした。")
                        time.sleep(.2)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            log_text = (work / "worker.log").read_text()
            if process.returncode != 0:
                failure_log = scratch_root / ("failed_" + uuid.uuid4().hex + ".log")
                failure_log.write_text(log_text, encoding="utf-8")
                raise RuntimeError(f"背景3Dの再構成に失敗しました。記録: {failure_log}\n" + log_text[-2200:])
            prediction = torch.load(work / "prediction.pt", weights_only=True, map_location="cpu")
        # Geometry may use the model's small input; textures retain source detail.
        texture_scale=min(1.,1024/max(manifest['width'],manifest['height']))
        texture_size=(round(manifest['width']*texture_scale),round(manifest['height']*texture_scale))
        textures=np.stack([np.asarray(Image.open(p).convert('RGB').resize(texture_size,Image.Resampling.LANCZOS),dtype=np.float32)/255 for p in files])
        merged, camera, alignment = assemble_scene(prediction, masks, subject, subject_camera, (width, height), images, textures)
        # A wide orbit stores the full 50% frame. Start the 3D viewer at the usual
        # 100% composition while retaining all surroundings for mouse zoom-out.
        basis = manifest.get('frame_basis_zoom', 1)
        camera['fov'] = math.degrees(2 * math.atan(math.tan(math.radians(camera['fov']) / 2) * basis))
        camera = fit_subject_camera(merged, camera, alignment['components'], manifest['width'], manifest['height'])
        from .scene_layers import low_orbit_camera
        camera = low_orbit_camera(camera,alignment['ground']['floor'])
        target, subfolder = new_target()
        save_file(merged, str(target / "scene.safetensors"))
        np.savez_compressed(target/'projection_sources.npz',cameras=prediction['camera_poses'][0].numpy(),
                            intrinsics=prediction['camera_intrs'][0].numpy(),masks=masks.numpy(),
                            center=np.array(alignment['subject_center_before_alignment']),
                            upright=np.array(alignment['upright_rotation']))
        if prompt is not None:
            (target / "prompt.json").write_text(json.dumps(prompt, ensure_ascii=False, indent=2), encoding="utf-8")
        if extra_pnginfo and extra_pnginfo.get("workflow"):
            (target / "workflow.json").write_text(json.dumps(extra_pnginfo["workflow"], ensure_ascii=False, indent=2), encoding="utf-8")
        metadata = {"schema_version": 1, "kind": "rigid_scene", "type": "output", "status": "complete",
                    "subfolder": subfolder, "source_sha256": source_hash(original),
                    "width": manifest["width"], "height": manifest["height"], "camera": camera, "camera_fov_axis": "vertical",
                    "orbit_manifest": sequence, "input_files": [str(p.relative_to(Path(folder_paths.get_output_directory()))) for p in files],
                    "frame_basis_zoom": basis,
                    "input_sha256": [file_digest(p) for p in files], "alignment": alignment,
                    "scene_sha256": file_digest(target / "scene.safetensors"),
                    "worldmirror_commit": "c61300e47ce06db2f02c8e2551aae68be7ae3a0b",
                    "quality_status": "requires_visual_review", "worker_log": log_text}
        commit_manifest(target, metadata)
        from .scene_layers import prepare_viewer_layers
        prepare_viewer_layers(target / 'scene.safetensors', metadata, merged)
        camera = native_proportion_camera(camera,metadata['width'],metadata['height'])
        return {"ui": {"h3_scene": [{"width": metadata["width"], "height": metadata["height"]}]},
                "result": (splat_from_arrays(merged), camera, metadata["width"], metadata["height"], subfolder + "/scene", [IDENTITY_MODEL])}


class H3OrbitSceneSnapshot:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"scene_id": ("STRING",), "camera_json": ("STRING", {"multiline": True})},
                "optional": {"model_json": ("STRING", {"default": "[]"})}}
    RETURN_TYPES = ()
    OUTPUT_NODE = True
    FUNCTION = "save"
    CATEGORY = "MiniMax H3/Orbit"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")  # Every explicit Save click creates a new snapshot.

    def save(self, scene_id, camera_json, model_json="[]"):
        path = checkpoint_path(scene_id.rstrip("/") + "/manifest.json")
        manifest = json.loads(path.read_text())
        if manifest.get("kind") != "rigid_scene" or manifest.get("status") != "complete":
            raise ValueError("保存する固定3Dシーンがありません。")
        scene = (path.parent / "scene.safetensors").resolve()
        if not scene.is_relative_to(path.parent) or file_digest(scene) != manifest.get("scene_sha256"):
            raise ValueError("固定3Dの保存データが変更・破損しています。")
        output_width=manifest.get('snapshot_width',manifest['width'])
        output_height=manifest.get('snapshot_height',manifest['height'])
        if any(type(v) is not int or not 1<=v<=16384 for v in (output_width,output_height)) or output_width*output_height>40000000:
            raise ValueError('保存画像の元サイズが不正です。')
        if abs(output_width/output_height-manifest['width']/manifest['height'])>max(2/manifest['height'],.001):
            raise ValueError('保存画像の縦横比が元シーンと一致しません。')
        camera = json.loads(camera_json)
        if not isinstance(camera, dict):
            raise ValueError("カメラの形式が不正です。")
        for key in ["position", "target"]:
            if not isinstance(camera.get(key), dict):
                raise ValueError("カメラ位置がありません。")
            for axis in "xyz":
                value = camera[key].get(axis)
                if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e5:
                    raise ValueError("カメラ位置が範囲外です。")
        for key, lower, upper in [("fov", 1, 175), ("zoom", .01, 100)]:
            value = camera.get(key, 1 if key == "zoom" else 45)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("カメラの画角・倍率が範囲外です。")
        if "quaternion" in camera:
            q = camera["quaternion"]
            if not isinstance(q, dict) or any(type(q.get(k)) not in (int, float)
                    or not math.isfinite(q[k]) or abs(q[k]) > 1 for k in "xyzw"):
                raise ValueError("カメラの回転が不正です。")
            if abs(sum(q[k] ** 2 for k in "xyzw") - 1) > 1e-3:
                raise ValueError("カメラの回転が正規化されていません。")
        if camera.get("cameraType", "perspective") not in ("perspective", "orthographic"):
            raise ValueError("カメラの種類が不正です。")
        if sum((camera["position"][k] - camera["target"][k]) ** 2 for k in "xyz") < 1e-12:
            raise ValueError("カメラ位置と注視点が同じです。")
        values = load_file(str(scene), device="cpu")
        models = json.loads(model_json)
        if not isinstance(models, list) or len(models) > 1:
            raise ValueError("固定3Dの配置は一つだけ指定してください。")
        values = apply_viewer_transform(values, models[0] if models else None)
        mm.unload_all_models()
        mm.soft_empty_cache()
        raster_camera(camera, output_width, output_height)  # Validate an orthographic frustum too.
        scratch_root = Path(folder_paths.base_path).parent / "Codex/work/h3-orbit-runtime"
        scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="render_", dir=scratch_root) as scratch:
            work = Path(scratch)
            request = {"scene": str(scene), "model": models[0] if models else None,
                       "camera": camera, "width": output_width, "height": output_height,
                       "result": str(work / "render.npy")}
            (work / "request.json").write_text(json.dumps(request))
            with (work / "renderer.log").open("w") as log:
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("scene_render.py")),
                                            str(work / "request.json")], stdout=log, stderr=subprocess.STDOUT)
                try:
                    started = time.monotonic()
                    while process.poll() is None:
                        mm.throw_exception_if_processing_interrupted()
                        if time.monotonic() - started > 180:
                            raise RuntimeError("3D画像の保存が3分以内に完了しませんでした。")
                        time.sleep(.2)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            log_text = (work / "renderer.log").read_text()
            if process.returncode != 0:
                failure_log = scratch_root / ("render_failed_" + uuid.uuid4().hex + ".log")
                failure_log.write_text(log_text, encoding="utf-8")
                raise RuntimeError(f"3D画像の保存に失敗しました。記録: {failure_log}\n" + log_text[-1800:])
            image = np.load(work / "render.npy", allow_pickle=False)
            if image.shape != (output_height, output_width, 3) or not np.isfinite(image).all():
                raise ValueError("3D保存画像の寸法・画素が不正です。")
            render_stats = json.loads(log_text.strip().splitlines()[-1])
        target = Path(folder_paths.get_output_directory()).resolve() / "H3_ORB360/snapshots"
        if not target.resolve().is_relative_to(Path(folder_paths.get_output_directory()).resolve()):
            raise ValueError("保存先がoutput外を指しています。")
        target.mkdir(exist_ok=True, parents=True)
        name = "scene_" + uuid.uuid4().hex[:16] + ".png"
        info = PngImagePlugin.PngInfo()
        info.add_text("h3_orbit_view", json.dumps({"scene_id": scene_id, "camera": camera,
                                                   "models": models, **render_stats}))
        Image.fromarray((image.clip(0, 1) * 255).astype(np.uint8)).save(target / name, pnginfo=info)
        return {"ui": {"images": [{"filename": name, "subfolder": "H3_ORB360/snapshots", "type": "output"}]}}
