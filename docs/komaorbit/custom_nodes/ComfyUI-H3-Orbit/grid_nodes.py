import copy
import datetime
import hashlib
import json
import re
import tempfile
import uuid
from pathlib import Path

import numpy as np
import torch
from PIL import Image, PngImagePlugin

import comfy.model_management
import folder_paths
from comfy_execution.graph import ExecutionBlocker

from .camera import GENERATION_PROFILES, anchor_prompts, camera_plan, orbit_prompt, loop_prompt, validate_plan
from .core import FRAME_COUNT, FPS, validate_geometry
from .nodes import pil_image


def single(values, name):
    if len(values) != 1:
        raise ValueError(f"{name}は一つだけ接続してください。")
    return values[0]


def source_hash(original):
    if original.ndim != 4 or original.shape[0] != 1 or original.shape[-1] not in (3, 4):
        raise ValueError("比較用の元画像は一枚必要です。")
    im = pil_image(original[0])
    return hashlib.sha256(f"{im.width}x{im.height}:".encode() + im.tobytes()).hexdigest()


def checkpoint_path(value):
    if not isinstance(value, str) or not re.fullmatch(r"H3_ORB360/[A-Za-z0-9_-]+/manifest\.json", value):
        raise ValueError("再利用IDは H3_ORB360/日時_識別子/manifest.json の形式で指定してください。")
    output = Path(folder_paths.get_output_directory()).resolve()
    target = (output / value).resolve()
    if not target.is_relative_to(output):
        raise ValueError("再利用IDがComfyUIのoutput外を指しています。")
    return target


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def new_target():
    output = Path(folder_paths.get_output_directory()).resolve()
    root = output / "H3_ORB360"
    if not root.resolve().is_relative_to(output):
        raise ValueError("H3_ORB360保存先がComfyUIのoutput外を指しています。")
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_")
    target = Path(tempfile.mkdtemp(prefix=stamp, dir=root))
    return target, target.relative_to(output).as_posix()


