"""Fixed distant environment from foreground-masked source image rays."""
import math
import numpy as np
import torch
from scipy.ndimage import maximum_filter
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation


def clear_orbit_space(background, subject_points, eye_distance):
    """Keep inferred background surfaces outside the subject's camera space.

    Source-view silhouettes alone cannot reject translucent slabs encountered
    when the user raises the camera. In upright, Y-down coordinates, clear a
    cylinder above the subject's feet. Floor and distant surfaces remain; the
    fixed environment supplies any newly exposed gaps.
    """
    radius = float(eye_distance) * 1.1
    floor = torch.quantile(subject_points[:, 1], .995)
    points = background['means']
    extent = background['scales'].amax(-1) * 2.5
    intersects = ((points[:, [0, 2]].norm(dim=-1)-extent < radius)
                  & (points[:, 1]-extent < floor))
    return ~intersects, {'removed': int(intersects.sum()), 'retained': int((~intersects).sum()),
                         'radius': radius, 'floor': float(floor)}


def prune_background_occluders(splats, masks, cameras, intrinsics, depth):
    """Remove background Gaussians that contradict a known foreground silhouette.

    Masking source pixels alone is insufficient: merged background splats can
    project in front of the subject from a different view, creating a fog layer.
    Large splats use conservative projected bounds; small ones use the mask.
    """
    means, scales = splats['means'], splats['scales']
    keep = torch.ones(len(means), dtype=torch.bool, device=means.device)
    masks = torch.nn.functional.max_pool2d(masks[:, None].float(), 9, 1, 4)[:, 0] > .1
    extent = scales.amax(-1) * 2.5
    h, w = masks.shape[-2:]
    for mask, pose, k, distance in zip(masks, cameras, intrinsics, depth):
        ys, xs = torch.where(mask)
        if len(xs) < 50:
            continue
        d = distance[..., 0] if distance.ndim == 3 else distance
        foreground_depth = d[mask].median()
        camera = (means-pose[:3, 3]) @ pose[:3, :3]
        z = camera[:, 2]
        uv = camera[:, :2] / z.clamp_min(1e-6)[:, None]
        uv = uv * k.diag()[:2] + k[:2, 2]
        px = uv[:, 0].round().long().clamp(0, w-1)
        py = uv[:, 1].round().long().clamp(0, h-1)
        inside = (uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h)
        overlap = inside & mask[py, px]
        radius = extent * k.diag()[:2].max() / z.clamp_min(1e-6)
        dx = torch.maximum(xs.min()-uv[:, 0], uv[:, 0]-xs.max()).clamp_min(0)
        dy = torch.maximum(ys.min()-uv[:, 1], uv[:, 1]-ys.max()).clamp_min(0)
        overlap |= (radius > 8) & (dx.square()+dy.square() < radius.square())
        keep &= ~((z > 1e-5) & (z-extent < foreground_depth*1.02) & overlap)
    return {k:v[keep] for k,v in splats.items()}, {'removed':int((~keep).sum()), 'retained':int(keep.sum())}


