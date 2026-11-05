import numpy as np
"""Combine one rigid subject with one reconstructed scene, without view-dependent warping."""
import math
import torch
from comfy_extras.nodes_gaussian_splat import _camera_basis, _mat_to_quat, _quat_mul, _lookat_camera_info


def proportion_camera(camera, width, height):
    """Preserve a target-plane composition without near/far body-size distortion."""
    radius = math.sqrt(sum((float(camera['position'][k])-float(camera['target'][k]))**2 for k in 'xyz'))
    fov, zoom = float(camera['fov']), float(camera.get('zoom',1))
    if not all(math.isfinite(v) for v in [radius,fov,zoom,width,height]) or radius < 1e-6 or not 1 <= fov <= 175 or min(zoom,width,height) <= 0:
        raise ValueError('固定3Dのカメラ位置・画角・寸法が不正です。')
    if camera.get('cameraType') == 'orthographic':
        raster_camera(camera,width,height)  # validate the saved viewing span
        return dict(camera)
    half = radius * math.tan(math.radians(fov)/2)
    return dict(camera,cameraType='orthographic',zoom=zoom,
                frustum=dict(top=half,bottom=-half,left=-half*width/height,right=half*width/height,near=.0001,far=10000.))


def rigid_camera_grid(camera, width=1, height=1, minimum_elevation=-30):
    """Three orbit rings share one scene, target, radius and focal length."""
    eye = [float(camera['position'][k])-float(camera['target'][k]) for k in 'xyz']
    radius = math.sqrt(sum(v*v for v in eye))
    fov = camera['fov']
    if not all(math.isfinite(v) for v in eye+[fov]) or radius < 1e-6 or not 1 <= fov <= 175:
        raise ValueError('固定3Dのカメラ位置・画角が不正です。')
    azimuth = math.atan2(eye[0], eye[2])
    # Render the complete 50% field; the existing viewer/export crop implements 100%.
    wide_fov = math.degrees(2*math.atan(math.tan(math.radians(fov)/2)*2))
    target = [float(camera['target'][k]) for k in 'xyz']
    parallel = proportion_camera(camera,width,height)
    wide_frustum = {k:v*2 if k in ('top','bottom','left','right') else v for k,v in parallel['frustum'].items()}
    views = []
    if type(minimum_elevation) is not int or not -30 <= minimum_elevation < 0:
        raise ValueError('固定3Dの最低カメラ高さが不正です。')
    for row, elevation in enumerate([minimum_elevation, 0, 30]):
        el = math.radians(elevation)
        for index in range(73):
            yaw = azimuth + 2*math.pi*(index % 72)/72
            position = [radius*math.sin(yaw)*math.cos(el), radius*math.sin(el), radius*math.cos(yaw)*math.cos(el)]
            position = [a+b for a,b in zip(position,target)]
            view = _lookat_camera_info(position,target,wide_fov,'cpu')
            view.update(cameraType='orthographic',zoom=parallel['zoom'],frustum=dict(wide_frustum))
            views.append({'row':row,'index':index,'elevation':elevation,'azimuth':index*5,'camera':view})
    return views


def native_proportion_camera(camera, width, height):
    """ComfyUI 1.57 CameraManager resets ortho height to 10 on resize.

    Encode the intended scale in zoom, which setCameraState actually restores.
    This keeps the native preview and saved orthographic PNG consistent.
    """
    camera = proportion_camera(camera,width,height)
    span = camera['frustum']['top']-camera['frustum']['bottom']
    return dict(camera,zoom=camera['zoom']*10/span,
                frustum=dict(top=5.,bottom=-5.,left=-5.*width/height,right=5.*width/height,near=.0001,far=10000.))


def viewer_camera(manifest):
    """Native viewer FOV is vertical; initial saved scenes used the shorter axis."""
    camera = dict(manifest["camera"])
    width, height = manifest["width"], manifest["height"]
    if manifest.get("camera_fov_axis") != "vertical":
        camera["fov"] = math.degrees(2 * math.atan(
            height / min(width, height) * math.tan(math.radians(camera["fov"]) / 2)))
    return camera


