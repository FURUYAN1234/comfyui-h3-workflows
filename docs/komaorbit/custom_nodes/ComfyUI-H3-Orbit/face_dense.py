"""High-density source face texture baked into the same portable Gaussian scene.

The face is a fixed curved surface, never a screen-facing overlay. All samples
outside the matched facial surface are retained; the native viewer, PNG renderer,
and PLY export therefore consume exactly the same data.
"""
import numpy as np
import cv2
from scipy.spatial.transform import Rotation
from scipy.spatial import cKDTree

SH_C0 = .28209479177387814


def quadratic_surface(depth, mask, focal):
    y, x = np.nonzero((mask > .7) & np.isfinite(depth) & (depth > 0))
    if len(x) < 100:
        raise ValueError('顔の表面深度が不足しています。')
    origin = np.array([np.median(x), np.median(y)])
    extent = float(max(np.ptp(x), np.ptp(y)))
    def features(x, y):
        px, py = (x-origin[0])/extent, (y-origin[1])/extent
        return np.column_stack([np.ones(len(x)), px, py, px*px, px*py, py*py])
    a = features(x, y); z = depth[y, x]; weights = np.ones(len(z))
    for _ in range(5):
        coef = np.linalg.lstsq(a*weights[:, None], z*weights, rcond=None)[0]
        residual = z-a@coef
        sigma = max(1e-7, 1.4826*np.median(abs(residual-np.median(residual))))
        weights = np.minimum(1, 2.5*sigma/np.maximum(abs(residual), 1e-8))
    # A turned face can have more depth than its projected width. Judge the
    # fit residual here; validate new samples against the original 3D points
    # below, rather than rejecting legitimate foreshortening.
    if not np.isfinite(coef).all() or np.median(abs(z-a@coef)) > extent/focal*.2:
        raise ValueError('顔の表面深度を安定して近似できません。')
    return origin, extent, coef


def build_dense_faces(values, selected, view, intrinsic, depth, regions, target, model_name):
    import torch
    from comfy_extras.nodes_upscale_model import UpscaleModelLoader
    from upscale_compat import upscale_image
    arrays = {k: v.detach().cpu().numpy() for k, v in values.items()}
    selected = np.asarray(selected, bool)
    focal = float(intrinsic[0, 0]); axes = np.asarray(view[:3, :3]); shift = np.asarray(view[:3, 3])
    center = np.asarray(intrinsic[:2, 2])
    model = UpscaleModelLoader().load_model(model_name)[0]
    parts = []; pixels = []; region_ids = []; coefficients = []; origins = []; extents = []
    assigned = np.zeros(depth.shape, bool)
    try:
        for index, mask in enumerate(regions):
            if np.any(assigned & (mask > .1)):
                raise ValueError('複数の顔表面が重なっています。')
            assigned |= mask > .1
            origin, extent, coef = quadratic_surface(depth, mask, focal)
            y, x = np.nonzero(mask > .03)
            lo = np.maximum([x.min()-8, y.min()-8], 0)
            hi = np.minimum([x.max()+9, y.max()+9], target.shape[1::-1])
            crop = torch.tensor(target[lo[1]:hi[1], lo[0]:hi[0]])[None]
            with torch.no_grad():
                high = upscale_image(model, crop)[0].cpu().numpy()
            factor = np.array(high.shape[1::-1])/(hi-lo)
            # Bound memory for close-up and multi-person inputs.
            spacing = max(.5, np.sqrt(len(x)/100000))
            yy, xx = np.mgrid[lo[1]:hi[1]:spacing, lo[0]:hi[0]:spacing]
            weights = cv2.remap(mask, xx.astype(np.float32), yy.astype(np.float32), cv2.INTER_LINEAR)
            keep = weights > .03
            color = cv2.remap(high, ((xx-lo[0])*factor[0]).astype(np.float32),
                              ((yy-lo[1])*factor[1]).astype(np.float32), cv2.INTER_CUBIC)[keep].clip(0, 1)
            x, y = xx[keep], yy[keep]; px, py = (x-origin[0])/extent, (y-origin[1])/extent
            a = np.column_stack([np.ones(len(x)), px, py, px*px, px*py, py*py])
            z = a@coef-.00015
            local = np.column_stack([(x-center[0])/focal, (y-center[1])/focal, z])
            means = (local-shift)@axes
            dx = (coef[1]+2*coef[3]*px+coef[4]*py)/extent*focal
            dy = (coef[2]+coef[4]*px+2*coef[5]*py)/extent*focal
            u = np.column_stack([np.ones(len(x)), np.zeros(len(x)), dx])
            normal = np.column_stack([-dx, -dy, np.ones(len(x))])
            u /= np.linalg.norm(u, axis=1)[:, None]; normal /= np.linalg.norm(normal, axis=1)[:, None]
            v = np.cross(normal, u)
            quats = Rotation.from_matrix(np.stack([u@axes, v@axes, normal@axes], axis=2)).as_quat()[:, [3, 0, 1, 2]]
            part = {k: np.zeros((len(x), *v.shape[1:]), dtype=v.dtype) for k, v in arrays.items()}
            part['means'][:] = means; part['quats'][:] = quats
            part['scales'][:] = [spacing*.6/focal, spacing*.6/focal, spacing*.06/focal]
            part['opacities'][:] = (weights[keep]*.99).reshape(part['opacities'].shape)
            part['sh'][:, 0] = (color-.5)/SH_C0
            parts.append(part); pixels.append(np.column_stack([x, y])); region_ids.append(np.full(len(x), index))
            coefficients.append(coef); origins.append(origin); extents.append(extent)
    finally:
        model.cpu()
    kept = np.flatnonzero(~selected)
    result = {k: np.concatenate([arrays[k][kept], *[p[k] for p in parts]]) for k in arrays}
    provenance = dict(kind=np.array('dense_source_face'), kept=kept, selected=selected,
                      pixels=np.concatenate(pixels), regions=np.concatenate(region_ids),
                      coefficients=np.array(coefficients), origins=np.array(origins), extents=np.array(extents),
                      view=np.asarray(view), intrinsic=np.asarray(intrinsic), masks=np.array(regions))
    validate_dense_faces(arrays, result, provenance)
    return {k: torch.from_numpy(v) for k, v in result.items()}, provenance