def grid_manifest(subfolder, geometry, original, plan, kind, frame_count=FRAME_COUNT):
    return {
        "schema_version": 2, "status": "complete", "type": "output", "subfolder": subfolder,
        "kind": kind, "frame_count": 4 if kind == "references" else frame_count,
        "fps": 0 if kind == "references" else FPS,
        "row_count": len(plan["elevations"]), "elevations": list(plan["elevations"]),
        "width": geometry["export_width"], "height": geometry["export_height"],
        "source_width": geometry["source_width"], "source_height": geometry["source_height"],
        "source_sha256": source_hash(original), "source_hash_kind": "rgb8_dimensions",
        "geometry": dict(geometry), "plan": copy.deepcopy(plan),
        "frame_basis_zoom": geometry.get("frame_basis_zoom", 1),
        "default_row": plan["elevations"].index(0),
        "pose_status": "requested_not_measured", "quality_status": "not_automatically_validated",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def write_metadata(target, manifest, original, prompt, extra_pnginfo, unique_id, related=None):
    pil_image(original[0]).save(target / "original.png")
    manifest["original_file_sha256"] = file_digest(target / "original.png")
    workflow = copy.deepcopy((extra_pnginfo or {}).get("workflow"))
    if workflow is not None:
        workflow.setdefault("extra", {})["source_workflow_id"] = workflow.get("id")
        workflow["id"] = str(uuid.uuid4())
        for node in workflow.get("nodes", []):
            state = manifest if str(node.get("id")) == str(unique_id) else (related or {}).get(str(node.get("id")))
            if state is not None:
                node.setdefault("properties", {}).update(h3_orbit=state, h3_orbit_index=0,
                                                          h3_orbit_row=state["default_row"])
    pnginfo = PngImagePlugin.PngInfo()
    for key, value in {"prompt": prompt, "workflow": workflow}.items():
        (target / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        if value is not None:
            pnginfo.add_text(key, json.dumps(value, ensure_ascii=False))
    return pnginfo


def export_image(frame, geometry):
    g = geometry
    box = (g["left"], g["top"], g["left"] + g["content_width"], g["top"] + g["content_height"])
    im = pil_image(frame).crop(box)
    size = (g["export_width"], g["export_height"])
    return im if im.size == size else im.resize(size, Image.Resampling.LANCZOS)


def commit_manifest(target, manifest):
    staged = target / "manifest.json.tmp"
    staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    staged.replace(target / "manifest.json")


def attach_metadata(path, pnginfo):
    with Image.open(path) as image:
        image.load()
        image.save(path, pnginfo=pnginfo, compress_level=3)


def read_references(value, plan, geometry, original):
    path = checkpoint_path(value)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 2 or manifest.get("status") != "complete"
            or manifest.get("kind") != "references" or manifest.get("frame_count") != 4
            or manifest.get("type") != "output" or manifest.get("fps") != 0
            or manifest.get("row_count") != len(plan["elevations"])
            or manifest.get("elevations") != plan["elevations"]
            or manifest.get("subfolder", "") + "/manifest.json" != value):
        raise ValueError("再利用IDが完成した参照画像セットを指していません。")
    if manifest.get("plan") != plan or manifest.get("geometry") != geometry:
        raise ValueError("保存した参照画像と視点構成・背景説明・生成サイズが異なります。再利用IDを空欄にして作り直してください。")
    if manifest.get("source_sha256") != source_hash(original):
        raise ValueError("保存した参照画像と入力画像が異なります。再利用IDを空欄にしてください。")
    original_path = (path.parent / "original.png").resolve()
    if not original_path.is_relative_to(path.parent) or file_digest(original_path) != manifest.get("original_file_sha256"):
        raise ValueError("保存した元画像が変更・破損しています。")
    images = []
    hashes = manifest.get("conditioning_sha256", {})
    for row in range(len(plan["elevations"])):
        for col in range(4):
            name = f"conditioning/row_{row:02d}_{col:03d}.png"
            frame = (path.parent / name).resolve()
            if not frame.is_relative_to(path.parent) or file_digest(frame) != hashes.get(name):
                raise ValueError("保存した参照画像が変更・破損しています。再利用IDを空欄にして作り直してください。")
            with Image.open(frame) as im:
                if im.size != (geometry["canvas_width"], geometry["canvas_height"]):
                    raise ValueError("保存した参照画像のサイズが一致しません。")
                images.append(torch.from_numpy(np.array(im.convert("RGB"), dtype=np.float32) / 255.0).unsqueeze(0))
    return manifest, images


class H3OrbitPlan:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "profile": (list(GENERATION_PROFILES), {"default": "上下3段階"}),
            "scene_context": ("STRING", {"default": "", "multiline": True,
                "tooltip": "任意。背景の場所や見えない側の説明。空欄でも入力画像から補います。"}),
        }, "optional": {"frame_basis_zoom": ("FLOAT", {"forceInput": True, "default": 1.}),
              "mode": (["固定3Dを新規生成", "固定3Dを再構成（周回ID指定）", "固定3Dを開く（3D ID指定）", "画像周回を生成", "参照画像だけ"],
              {"default": "固定3Dを新規生成", "tooltip": "固定3Dは同じ体形で上下左右を描画します。比較用の画像周回は角度ごとに体形が変わる場合があります。"})}}

    RETURN_TYPES = ("STRING", "H3_ORBIT_PLAN", ["周回まで生成", "参照画像だけ", "周回を再利用"], "STRING")
    RETURN_NAMES = ("Qwen_prompts", "plan", "stage", "mode")
    OUTPUT_IS_LIST = (True, False, False, False)
    FUNCTION = "plan"
    CATEGORY = "MiniMax H3/Orbit"

    def plan(self, profile, scene_context, mode="画像周回を生成", frame_basis_zoom=1):
        stages = {"固定3Dを新規生成": "周回まで生成", "固定3Dを再構成（周回ID指定）": "周回を再利用", "固定3Dを開く（3D ID指定）": "周回を再利用",
                  "画像周回を生成": "周回まで生成", "参照画像だけ": "参照画像だけ"}
        if mode not in stages:
            raise ValueError("生成する方式を選び直してください。")
        selected = "水平のみ（比較用）" if mode.startswith("固定3D") else profile
        plan = camera_plan(selected, scene_context, frame_basis_zoom)
        return anchor_prompts(plan), plan, stages[mode], mode