def projected_shell(images, masks, cameras, intrinsics, center, upright, radius, count=65536):
    images, masks = np.asarray(images), np.asarray(masks)
    cameras, intrinsics = np.asarray(cameras), np.asarray(intrinsics)
    center, upright = np.asarray(center), np.asarray(upright)
    if (images.ndim != 4 or images.shape[-1] != 3 or masks.shape != images.shape[:3]
            or cameras.shape != (len(images), 4, 4) or intrinsics.shape != (len(images), 3, 3)
            or center.shape != (3,) or upright.shape != (3, 3)
            or count < 100 or not math.isfinite(radius) or radius <= 0):
        raise ValueError('背景画像・マスク・カメラの対応が不正です。')
    if not all(np.isfinite(a).all() for a in (images, masks, cameras, intrinsics, center, upright)):
        raise ValueError('背景の画像・位置情報に非有限の値があります。')
    _, h, w, _ = images.shape
    y, x = np.mgrid[0:h:2, 0:w:2]
    uv = np.stack([x+.5, y+.5, np.ones_like(x)], -1).reshape(-1, 3)
    dirs, colors, qualities, ambient, ground = [], [], [], [], []
    for im, mask, pose, intrinsic in zip(images, masks, cameras, intrinsics):
        valid = (maximum_filter(mask, size=11)[::2, ::2] < .1).reshape(-1)
        ray = uv @ np.linalg.inv(intrinsic).T @ pose[:3, :3].T @ upright.T
        ray /= np.linalg.norm(ray, axis=1, keepdims=True)
        eye = (pose[:3, 3] - center) @ upright.T
        if np.linalg.norm(eye) >= radius:
            raise ValueError('背景を補完する範囲の外にカメラがあります。')
        along = ray @ eye
        t = -along + np.sqrt(np.maximum(along**2 + radius**2 - eye @ eye, 0))
        direction = (eye + t[:, None]*ray) / radius
        rgb = im[::2, ::2].reshape(-1, 3)
        edge = ((uv[:, 0]-w/2)/(w/2))**2 + ((uv[:, 1]-h/2)/(h/2))**2
        quality = np.maximum(.1, 1-edge/2)
        dirs.append(direction[valid]); colors.append(rgb[valid]); qualities.append(quality[valid])
        upper = valid & (uv[:, 1] < h*.12)
        if upper.any(): ambient.append(rgb[upper])
        lower = valid & (uv[:, 1] > h*.88)
        if lower.any(): ground.append(rgb[lower])
    dirs, colors, qualities = np.concatenate(dirs), np.concatenate(colors), np.concatenate(qualities)
    if len(dirs) < 100: raise ValueError('背景を投影できる画像領域が不足しています。')
    ambient = np.median(np.concatenate(ambient) if ambient else colors, axis=0)
    ground = np.median(np.concatenate(ground) if ground else colors, axis=0)
    i = np.arange(count, dtype=np.float64)
    ny = 1-2*(i+.5)/count; theta=i*(math.pi*(3-math.sqrt(5))); r=np.sqrt(1-ny*ny)
    normal=np.stack([np.cos(theta)*r, ny, np.sin(theta)*r], 1).astype(np.float32)
    distance, near = cKDTree(dirs).query(normal, k=6, workers=2)
    weight = qualities[near]/np.maximum(distance, .008)**3
    weight /= weight.sum(1, keepdims=True)
    texture = (colors[near]*weight[:, :, None]).sum(1)
    # Unseen directions fade smoothly to scene lighting instead of stretching edge pixels.
    observed = np.clip((.12-distance[:, 0])/.08, 0, 1)
    observed = observed*observed*(3-2*observed)
    # The unobserved lower hemisphere belongs to the floor, not the sky.
    # Use only unmasked lower source pixels; preserve measured directions.
    ground_weight = np.clip((normal[:, 1] + .1) / .4, 0, 1)
    ground_weight = ground_weight**2 * (3-2*ground_weight)
    fallback = ambient*(1-ground_weight[:, None]) + ground*ground_weight[:, None]
    texture = texture*observed[:, None] + fallback*(1-observed[:, None])
    tangent = np.cross(np.broadcast_to([0., 1., 0.], normal.shape), normal)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    vertical = np.cross(normal, tangent)
    quat = Rotation.from_matrix(np.stack([tangent, vertical, normal], -1)).as_quat().astype(np.float32)
    quat = np.concatenate([quat[:, 3:], quat[:, :3]], 1)
    spacing=radius*math.sqrt(4*math.pi/count)
    shell={'means':torch.from_numpy(normal*radius),
           'scales':torch.tensor([spacing*.65,spacing*.65,radius*.00005]).repeat(count,1),
           'quats':torch.from_numpy(quat),'opacities':torch.full((count,),.97),
           'sh':torch.from_numpy(((texture.astype(np.float32)-.5)/.28209479177387814)[:,None])}
    return shell, {'method':'fixed_image_projected_environment','radius':float(radius),
                   'point_count':count,'observed_fraction':float((observed>.5).mean()),
                   'ambient_color':ambient.tolist(), 'ground_color':ground.tolist(),
                   'unseen_fill':'masked_upper_lower_hemispheres'}
