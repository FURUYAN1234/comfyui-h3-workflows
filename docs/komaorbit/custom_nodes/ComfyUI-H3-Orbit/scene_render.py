"""Isolated gsplat renderer using the same camera and model placement as the viewer."""
import importlib.util
import json
import math
from pathlib import Path
import sys
import time


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    pack = Path(__file__).parent
    sys.path[:0] = [str(pack / 'vendor/worldmirror/deps'), str(pack.parents[1]), str(pack)]
    # Comfy's argument parser must not consume our request filename.
    sys.argv = [sys.argv[0]]
    import torch
    import numpy as np
    from PIL import Image
    from safetensors.torch import load_file
    from comfy_extras.nodes_gaussian_splat import _camera_basis
    from scene_geometry import apply_viewer_transform
    from scene_layers import component_bounds, background_camera, foreground_mask, anchor_shift

    from runtime import load_renderer
    load_renderer(pack)
    from gsplat.rendering import rasterization

    started = time.monotonic()
    values = load_file(request['scene'], device='cpu')
    metadata = json.loads(Path(request['scene']).with_name('manifest.json').read_text())
    start, end = component_bounds(metadata.get('alignment', {}).get('components'), len(values['means']))
    anchor = (metadata.get('alignment',{}).get('ground') or {}).get('anchor')
    if anchor is not None:
        a={k:v[:1] for k,v in values.items()};a['means']=torch.tensor([anchor],dtype=values['means'].dtype)
        anchor=apply_viewer_transform(a,request.get('model'))['means'][0].cuda()
    values = apply_viewer_transform(values, request.get('model'))
    values = {k: v.cuda() for k, v in values.items()}
    width, height = request['width'], request['height']
    rgb = (values['sh'][:, 0] * .28209479177387814 + .5).clamp(0, 1)
    subject = foreground_mask(metadata.get('alignment', {}).get('components'),len(rgb)).cuda()
    environment=None
    if metadata.get('projection')=='unified_textured_scene':
        from scene_textures import TextureEnvironment
        environment=TextureEnvironment(Path(request['scene']).parent,metadata['texture_environment'],request.get('model'))
    jobs = request.get('jobs') or [{'camera':request['camera'],'result':request['result']}]
    for index, job in enumerate(jobs):
        camera = job['camera']
        eye, _, right, down, forward = _camera_basis(camera, 'cuda')
        axes = torch.stack([right, down, forward])
        view = torch.eye(4, device='cuda')
        view[:3, :3], view[:3, 3] = axes, -axes @ eye
        is_ortho = camera.get('cameraType') == 'orthographic'
        if is_ortho:
            focal = height / (camera['frustum']['top'] - camera['frustum']['bottom'])
        else:
            focal = height / (2 * math.tan(math.radians(camera.get('fov', 45)) / 2))
        focal *= camera.get('zoom', 1)
        intrinsic = torch.tensor([[focal, 0, width/2], [0, focal, height/2], [0, 0, 1]], device='cuda')
        bg_camera = background_camera(camera)
        bg_focal = height / (2*math.tan(math.radians(bg_camera['fov'])/2))*bg_camera.get('zoom',1)
        bg_intrinsic = intrinsic.clone()
        bg_intrinsic[0,0] = bg_intrinsic[1,1] = bg_focal
        if is_ortho and anchor is not None:
            ac=(anchor-eye)@axes.T
            radius=math.sqrt(sum((camera['position'][k]-camera['target'][k])**2 for k in 'xyz'))
            dx,dy=anchor_shift(float(ac[0]),float(ac[1]),float(ac[2]),radius,focal)
            intrinsic[0,2]+=dx;intrinsic[1,2]+=dy

        if environment is not None:
            background=torch.from_numpy(environment.render(eye.cpu().numpy(),axes.cpu().numpy(),camera,width,height)).cuda()[None]
        else:
            mask = ~subject
            background, _, _ = rasterization(values['means'][mask], values['quats'][mask], values['scales'][mask],
                values['opacities'][mask].reshape(-1), rgb[mask], view[None], bg_intrinsic[None], width, height,
                near_plane=.0001, far_plane=10000., packed=False, camera_model='pinhole',
                backgrounds=torch.tensor([[16/255, 18/255, 23/255]], device='cuda'))
        mask = subject
        image, alpha, _ = rasterization(values['means'][mask], values['quats'][mask], values['scales'][mask],
            values['opacities'][mask].reshape(-1), rgb[mask], view[None], intrinsic[None], width, height,
            near_plane=.0001, far_plane=10000., packed=False,
            camera_model='ortho' if is_ortho else 'pinhole')
        image = image + (1-alpha)*background
        pixels = image[0].detach().cpu().numpy().clip(0, 1)
        if not np.isfinite(pixels).all():raise ValueError('3D描画に非有限の画素があります。')
        if 'png' in job:
            Image.fromarray((pixels*255).astype(np.uint8)).save(job['png'],compress_level=3)
        else:
            np.save(job['result'],pixels)
        if len(jobs)>1 and (index+1)%12==0:print(json.dumps({'completed':index+1,'total':len(jobs)}),flush=True)
    print(json.dumps({'renderer': 'gsplat', 'seconds': time.monotonic()-started,
                      'mean_coverage': float(alpha.mean())}), flush=True)


if __name__ == '__main__':
    main()