def fit_subject_camera(values, camera, components, width, height):
    """Keep the initial whole subject visible, including inferred hair/ears/feet."""
    if not components or camera.get("h3_subject_fitted"):
        return camera
    start, count = components['background'], components['subject']
    points = values['means'][start:start+count]
    opacity = values['opacities'][start:start+count].reshape(-1)
    points = points[opacity > .2]
    if len(points) < 100:
        return camera
    eye, _, right, down, forward = _camera_basis(camera, 'cpu')
    local = (points-eye) @ torch.stack([right, down, forward]).T
    local = local[local[:, 2] > 1e-5]
    if len(local) < 100:
        return camera
    tangent = (local[:, :2] / local[:, 2:]).abs()
    extent = torch.quantile(tangent, .999, dim=0)
    required = max(float(extent[1]), float(extent[0]) * height/width) * 1.06
    return dict(camera, fov=max(camera['fov'], min(170., math.degrees(2*math.atan(required)))))


def raster_camera(camera, width, height):
    """Match the viewer projection to ComfyUI's short-axis Gaussian rasterizer."""
    result = dict(camera)
    if camera.get("cameraType") == "orthographic":
        frustum = camera.get("frustum", {})
        top, bottom = frustum.get("top"), frustum.get("bottom")
        if (type(top) not in (float, int) or type(bottom) not in (float, int)
                or not math.isfinite(top) or not math.isfinite(bottom) or top <= bottom):
            raise ValueError("平行投影カメラの表示範囲が不正です。")
        distance = math.sqrt(sum((camera["position"][k] - camera["target"][k]) ** 2 for k in "xyz"))
        tangent = min(width, height) * (top - bottom) / (2 * height * max(distance, 1e-6))
    else:
        tangent = min(width, height) / height * math.tan(math.radians(camera["fov"]) / 2)
    result["fov"] = math.degrees(2 * math.atan(tangent))
    return result


