import copy
import datetime
import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
import torch.nn.functional as F
from PIL import Image, PngImagePlugin

import comfy.utils
import comfy.model_management
import folder_paths

from .core import FRAME_COUNT, FPS, SCHEMA_VERSION, image_geometry, validate_geometry


def pil_image(tensor):
    pixels = tensor.detach().to(device="cpu").numpy()
    return Image.fromarray(np.clip(pixels[..., :3] * 255.0, 0, 255).round().astype(np.uint8))


class H3OrbitPrepare:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "long_edge": ("INT", {"default": 1024, "min": 32, "max": 4096, "step": 32,
                "tooltip": "生成する画像の長辺（px）。全体を縮小し、32の倍数への不足分だけ余白を付けます。"}),
        }}

    RETURN_TYPES = ("IMAGE", "INT", "INT", "H3_ORBIT_GEOMETRY")
    RETURN_NAMES = ("image", "width", "height", "geometry")
    FUNCTION = "prepare"
    CATEGORY = "MiniMax H3/Orbit"

    def prepare(self, image, long_edge):
        if image.ndim != 4 or image.shape[0] != 1 or image.shape[-1] not in (3, 4):
            raise ValueError("一枚のRGB/RGBA画像を入力してください。画像バッチは使用できません。")
        g = image_geometry(int(image.shape[2]), int(image.shape[1]), long_edge)
        resized = comfy.utils.common_upscale(image[..., :3].movedim(-1, 1),
            g["content_width"], g["content_height"], "lanczos", "disabled")
        right = g["canvas_width"] - g["content_width"] - g["left"]
        bottom = g["canvas_height"] - g["content_height"] - g["top"]
        padded = F.pad(resized, (g["left"], right, g["top"], bottom), mode="replicate")
        return (padded.movedim(1, -1), g["canvas_width"], g["canvas_height"], g)


class H3OrbitSave:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",), "geometry": ("H3_ORBIT_GEOMETRY",), "original": ("IMAGE",),
        }, "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"}}

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "MiniMax H3/Orbit"
    DESCRIPTION = "124フレームを元画像と同じ縦横比で保存。プレビューを左右ドラッグして周回位置を確認します。"

    def save(self, images, geometry, original, prompt=None, extra_pnginfo=None, unique_id=None):
        if images.ndim != 4 or images.shape[0] != FRAME_COUNT or images.shape[-1] not in (3, 4):
            raise ValueError(f"ORB360の固定プロンプトは{FRAME_COUNT}フレーム用です。生成lengthを124にしてください。")
        if original.ndim != 4 or original.shape[0] != 1 or original.shape[-1] not in (3, 4):
            raise ValueError("比較用の元画像は一枚必要です。")
        g = dict(geometry)
        validate_geometry(g, int(images.shape[2]), int(images.shape[1]), int(original.shape[2]), int(original.shape[1]))
        output = Path(folder_paths.get_output_directory()).resolve()
        root = output / "H3_ORB360"
        root.mkdir(parents=True, exist_ok=True)
        if not root.resolve().is_relative_to(output):
            raise ValueError("H3_ORB360保存先がComfyUIのoutput外を指しています。")
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_")
        target = Path(tempfile.mkdtemp(prefix=stamp, dir=root))
        subfolder = target.relative_to(output).as_posix()
        source = pil_image(original[0])
        source.save(target / "original.png")
        source_digest = hashlib.sha256((target / "original.png").read_bytes()).hexdigest()
        export_size = (g["export_width"], g["export_height"])
        manifest = {
            "schema_version": SCHEMA_VERSION, "status": "complete",
            "type": "output", "subfolder": subfolder,
            "frame_count": FRAME_COUNT, "fps": FPS,
            "width": export_size[0], "height": export_size[1],
            "source_width": g["source_width"], "source_height": g["source_height"],
            "source_sha256": source_digest, "geometry": g,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "motion": "requested_clockwise_orbit_not_geometrically_verified",
        }
        workflow = copy.deepcopy((extra_pnginfo or {}).get("workflow"))
        if workflow is not None:
            for node in workflow.get("nodes", []):
                if node.get("type") == "H3OrbitSave" and str(node.get("id")) == str(unique_id):
                    node.setdefault("properties", {})["h3_orbit"] = manifest
                    node["properties"]["h3_orbit_index"] = 0
        run = {"prompt": prompt, "workflow": workflow}
        (target / "workflow.json").write_text(json.dumps(run["workflow"], ensure_ascii=False, indent=2), encoding="utf-8")
        (target / "prompt.json").write_text(json.dumps(run["prompt"], ensure_ascii=False, indent=2), encoding="utf-8")
        pnginfo = PngImagePlugin.PngInfo()
        for key, value in run.items():
            if value is not None:
                pnginfo.add_text(key, json.dumps(value, ensure_ascii=False))
        box = (g["left"], g["top"], g["left"] + g["content_width"], g["top"] + g["content_height"])
        for i, frame in enumerate(images):
            comfy.model_management.throw_exception_if_processing_interrupted()
            # Export one frame at a time; never allocate a full upscaled video tensor.
            im = pil_image(frame).crop(box)
            if im.size != export_size:
                im = im.resize(export_size, Image.Resampling.LANCZOS)
            im.save(target / f"frame_{i:03d}.png", compress_level=3, pnginfo=pnginfo if i == 0 else None)
        # Only a complete sequence gets a manifest and a UI result.
        staged = target / "manifest.json.tmp"
        staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        staged.replace(target / "manifest.json")
        return {"ui": {"h3_orbit": [manifest]}}