class H3OrbitReferences:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE", {"lazy": True}), "plan": ("H3_ORBIT_PLAN",),
            "geometry": ("H3_ORBIT_GEOMETRY",), "original": ("IMAGE",),
            "stage": (["周回まで生成", "参照画像だけ", "周回を再利用"], {"default": "周回まで生成"}),
            "checkpoint": ("STRING", {"default": "", "tooltip": "以前の参照プレビューに表示された再利用ID。空欄ならQwenで新規生成。"}),
        }, "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"}}

    INPUT_IS_LIST = True
    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "IMAGE", "STRING", "H3_ORBIT_REFERENCES", "STRING")
    RETURN_NAMES = ("front", "right", "back", "left", "H3_prompts", "references", "FL_prompts")
    OUTPUT_IS_LIST = (True, True, True, True, True, False, True)
    OUTPUT_NODE = True
    FUNCTION = "prepare"
    CATEGORY = "MiniMax H3/Orbit"

    def check_lazy_status(self, checkpoint, images=None, **kwargs):
        if kwargs.get("stage") == ["周回を再利用"]:
            return []
        if single(checkpoint, "再利用ID").strip():
            return []
        return ["images"] if images is None or any(image is None for image in images) else []

    @classmethod
    def IS_CHANGED(cls, checkpoint, **kwargs):
        value = single(checkpoint, "再利用ID").strip()
        if not value:
            return ""
        path = checkpoint_path(value)
        digest = hashlib.sha256(path.read_bytes())
        # A changed or removed conditioning PNG must invalidate the execution cache.
        files = [path.parent / "original.png", *sorted((path.parent / "conditioning").glob("row_*.png"))]
        for item in files:
            if not item.resolve().is_relative_to(path.parent):
                raise ValueError("保存した参照画像が参照セットの外を指しています。")
            digest.update(item.name.encode())
            digest.update(item.read_bytes())
        return digest.hexdigest()

    def prepare(self, plan, geometry, original, stage, checkpoint, images=None,
                prompt=None, extra_pnginfo=None, unique_id=None):
        p, g, source = single(plan, "視点計画"), single(geometry, "サイズ情報"), single(original, "元画像")
        validate_plan(p)
        source_hash(source)
        validate_geometry(g, g["canvas_width"], g["canvas_height"], int(source.shape[2]), int(source.shape[1]))
        selected_stage = single(stage, "実行段階")
        if selected_stage == "周回を再利用":
            return {"result": tuple([ExecutionBlocker(None)] for _ in range(5)) + (ExecutionBlocker(None), [ExecutionBlocker(None)])}
        if selected_stage not in ("周回まで生成", "参照画像だけ"):
            raise ValueError("実行段階を選び直してください。")
        value = single(checkpoint, "再利用ID").strip()
        if value:
            manifest, images = read_references(value, p, g, source)
        else:
            if images is None or len(images) != len(p["elevations"]) * 4:
                raise ValueError("参照画像の枚数が視点計画と異なります。Qwenのバッチ数を1にしてください。")
            for image in images:
                if image.ndim != 4 or tuple(image.shape) != (1, g["canvas_height"], g["canvas_width"], 3):
                    raise ValueError("Qwen参照画像のサイズ・バッチ数が不一致です。元画像と同じ準備ノードから接続してください。")
            target, subfolder = new_target()
            manifest = grid_manifest(subfolder, g, source, p, "references")
            manifest["conditioning_sha256"] = {}
            manifest["anchor_prompts"] = anchor_prompts(p)
            (target / "conditioning").mkdir()
            for i, image in enumerate(images):
                comfy.model_management.throw_exception_if_processing_interrupted()
                row, col = divmod(i, 4)
                name = f"conditioning/row_{row:02d}_{col:03d}.png"
                pil_image(image[0]).save(target / name)
                manifest["conditioning_sha256"][name] = file_digest(target / name)
            for i, image in enumerate(images):
                comfy.model_management.throw_exception_if_processing_interrupted()
                row, col = divmod(i, 4)
                folder = target / f"row_{row:02d}"
                folder.mkdir(exist_ok=True)
                export_image(image[0], g).save(folder / f"frame_{col:03d}.png")
            pnginfo = write_metadata(target, manifest, source, single(prompt, "prompt") if prompt else None,
                                     single(extra_pnginfo, "extra_pnginfo") if extra_pnginfo else None,
                                     single(unique_id, "unique_id") if unique_id else None)
            attach_metadata(target / "row_00/frame_000.png", pnginfo)
            commit_manifest(target, manifest)
            value = subfolder + "/manifest.json"
            # Both fresh and resumed runs consume the same RGB8 checkpoint bytes.
            manifest, images = read_references(value, p, g, source)
        if selected_stage == "参照画像だけ":
            result = tuple([ExecutionBlocker(None)] for _ in range(5)) + (ExecutionBlocker(None), [ExecutionBlocker(None)])
        else:
            columns = tuple([images[row * 4 + col] for row in range(len(p["elevations"]))] for col in range(4))
            prompts = [orbit_prompt(e, p["scene_context"]) for e in p["elevations"]]
            result = columns + (prompts, manifest, [loop_prompt(e, p["scene_context"]) for e in p["elevations"]])
        return {"ui": {"h3_orbit": [manifest]}, "result": result}


