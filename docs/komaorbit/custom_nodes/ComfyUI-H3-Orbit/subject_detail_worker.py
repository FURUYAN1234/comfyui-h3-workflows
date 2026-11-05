"""Isolated, bounded color refinement from multiple super-resolved views."""
import importlib.util
import json
from pathlib import Path
import sys
import time


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    pack = Path(__file__).parent
    sys.path[:0] = [str(pack/'vendor/worldmirror/deps'), str(pack.parents[1]), str(pack)]
    sys.argv = [sys.argv[0]]
    import numpy as np
    import torch
    import torch.nn.functional as F
    from safetensors.torch import load_file, save_file
    from comfy_extras.nodes_gaussian_splat import _camera_basis
    from comfy_extras.nodes_upscale_model import UpscaleModelLoader
    from upscale_compat import upscale_image
    from subject_detail import detail_cameras, validate_detail_result, SH_C0
    from runtime import load_renderer
    load_renderer(pack)
    from gsplat.rendering import rasterization

    started = time.monotonic()
    before = load_file(request['scene'])
    metadata = json.loads(Path(request['scene']).with_name('manifest.json').read_text())
    _, _, right, down, forward = _camera_basis(metadata['camera'], 'cpu')
    basis = torch.stack([right, down, forward]).numpy()
    cameras, intrinsic = detail_cameras(before['means'].numpy(), before['opacities'].numpy(), basis)
    values = {k: v.cuda() for k, v in before.items()}
    base = (values['sh'][:, 0]*SH_C0+.5).clamp(0, 1).detach()
    cameras = torch.from_numpy(cameras).cuda()
    intrinsic = torch.from_numpy(intrinsic).cuda()

    def render(colors, camera, size):
        k = intrinsic.clone()
        k[:2] *= size/1024
        return rasterization(
            values['means'], values['quats'], values['scales'], values['opacities'].reshape(-1),
            colors, camera[None], k[None], size, size, near_plane=.0001, far_plane=10000.,
            packed=False, camera_model='ortho')[0]

    model = UpscaleModelLoader().load_model(request['model'])[0]
    targets = []
    with torch.no_grad():
        for i, camera in enumerate(cameras):
            image = render(base, camera, 1024)
            high = upscale_image(model, image.cpu())
            high = F.interpolate(high.movedim(-1, 1), size=(2048, 2048), mode='bicubic',
                                 align_corners=False).movedim(1, -1).clamp(0, 1)
            targets.append(high.cuda())
            print(json.dumps({'stage': 'superresolution', 'view': i+1, 'total': len(cameras)}), flush=True)
    model.cpu()
    del model, high, image
    torch.cuda.empty_cache()
    with torch.no_grad():
        loss_before = np.mean([float((render(base, c, 2048)-t).square().mean())
                               for c, t in zip(cameras, targets)])
    colors = torch.nn.Parameter(base.clone())
    optimizer = torch.optim.Adam([colors], lr=.025)
    for step in range(400):
        i = step % len(cameras)
        optimizer.zero_grad(set_to_none=True)
        predicted = render(colors, cameras[i], 2048)
        loss = (predicted-targets[i]).square().mean() + .001*(colors-base).square().mean()
        if not torch.isfinite(loss):
            raise ValueError('3D高精細化の学習値が不正です。')
        loss.backward()
        optimizer.step()
        with torch.no_grad():
            colors.copy_(torch.maximum(torch.minimum(colors, base+.15), base-.15).clamp(0, 1))
    with torch.no_grad():
        loss_after = np.mean([float((render(colors, c, 2048)-t).square().mean())
                              for c, t in zip(cameras, targets)])
    if loss_after >= loss_before:
        raise ValueError('超解像結果への一致度が改善しませんでした。元の3Dを維持してください。')
    result = {k: v.clone() for k, v in before.items()}
    result['sh'][:, 0] = (colors.detach().cpu()-.5)/SH_C0
    validate_detail_result(before, result)
    max_change = float((colors.detach()-base).abs().max())
    mean_change = float((colors.detach()-base).abs().mean())
    face_stats = {'status': 'reference_not_connected', 'restored_faces': 0}
    if request.get('reference_image'):
        from face_restore import restore_faces
        # Release the SR views/model before face detection and surface subdivision.
        del targets, colors, optimizer, predicted, values
        torch.cuda.empty_cache()
        result, face_stats = restore_faces(result, metadata, request)
    destination = Path(request['result'])
    temporary = destination.with_suffix('.tmp.safetensors')
    save_file(result, str(temporary))
    temporary.replace(destination)
    stats = {'views': len(cameras), 'render_resolution': 1024, 'fit_resolution': 2048,
             'iterations': 400, 'max_color_change': max_change,
             'mean_color_change': mean_change,
             'fit_loss_before': float(loss_before), 'fit_loss_after': float(loss_after),
             'worker_seconds': time.monotonic()-started,
             'source_projection': face_stats['status'] == 'restored', 'face_restore': face_stats}
    Path(request['stats']).write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats), flush=True)


if __name__ == '__main__':
    main()
