"""Isolated WorldMirror inference; keeps its generic `src` imports out of ComfyUI."""
import argparse
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    args = parser.parse_args()
    vendor = Path(__file__).parent / "vendor/worldmirror"
    sys.path[:0] = [str(vendor / "deps"), str(vendor)]
    import numpy as np
    import torch
    from PIL import Image
    from src.models.models.worldmirror import WorldMirror
    from scene_environment import prune_background_occluders

    request = json.loads(Path(args.request).read_text())
    images = torch.stack([torch.from_numpy(np.asarray(Image.open(p).convert("RGB").resize(
        tuple(request["size"]), Image.Resampling.LANCZOS)).copy()).permute(2, 0, 1).float() / 255
        for p in request["files"]])[None].cuda()
    started = time.monotonic()
    print("WorldMirror: loading local weights", flush=True)
    model = WorldMirror.from_pretrained(request["model"]).eval().cuda()
    model.gs_renderer.enable_prune = False  # Remove foreground before the official confidence-weighted merge.
    print("WorldMirror: reconstructing background", flush=True)
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        prediction = model({"img": images}, cond_flags=[0, 0, 0])
    masks = torch.from_numpy(np.load(request["masks"])).cuda()
    foreground = torch.nn.functional.max_pool2d(masks[:, None], 9, 1, 4)[:, 0] > 0.1
    background = ~foreground.flatten()
    splats = prediction["splats"]
    filtered = {k: v[:, background] for k, v in splats.items()}
    merged = model.gs_renderer.prune_gs(filtered, voxel_size=0.002)
    background_splats, visibility = prune_background_occluders(
        {k:v[0] for k,v in merged.items()}, masks, prediction['camera_poses'][0],
        prediction['camera_intrs'][0], prediction['depth'][0])
    output = {k: prediction[k].cpu() for k in ["camera_poses", "camera_intrs", "depth"]}
    output["splats"] = {k: v.detach().cpu() for k, v in background_splats.items()}
    output['background_visibility'] = visibility
    torch.save(output, request["result"])
    print(json.dumps({"seconds": time.monotonic() - started,
                      "peak_bytes": torch.cuda.max_memory_allocated(),
                      "background_points": len(output["splats"]["means"]),
                      "background_visibility": visibility}), flush=True)


if __name__ == "__main__":
    main()