class H3OrbitGridSave:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",), "references": ("H3_ORBIT_REFERENCES",),
            "geometry": ("H3_ORBIT_GEOMETRY",), "original": ("IMAGE",),
        }, "optional": {"frame_count": ("INT", {"default": 124, "min": 5, "max": 3600, "step": 17})},
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"}}

    INPUT_IS_LIST = True
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("orbit_manifest",)
    OUTPUT_NODE = True
    FUNCTION = "save"
    CATEGORY = "MiniMax H3/Orbit"

    def save(self, images, references, geometry, original, prompt=None, extra_pnginfo=None, unique_id=None, frame_count=None):
        counts = frame_count if frame_count is not None else [FRAME_COUNT]
        if not counts:
            raise ValueError("生成方式のフレーム数が必要です。")
        count = counts[0]
        if count not in (73, 124) or any(c != count for c in counts):
            raise ValueError("生成方式のフレーム数が一致しません。FL2VAは73、Ref2VAは124です。")
        refs, g, source = single(references, "参照セット"), single(geometry, "サイズ情報"), single(original, "元画像")
        if refs.get("kind") != "references" or refs.get("status") != "complete":
            raise ValueError("完成した参照画像セットが必要です。")
        plan = refs["plan"]
        validate_plan(plan)
        if refs["source_sha256"] != source_hash(source) or refs["geometry"] != g:
            raise ValueError("周回画像と元画像・参照セットの組み合わせが異なります。")
        if len(images) != len(plan["elevations"]):
            raise ValueError("周回の段数が参照セットと異なります。各高さを一つずつ生成してください。")
        for row in images:
            if row.ndim != 4 or row.shape[0] != count or row.shape[-1] != 3:
                raise ValueError(f"各高さの周回には{count}フレームが必要です。")
            validate_geometry(g, int(row.shape[2]), int(row.shape[1]), int(source.shape[2]), int(source.shape[1]))
        target, subfolder = new_target()
        manifest = grid_manifest(subfolder, g, source, plan, "orbit_grid", count)
        manifest["reference_checkpoint"] = refs["subfolder"] + "/manifest.json"
        prompt_builder = loop_prompt if count == 73 else orbit_prompt
        manifest["generation_profile"] = "FL2VA_73" if count == 73 else "Ref2VA_ORB360_124"
        manifest["orbit_prompts"] = [prompt_builder(e, plan["scene_context"]) for e in plan["elevations"]]
        for row, frames in enumerate(images):
            folder = target / f"row_{row:02d}"
            folder.mkdir()
            for col, frame in enumerate(frames):
                comfy.model_management.throw_exception_if_processing_interrupted()
                export_image(frame, g).save(folder / f"frame_{col:03d}.png", compress_level=3)
        actual_prompt = single(prompt, "prompt") if prompt else None
        node_id = single(unique_id, "unique_id") if unique_id else None
        link = (actual_prompt or {}).get(str(node_id), {}).get('inputs', {}).get('references')
        related = {str(link[0]): refs} if isinstance(link, list) and len(link) == 2 else None
        pnginfo = write_metadata(target, manifest, source, actual_prompt,
                                 single(extra_pnginfo, "extra_pnginfo") if extra_pnginfo else None,
                                 node_id, related)
        attach_metadata(target / "row_00/frame_000.png", pnginfo)
        commit_manifest(target, manifest)
        return {"ui": {"h3_orbit": [manifest]}, "result": (subfolder + "/manifest.json",)}