def rotation_between(a, b):
    a, b = a / a.norm(), b / b.norm()
    v = torch.linalg.cross(a, b)
    c = a.dot(b)
    if c > 1 - 1e-6:
        return torch.eye(3)
    if c < -1 + 1e-6:
        axis = torch.tensor([1., 0., 0.]) if abs(float(a[0])) < .9 else torch.tensor([0., 0., 1.])
        axis = torch.linalg.cross(a, axis)
        axis /= axis.norm()
        return 2 * axis[:, None] @ axis[None] - torch.eye(3)
    x, y, z = v.tolist()
    cross = torch.tensor([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
    return torch.eye(3) + cross + cross @ cross / (1 + c)


def apply_viewer_transform(values, transform):
    """Apply the native viewer's model placement in its Y-up coordinate system."""
    from comfy_extras.nodes_gaussian_splat import _quat_to_mat
    if not transform:
        return values
    if not isinstance(transform, dict):
        raise ValueError("3Dプレビューの配置情報が不正です。")
    for field, axes in [("position", "xyz"), ("scale", "xyz"), ("quaternion", "xyzw")]:
        item = transform.get(field)
        if not isinstance(item, dict) or any(type(item.get(k)) not in (int, float)
                or not math.isfinite(item[k]) or abs(item[k]) > 1e5 for k in axes):
            raise ValueError("3Dプレビューの配置情報が不正です。")
    q = transform["quaternion"]
    if abs(sum(q[k] ** 2 for k in "xyzw") - 1) > 1e-3:
        raise ValueError("3Dプレビューの回転が不正です。")
    tensor = values["means"].new_tensor
    scale = tensor([transform["scale"][k] for k in "xyz"])
    if (scale <= 0).any():
        raise ValueError("3Dプレビューの拡大率は正の値にしてください。")
    flip = torch.diag(tensor([1., -1., -1.]))
    rotation = _quat_to_mat(tensor([[q[k] for k in "wxyz"]]))[0]
    rotation = flip @ rotation @ flip
    affine = rotation @ torch.diag(scale)
    translation = flip @ tensor([transform["position"][k] for k in "xyz"])
    result = dict(values, means=values["means"] @ affine.T + translation)
    if torch.allclose(scale, scale[:1].expand_as(scale), rtol=1e-6, atol=1e-8):
        result["scales"] = values["scales"] * scale[0]
        result["quats"] = _quat_mul(_mat_to_quat(rotation[None]).expand_as(values["quats"]), values["quats"])
    else:
        axes = _quat_to_mat(values["quats"])
        covariance = (axes * values["scales"].square()[:, None, :]) @ axes.transpose(-1, -2)
        covariance = affine @ covariance @ affine.T
        eigenvalues, basis = torch.linalg.eigh(covariance)
        basis *= torch.where(torch.linalg.det(basis) < 0, -1., 1.)[:, None, None]
        result["scales"] = eigenvalues.clamp_min(0).sqrt()
        result["quats"] = _mat_to_quat(basis)
    return result


def assemble_scene(prediction, masks, subject, subject_camera, model_size, images=None, texture_images=None):
    obj = {"means": subject.positions[0].detach().float().cpu(),
           "scales": subject.scales[0].detach().float().cpu(),
           "quats": subject.rotations[0].detach().float().cpu(),
           "opacities": subject.opacities[0].detach().float().cpu().reshape(-1),
           "sh": subject.sh[0].detach().float().cpu()[:, :1]}
    if subject.counts is not None:
        obj = {k: v[:int(subject.counts[0])] for k, v in obj.items()}
    active = obj["opacities"] > .1
    if int(active.sum()) < 100:
        raise ValueError("固定する人物の3Dデータが空です。前景マスクを確認してください。")
    _, _, right, down, forward = _camera_basis(subject_camera, "cpu")
    axes = torch.stack([right, down, forward])
    local = obj["means"] @ axes.T
    low, high = torch.quantile(local[active], torch.tensor([.002, .998]), dim=0)
    if high[1] - low[1] < 1e-5:
        raise ValueError("人物の3Dデータに高さがありません。")
    ys, xs = torch.where(masks[0] > .5)
    if xs.numel() < 50:
        raise ValueError("周回画像から人物を分離できませんでした。")
    xmin, xmax, ymin, ymax = [float(x) for x in (xs.min(), xs.max(), ys.min(), ys.max())]
    cameras, intrinsics = prediction["camera_poses"][0], prediction["camera_intrs"][0]
    camera, intrinsic = cameras[0], intrinsics[0]
    depth = prediction["depth"][0, 0, ..., 0][masks[0] > .5].median()
    scale = (ymax - ymin) * depth / (intrinsic[1, 1] * (high[1] - low[1]))
    if not torch.isfinite(scale) or scale <= 0:
        raise ValueError("背景と人物の奥行きを対応付けられませんでした。")
    uv = torch.tensor([(xmin + xmax) / 2, (ymin + ymax) / 2, 1.])
    center = camera[:3, :3] @ (torch.linalg.inv(intrinsic) @ uv * depth) + camera[:3, 3]
    rotation = camera[:3, :3] @ axes
    obj["means"] = (local - (low + high) / 2) @ camera[:3, :3].T * scale + center
    obj["scales"] *= scale
    obj["quats"] = _quat_mul(_mat_to_quat(rotation[None]).expand_as(obj["quats"]), obj["quats"])
    background = prediction["splats"]
    # The fitted camera ring supplies world up. This prevents orbiting about the input image's tilted Y axis.
    positions = cameras[:, :3, 3]
    _, singular, vh = torch.linalg.svd(positions - positions.mean(0), full_matrices=False)
    up = vh[-1]
    if up[1] > 0:
        up = -up
    if singular[1] < 1e-4:
        raise ValueError("周回のカメラ位置を推定できませんでした。異なる角度の周回画像が必要です。")
    upright = rotation_between(up, torch.tensor([0., -1., 0.]))
    upright_quat = _mat_to_quat(upright[None])
    merged = {k: torch.cat([background[k], obj[k]], dim=0) for k in obj}
    merged["means"] = (merged["means"] - center) @ upright.T
    merged["quats"] = _quat_mul(upright_quat.expand_as(merged["quats"]), merged["quats"])
    keep = merged["opacities"] > .05
    for value in merged.values():
        keep &= torch.isfinite(value).reshape(len(keep), -1).all(-1)
    clearance = None
    if images is not None:
        from .scene_environment import clear_orbit_space
        n = len(background['means'])
        foreground = merged['means'][n:][keep[n:]]
        background_keep, clearance = clear_orbit_space(
            {k: v[:n] for k, v in merged.items()}, foreground, (camera[:3, 3]-center).norm())
        keep[:n] &= background_keep
    merged = {k: v[keep].contiguous() for k, v in merged.items()}
    components = {'background': int(keep[:len(background['means'])].sum()),
                  'subject': int(keep[len(background['means']):].sum())}
    if len(merged["means"]) < 1000:
        raise ValueError("再構成した3D点が不足しています。")
    eye_splat = upright @ (camera[:3, 3] - center)
    environment = None
    ground_info = None
    if images is not None:
        from .scene_surfaces import textured_environment, contact_ground, contact_patch
        texture_images = images.numpy() if texture_images is None else np.asarray(texture_images)
        tex_h,tex_w=texture_images.shape[1:3]
        texture_masks=torch.nn.functional.interpolate(masks[:,None],size=(tex_h,tex_w),mode='nearest')[:,0].numpy()
        texture_k=intrinsics.numpy().copy()
        texture_k[:,0,:]*=tex_w/model_size[0];texture_k[:,1,:]*=tex_h/model_size[1]
        background_points=merged['means'][:components['background']]
        body_height=float(merged['means'][components['background']:,1].max()-merged['means'][components['background']:,1].min())
        wall=background_points[background_points[:,1].abs()<body_height*2]
        measured=float(wall[:,[0,2]].norm(dim=-1).median()) if len(wall)>100 else 0.
        radius=max(measured,float((positions-center).norm(dim=-1).max())*1.5)
        source_args=(texture_images,texture_masks,cameras.numpy(),texture_k,center.numpy(),upright.numpy())
        shell,environment=textured_environment(*source_args,radius)
        n=components['background'];body={k:v[n:n+components['subject']] for k,v in merged.items()}
        active=body['opacities']>.1
        ground,ground_info=contact_ground(body['means'][active].numpy(),*source_args,float((camera[:3,3]-center).norm()))
        patch=contact_patch(body['means'][active].numpy(),ground,ground_info['floor'])
        merged={k:torch.cat([body[k],shell[k],ground[k],patch[k]],0).contiguous() for k in body}
        components={'background':0,'subject':len(body['means']),'environment':len(shell['means']),'ground':len(ground['means']),'contact_patch':len(patch['means'])}
        clearance['floor']=ground_info['floor']
    width, height = model_size
    focal = intrinsic[1, 1]
    fov = math.degrees(2 * math.atan(height / (2 * float(focal))))
    camera_info = _lookat_camera_info(eye_splat * torch.tensor([1., -1., -1.]), [0, 0, 0], fov, "cpu")
    metadata = {"subject_scale": float(scale), "subject_center_before_alignment": center.tolist(),
                "world_up_before_alignment": up.tolist(), "upright_rotation": upright.tolist(),
                "point_count": len(merged["means"]), "pose_status": "fixed_geometry_estimated_from_images",
                "environment": environment, "ground": ground_info, "components": components,
                "orbit_clearance": clearance,
                "background_visibility": prediction.get('background_visibility')}
    return merged, camera_info, metadata