def validate_dense_faces(before, after, provenance, allow_sr_colors=False):
    b, a = [{k: np.asarray(v) for k, v in d.items()} for d in (before, after)]
    selected = np.asarray(provenance['selected']); kept = np.asarray(provenance['kept'])
    uv = np.asarray(provenance['pixels']); rid = np.asarray(provenance['regions'])
    if selected.shape != (len(b['means']),) or not np.array_equal(kept, np.flatnonzero(~selected)):
        raise ValueError('顔以外の保持対応が不正です。')
    count = len(kept); added = len(uv)
    if not 100 <= added <= 1600000 or not selected.any() or selected.sum() > min(len(selected)*.4, 40000):
        raise ValueError('高精細な顔表面の範囲が不正です。')
    for key in b:
        if a[key].shape != (count+added, *b[key].shape[1:]) or not np.isfinite(a[key]).all():
            raise ValueError('高精細な顔表面の数値が不正です。')
        if key == 'sh' and allow_sr_colors:
            old = np.clip(b[key][kept, 0]*SH_C0+.5, 0, 1); new = a[key][:count, 0]*SH_C0+.5
            if np.max(abs(old-new)) > .1501 or not np.array_equal(b[key][kept, 1:], a[key][:count, 1:]):
                raise ValueError('顔以外の色の変更が許容範囲を超えました。')
        elif not np.array_equal(b[key][kept], a[key][:count]):
            raise ValueError('顔以外の3D表面が変更されました。')
    view, intrinsic = provenance['view'], provenance['intrinsic']
    local = a['means'][count:]@view[:3, :3].T+view[:3, 3]
    focal = float(intrinsic[0, 0]); projected = local[:, :2]*focal+intrinsic[:2, 2]
    if not np.allclose(projected, uv, atol=.01):
        raise ValueError('顔テクスチャの位置がずれています。')
    masks = provenance['masks']
    if rid.shape != (added,) or np.any(rid < 0) or np.any(rid >= len(masks)):
        raise ValueError('顔の対応領域が不正です。')
    for i, mask in enumerate(masks):
        ids = rid == i; xy = uv[ids]; ix = np.rint(xy).astype(int)
        if (ix < 0).any() or (ix[:, 0] >= mask.shape[1]).any() or (ix[:, 1] >= mask.shape[0]).any():
            raise ValueError('顔テクスチャが画像の外にあります。')
        sampled = np.concatenate([cv2.remap(mask.astype(np.float32), part[:, 0].astype(np.float32)[None],
            part[:, 1].astype(np.float32)[None], cv2.INTER_LINEAR)[0] for part in np.array_split(xy, max(1, (len(xy)+19999)//20000))])
        if np.any(sampled <= .03):
            raise ValueError('元絵の顔の外に表面が作られました。')
        extent = float(provenance['extents'][i]); px, py = ((xy-provenance['origins'][i])/extent).T
        expected = np.column_stack([np.ones(len(xy)), px, py, px*px, px*py, py*py])@provenance['coefficients'][i]-.00015
        if not np.allclose(local[ids, 2], expected, atol=2e-6):
            raise ValueError('顔の深度が復元した表面と一致しません。')
    distance = cKDTree(b['means'][selected]).query(a['means'][count:], workers=1)[0]
    span = np.ptp(b['means'][selected]@view[:2, :3].T, axis=0).max()
    if distance.max() > span*.5:
        raise ValueError('元の顔から離れた場所に表面が作られました。')
    if (a['scales'][count:] <= 0).any() or a['scales'][count:].max() > 2/focal:
        raise ValueError('顔の描画点が大きすぎます。')
    if not np.allclose(np.linalg.norm(a['quats'][count:], axis=1), 1, atol=1e-5):
        raise ValueError('顔表面の向きが不正です。')
    rgb = a['sh'][count:, 0]*SH_C0+.5
    if rgb.min() < -1e-5 or rgb.max() > 1.00001 or np.any(a['sh'][count:, 1:]):
        raise ValueError('顔テクスチャの色が不正です。')
    if a['opacities'][count:].min() < 0 or a['opacities'][count:].max() > 1:
        raise ValueError('顔表面の透明度が不正です。')
