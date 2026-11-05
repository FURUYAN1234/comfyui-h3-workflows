"""Recover source facial features on a rigid splat surface, with correspondence gates.

Only observable, confidently paired faces are transferred. Dense source-textured
curved surfaces preserve eye detail; body geometry and background stay untouched.
"""
from pathlib import Path
import json
import sys
import time
import cv2
import numpy as np
from scipy.interpolate import RBFInterpolator
from scipy.optimize import linear_sum_assignment

SH_C0 = .28209479177387814
FACE_METHOD_VERSION = 7
ANGLES = [(0, 0), (-30, 0), (-15, 0), (15, 0), (30, 0), (0, -15), (0, 15)]


def valid_face(face):
    b = np.asarray(face.get('bbox', [])); k = np.asarray(face.get('keypoints', []))
    return (b.shape == (5,) and k.shape == (28, 3) and np.isfinite(b).all()
            and np.isfinite(k).all() and b[4] >= .35 and min(b[2:4]-b[:2]) >= 20
            and k[:, 2].mean() >= .65 and np.count_nonzero(k[:, 2] > .55) >= 22)


def face_center(face):
    b = np.asarray(face['bbox'])
    return (b[:2]+b[2:4])/2


def match_faces(source, target, source_size, target_size, expected=None, max_distance=.28, align_frame=False):
    """Return unambiguous (source, target) pairs; never infer missing identities."""
    si = [i for i, f in enumerate(source) if valid_face(f)]
    ti = [i for i, f in enumerate(target) if valid_face(f)]
    if not si or not ti:
        return []
    sp = np.array([face_center(source[i])/source_size for i in si])
    if expected is not None:
        sp = np.asarray(expected)[si]/target_size
    tp = np.array([face_center(target[i])/target_size for i in ti])
    if align_frame and len(si) == len(ti):
        # Prepared input may be cropped and padded: match in its corresponding frame.
        source_extent = np.median([np.linalg.norm((np.asarray(source[i]['bbox'])[2:4]
                              - np.asarray(source[i]['bbox'])[:2])/source_size) for i in si])
        target_extent = np.median([np.linalg.norm((np.asarray(target[i]['bbox'])[2:4]
                              - np.asarray(target[i]['bbox'])[:2])/target_size) for i in ti])
        sp = (sp-np.median(sp, axis=0))*target_extent/max(source_extent, 1e-8)+np.median(tp, axis=0)
    cost = np.linalg.norm(sp[:, None]-tp[None], axis=-1)
    rows, cols = linear_sum_assignment(cost)
    result = []
    for r, c in zip(rows, cols):
        alternatives = [*np.delete(cost[r], c), *np.delete(cost[:, c], r)]
        if cost[r, c] > max_distance or (alternatives and min(alternatives)-cost[r, c] < .035):
            continue
        result.append((si[r], ti[c]))
    return result


def warp_face(source, base, source_face, destination_face):
    """Landmark-guided source RGB transfer confined to a feathered face hull."""
    sk = np.asarray(source_face['keypoints']); dk = np.asarray(destination_face['keypoints'])
    if not valid_face(source_face) or not valid_face(destination_face):
        raise ValueError('顔の対応点が不正です。')
    keep = np.minimum(sk[:, 2], dk[:, 2]) > .55
    scale = float(np.linalg.norm(np.ptp(dk[:, :2], axis=0)))
    fn = RBFInterpolator(dk[keep, :2]/scale, sk[keep, :2],
                         kernel='thin_plate_spline', smoothing=.0001)
    mask = np.zeros(base.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, cv2.convexHull(np.round(dk[:, :2]).astype(np.int32)), 255)
    alpha = np.clip(cv2.distanceTransform(mask, cv2.DIST_L2, 5)/(scale*.04), 0, 1)
    yy, xx = np.mgrid[:base.shape[0], :base.shape[1]]
    valid = mask > 0
    if np.count_nonzero(valid) < 100:
        raise ValueError('顔の対応領域が小さすぎます。')
    mapped = fn(np.stack([xx[valid], yy[valid]], -1)/scale).astype(np.float32)
    if (not np.isfinite(mapped).all() or (mapped < 0).any()
            or (mapped[:, 0] >= source.shape[1]).any() or (mapped[:, 1] >= source.shape[0]).any()):
        raise ValueError('元絵の範囲外へ顔が対応しています。')
    mx, my = xx.astype(np.float32), yy.astype(np.float32)
    mx[valid], my[valid] = mapped[:, 0], mapped[:, 1]
    # Folded correspondence would double an eye: refuse instead of painting it.
    inner = cv2.erode(mask, np.ones((5, 5), np.uint8)) > 0
    dx_x, dy_x = np.gradient(mx); dx_y, dy_y = np.gradient(my)
    determinant = dy_x*dx_y-dx_x*dy_y
    if inner.any() and np.mean(determinant[inner] <= 0) > .03:
        raise ValueError('顔の対応に折り返しがあるため転写を中止しました。')
    warped = cv2.remap(source, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT)
    sigma = max(4., scale*.08)
    light = np.clip(cv2.GaussianBlur(base, (0, 0), sigma)
                    - cv2.GaussianBlur(warped, (0, 0), sigma), -.1, .1)
    corrected = np.clip(warped+light, 0, 1)
    return base*(1-alpha[:, :, None])+corrected*alpha[:, :, None], alpha


def valid_face_box(face):
    box = np.asarray(face.get('bbox', []))
    return (box.shape == (5,) and np.isfinite(box).all() and box[4] >= .75
            and min(box[2:4]-box[:2]) >= 20)


def detect_render_faces(detect, image):
    """Recover small full-body face candidates without lowering landmark gates."""
    faces = list(detect(image))
    if faces and all(valid_face(f) for f in faces):
        return faces
    height, width = image.shape[:2]
    th, tw = height//2, width//2
    for top in (0, (height-th)//2, height-th):
        for left in (0, (width-tw)//2, width-tw):
            for face in detect(image[top:top+th, left:left+tw]):
                f = {k: np.asarray(v).copy() for k, v in face.items()}
                f['bbox'][[0, 2]] += left; f['bbox'][[1, 3]] += top
                f['keypoints'][:, :2] += [left, top]
                faces.append(f)
    result = []
    for f in sorted(faces, key=lambda f: (valid_face(f), f['bbox'][4]), reverse=True):
        b = np.asarray(f['bbox'])
        duplicate = False
        for other in result:
            a = np.asarray(other['bbox'])
            overlap = np.maximum(0, np.minimum(a[2:4], b[2:4])-np.maximum(a[:2], b[:2])).prod()
            union = (a[2:4]-a[:2]).prod()+(b[2:4]-b[:2]).prod()-overlap
            if overlap/max(union, 1) > .35:
                duplicate = True; break
        if not duplicate:
            result.append(f)
    return result


def match_face_boxes(source, target, source_size, target_size, expected=None, source_landmarks=True):
    """Independent geometric gate for reprojection when reconstructed eyes are lost."""
    si = [i for i, f in enumerate(source) if (valid_face(f) if source_landmarks else valid_face_box(f))]
    ti = [i for i, f in enumerate(target) if valid_face_box(f)]
    if not si or not ti:
        return []
    cost = np.full((len(si), len(ti)), 1e6)
    for a, i in enumerate(si):
        sb = np.asarray(source[i]['bbox'])
        sp = face_center(source[i])/source_size if expected is None else expected[i]/target_size
        extent = (sb[2:4]-sb[:2])/source_size
        for b, j in enumerate(ti):
            tb = np.asarray(target[j]['bbox']); ratio = ((tb[2:4]-tb[:2])/target_size)/extent
            distance = np.linalg.norm(sp-face_center(target[j])/target_size)
            if distance < .10 and np.all((ratio > .6)&(ratio < 1.65)):
                cost[a, b] = distance
    rows, cols = linear_sum_assignment(cost); pairs = []
    for a, b in zip(rows, cols):
        alternatives = [*np.delete(cost[a], b), *np.delete(cost[:, b], a)]
        if cost[a, b] < .10 and (not alternatives or min(alternatives)-cost[a, b] >= .035):
            pairs.append((si[a], ti[b]))
    return pairs


def project_source_face(source, base, source_face, destination_face):
    """Similarity projection preserves source eye/mouth shape; no target-eye warp."""
    if not valid_face(source_face) or not (valid_face_box(destination_face) or valid_face(destination_face)):
        raise ValueError('元絵の顔または3D顔領域の信頼度が不足しています。')
    sb, tb = [np.asarray(f['bbox']) for f in (source_face, destination_face)]
    scale = float(np.sqrt(np.prod(tb[2:4]-tb[:2])/np.prod(sb[2:4]-sb[:2])))
    shift = (tb[:2]+tb[2:4])/2-scale*(sb[:2]+sb[2:4])/2
    matrix = np.array([[scale, 0, shift[0]], [0, scale, shift[1]]])
    points = np.asarray(source_face['keypoints'])[:, :2]*scale+shift
    mask = np.zeros(base.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, cv2.convexHull(np.rint(points).astype(np.int32)), 255)
    if np.count_nonzero(mask) < 100 or np.any(points < 1) or np.any(points >= np.array(base.shape[1::-1])-1):
        raise ValueError('顔の投影範囲が不正です。')
    # Keep the existing 3D jaw boundary. Transferring the source jaw as well
    # produces a second contour when the generated head has a different outline.
    span = np.linalg.norm(tb[2:4]-tb[:2])
    alpha = np.clip((cv2.distanceTransform(mask, cv2.DIST_L2, 5)-span*.035)/(span*.025), 0, 1)
    warped = cv2.warpAffine(source, matrix, base.shape[1::-1], flags=cv2.INTER_CUBIC)
    return base*(1-alpha[:, :, None])+warped.clip(0, 1)*alpha[:, :, None], alpha, points


def reference_view(values, metadata, projection_file, resolution=2048):
    """Use the reconstruction's original camera, including its grounding rotation."""
    import torch
    if projection_file:
        with np.load(projection_file) as data:
            rotation = np.asarray(data['cameras'][0, :3, :3])
        alignment = metadata['alignment']
        rotation = (np.asarray(alignment.get('support', {}).get('rotation', np.eye(3)))
                    @ np.asarray(alignment.get('upright_rotation', np.eye(3))) @ rotation).T
    else:
        from comfy_extras.nodes_gaussian_splat import _camera_basis
        _, _, right, down, forward = _camera_basis(metadata['camera'], 'cpu')
        rotation = torch.stack([right, down, forward]).numpy()
    axes = torch.tensor(rotation, device='cuda', dtype=torch.float32)
    local = values['means']@axes.T
    active = values['opacities'].reshape(-1) > .2
    low, high = torch.quantile(local[active], torch.tensor([.002, .998], device='cuda'), dim=0)
    center = (low+high)/2
    span = float((high[:2]-low[:2]).max())*1.2
    if not np.isfinite(span) or span <= 0:
        raise ValueError('顔復元の3D範囲が不正です。')
    view = torch.eye(4, device='cuda'); view[:3, :3] = axes; view[:3, 3] = -center
    distance = float((high-low).max())+1.; view[2, 3] += distance
    intrinsic = torch.tensor([[resolution/span, 0, resolution/2],
                              [0, resolution/span, resolution/2], [0, 0, 1]], device='cuda')
    world_center = torch.linalg.solve(axes, center)
    return view, intrinsic, world_center, distance


def subdivide_surface(values, selected, levels=3):
    import torch
    from scipy.spatial.transform import Rotation
    parent = torch.arange(len(selected), device='cuda')
    for _ in range(levels):
        ids, preserve = torch.where(selected)[0], torch.where(~selected)[0]
        scales = values['scales'][ids]; order = scales.argsort(dim=1, descending=True)
        matrices = torch.tensor(Rotation.from_quat(values['quats'][ids].cpu().numpy()[:, [1, 2, 3, 0]])
                                .as_matrix(), device='cuda', dtype=torch.float32)
        axes = torch.gather(matrices, 2, order[:, None, :].expand(-1, 3, -1))
        s = scales.gather(1, order)
        offsets = torch.stack([a*axes[:, :, 0]*s[:, 0:1]*.65+b*axes[:, :, 1]*s[:, 1:2]*.65
                               for a, b in [(-1, -1), (-1, 1), (1, -1), (1, 1)]], 1)
        child = {k: v[ids, None].repeat(1, 4, *([1]*(v.ndim-1))).reshape(-1, *v.shape[1:])
                 for k, v in values.items()}
        child['means'] += offsets.reshape(-1, 3)
        reduced = scales.clone(); reduced.scatter_(1, order[:, :2], scales.gather(1, order[:, :2])*.65)
        child['scales'] = reduced[:, None].expand(-1, 4, -1).reshape(-1, 3)
        child['opacities'] *= .65
        values = {k: torch.cat([v[preserve], child[k]]) for k, v in values.items()}
        parent = torch.cat([parent[preserve], parent[ids].repeat_interleave(4)])
        selected = torch.zeros(len(parent), device='cuda', dtype=torch.bool); selected[len(preserve):] = True
    return values, selected, parent


def smooth_face_depth(means, selected, view, intrinsic, depth, mask):
    """Remove texture-bearing depth layers only inside the source-facing face.

    Fits a robust quadratic to the visible surface. Tangential coordinates,
    other body points and the camera remain unchanged.
    """
    means, selected, view = np.asarray(means), np.asarray(selected), np.asarray(view)
    focal = float(intrinsic[0, 0]); center = np.asarray(intrinsic[:2, 2])
    local = means@view[:3, :3].T+view[:3, 3]
    xy = local[:, :2]*focal+center
    yy, xx = np.nonzero((mask > .8)&np.isfinite(depth)&(depth > 0))
    if len(xx) < 100 or not selected.any():
        raise ValueError('顔の深度を安定して推定できません。')
    origin = np.array([np.median(xx), np.median(yy)])
    extent = float(max(np.ptp(xx), np.ptp(yy)))
    def features(points):
        x, y = ((points-origin)/extent).T
        return np.column_stack([np.ones(len(x)), x, y, x*x, x*y, y*y])
    points = np.column_stack([xx, yy])[::4]; z = depth[yy[::4], xx[::4]]
    a = features(points); weights = np.ones(len(z))
    for _ in range(5):
        coef = np.linalg.lstsq(a*weights[:, None], z*weights, rcond=None)[0]
        residual = z-a@coef
        sigma = max(1e-7, 1.4826*np.median(np.abs(residual-np.median(residual))))
        weights = np.minimum(1, 2.5*sigma/np.maximum(np.abs(residual),1e-8))
    selected_span = float(np.ptp(local[selected, :2],axis=0).max())
    limit = selected_span*.35
    desired = features(xy[selected])@coef
    shifts = np.zeros_like(means)
    amount = np.clip(desired-local[selected,2], -limit, limit)
    ix = np.rint(xy[selected]).astype(int)
    ix[:,0] = np.clip(ix[:,0],0,mask.shape[1]-1); ix[:,1] = np.clip(ix[:,1],0,mask.shape[0]-1)
    amount *= mask[ix[:,1],ix[:,0]]
    shifts[selected] = amount[:,None]*view[2,:3]
    if not np.isfinite(shifts).all():
        raise ValueError('顔の深度補正が不正です。')
    return shifts


def smooth_face_regions(means, selected, view, intrinsic, depth, regions):
    # Fit separate heads independently: a surface spanning two people is invalid.
    if len(regions) == 1:
        return smooth_face_depth(means, selected, view, intrinsic, depth, regions[0])
    local = means@view[:3,:3].T+view[:3,3]
    xy = np.rint(local[:,:2]*float(intrinsic[0,0])+intrinsic[:2,2]).astype(int)
    offsets = np.zeros_like(means); assigned = np.zeros(len(means),bool)
    for mask in regions:
        x=np.clip(xy[:,0],0,mask.shape[1]-1); y=np.clip(xy[:,1],0,mask.shape[0]-1)
        current = selected&(mask[y,x] > .05)
        if np.any(current&assigned):
            raise ValueError('人物の顔領域が重なっているため奥行きを分離できません。')
        if current.any():
            offsets += smooth_face_depth(means,current,view,intrinsic,depth,mask)
            assigned |= current
    return offsets


def validate_restored_surface(before, after, parent_indices, selected_indices, allow_sr_colors=False, surface_offsets=None, surface_basis=None):
    """Check actual tensors against subdivision provenance, including unchanged body."""
    b, a = [{k: np.asarray(v) for k, v in x.items()} for x in (before, after)]
    parent = np.asarray(parent_indices); selected = np.asarray(selected_indices)
    n = len(b['means']); count = len(parent)
    if (set(a) != set(b) or parent.shape != (count,) or selected.shape != (n,)
            or parent.dtype.kind not in 'iu' or (parent < 0).any() or (parent >= n).any()
            or count != n+63*int(selected.sum())):
        raise ValueError('顔復元の表面対応情報が不正です。')
    split = selected[parent]
    occurrences = np.bincount(parent, minlength=n)
    if not np.array_equal(occurrences, np.where(selected, 64, 1)):
        raise ValueError('顔以外の3D表面が増減しました。')
    for key in b:
        if a[key].shape != (count, *b[key].shape[1:]) or not np.isfinite(a[key]).all():
            raise ValueError('顔復元の3Dデータが不正です。')
        if key == 'sh' and allow_sr_colors:
            old = np.clip(b[key][parent[~split], 0]*SH_C0+.5, 0, 1)
            new = a[key][~split, 0]*SH_C0+.5
            if np.max(np.abs(old-new)) > .1501:
                raise ValueError('顔以外の色が高精細化の上限を超えました。')
            continue
        if not np.array_equal(a[key][~split], b[key][parent[~split]]):
            raise ValueError('顔復元で対象外の表面が変更されました。')
    if not np.array_equal(a['quats'], b['quats'][parent]):
        raise ValueError('顔復元で表面の向きが変更されました。')
    offsets = np.zeros_like(b['means']) if surface_offsets is None else np.asarray(surface_offsets)
    if offsets.shape != b['means'].shape or not np.isfinite(offsets).all() or np.any(offsets[~selected]):
        raise ValueError('顔以外の形状補正または不正な補正を検出しました。')
    if np.any(offsets):
        basis = np.asarray(surface_basis)
        if basis.shape != (3,3) or not np.allclose(basis@basis.T,np.eye(3),atol=1e-5):
            raise ValueError('顔の深度基準が不正です。')
        local_delta = offsets@basis.T
        span = float(np.ptp(b['means'][selected]@basis[:2].T,axis=0).max())
        if np.max(np.abs(local_delta[:,:2])) > 1e-6 or np.max(np.abs(local_delta[:,2])) > span*.35+1e-6:
            raise ValueError('顔の深度補正が許容する範囲を超えました。')
    delta = a['means']-b['means'][parent]-offsets[parent]
    bound = np.linalg.norm(b['scales'][parent], axis=1)*1.35+1e-6
    if (np.linalg.norm(delta, axis=1) > bound).any():
        raise ValueError('顔復元で表面の位置が変わりすぎました。')
    for axis in range(3):
        sums = np.bincount(parent, weights=delta[:, axis], minlength=n)
        if np.max(np.abs(sums)/occurrences) > 1e-6:
            raise ValueError('顔復元で3D形状の中心が移動しました。')
    if (a['scales'] <= 0).any() or (a['scales'] > b['scales'][parent]+1e-7).any():
        raise ValueError('顔復元で表面の大きさが不正になりました。')
    expected_opacity = b['opacities'][parent]*np.where(split, .65**3, 1).reshape((-1,)+((1,)*(b['opacities'].ndim-1)))
    if not np.allclose(a['opacities'], expected_opacity, rtol=1e-5, atol=1e-7):
        raise ValueError('顔復元で透明度が不正になりました。')
    if not np.array_equal(a['sh'][:, 1:], b['sh'][parent, 1:]):
        raise ValueError('顔復元で視点依存色が変わりました。')
    rgb = a['sh'][split, 0]*SH_C0+.5
    if len(rgb) and (rgb.min() < -1e-5 or rgb.max() > 1.00001):
        raise ValueError('顔復元の色が範囲外です。')


def restore_faces(before, metadata, request):
    """Run in the isolated worker after optional SR; return tensors and audit stats."""
    import torch
    from PIL import Image
    from gsplat.rendering import rasterization
    sys.path.insert(0, str(Path(__file__).parent/'vendor/anime_face_detector_runtime'))
    from anime_face_detector import LandmarkDetector
    from huggingface_hub import hf_hub_download
    revisions = {'hrnetv2': '9b3435248b26aeb82e2a8578fe9d86d5d57158af',
                 'yolov3': 'afdd4226a79ae8bb81f334dbcffd34f8cc000c38'}
    checkpoints = {}
    for name, revision in revisions.items():
        args = {'repo_id': 'hysts/anime-face-detector-'+name,
                'filename': 'model.safetensors', 'revision': revision}
        try:
            checkpoints[name] = hf_hub_download(**args, local_files_only=True)
        except Exception:
            checkpoints[name] = hf_hub_download(**args)

    started = time.monotonic(); root = Path(request['result']).parent
    reference = np.asarray(Image.open(request['reference_image']).convert('RGB'), dtype=np.float32)/255
    original = (np.asarray(Image.open(request['original_image']).convert('RGB'), dtype=np.float32)/255
                if request.get('original_image') else reference)
    detector = LandmarkDetector(checkpoints['hrnetv2'], face_detector_name='yolov3',
                                face_detector_checkpoint_path=checkpoints['yolov3'], device='cuda:0')
    detect = lambda img: detector((img[:, :, ::-1].clip(0, 1)*255).astype(np.uint8))
    reference_faces = detect(reference); original_faces = detect(original)
    stats = {'method_version': FACE_METHOD_VERSION, 'reference_faces': len(reference_faces),
             'original_faces': len(original_faces), 'restored_faces': 0, 'landmark_model_revisions': revisions}
    if not reference_faces:
        return before, dict(stats, status='no_face_in_reference', seconds=time.monotonic()-started)
    source_pairs = match_faces(reference_faces, original_faces, reference.shape[1::-1], original.shape[1::-1], align_frame=True)
    source_map = dict(source_pairs)
    # A low confidence high resolution identity never overrides the prepared reference.
    sources = [(original, original_faces[source_map[i]]) if i in source_map else (reference, f)
               for i, f in enumerate(reference_faces)]
    values = {k: v.cuda() for k, v in before.items()}
    base = (values['sh'][:, 0]*SH_C0+.5).clamp(0, 1)
    view, intrinsic, center, distance = reference_view(values, metadata, request.get('projection_file'))
    def render(colors, camera, depth=False):
        return rasterization(values['means'], values['quats'], values['scales'], values['opacities'].reshape(-1),
            colors, camera[None], intrinsic[None], 2048, 2048, near_plane=.0001, far_plane=10000,
            packed=False, camera_model='ortho', render_mode='RGB+ED' if depth else 'RGB')
    with torch.no_grad():
        front_render, _, _ = render(base, view, True)
        front_image = front_render[0, :, :, :3].cpu().numpy()
        depth = front_render[0, :, :, 3].cpu().numpy()
    front = detect_render_faces(detect, front_image)
    pairs = match_faces(reference_faces, front, reference.shape[1::-1], (2048, 2048))
    projection_mode = not pairs
    if projection_mode:
        pairs = match_face_boxes(reference_faces, front, reference.shape[1::-1], (2048, 2048))
        if len(pairs) != sum(valid_face(f) for f in reference_faces):
            pairs = []
    if not pairs:
        Image.fromarray(np.uint8(np.clip(front_image, 0, 1)*255)).save(root/'face_match_render.png')
        diagnostic = {'reference_size': reference.shape[1::-1], 'original_size': original.shape[1::-1],
                      'reference': reference_faces, 'original': original_faces, 'render': front,
                      'reference_valid': [valid_face(f) for f in reference_faces],
                      'render_valid': [valid_face(f) for f in front]}
        (root/'face_match_diagnostic.json').write_text(json.dumps(diagnostic, default=lambda x: x.tolist()))
        raise ValueError('元絵と3Dの顔を安全に対応付けられません。顔の復元を未実施のまま完了にはできません。診断: '+str(root/'face_match_diagnostic.json'))
    # Reliable 3D landmarks establish identity, not a license to deform source eyes.
    projection_mode = True
    if request.get('original_image') and any(si not in source_map for si, _ in pairs):
        raise ValueError('元画像の顔を確実に対応付けられません。別の顔や低解像度の参照で代用せず中止しました。')
    front_to_reference = {t: s for s, t in pairs}
    anchors = []
    for face in front:
        x, y = face_center(face); ix, iy = int(np.clip(x, 0, 2047)), int(np.clip(y, 0, 2047))
        local = np.array([(x-1024)/float(intrinsic[0, 0]), (y-1024)/float(intrinsic[1, 1]), depth[iy, ix]])
        anchors.append(np.linalg.solve(view[:3, :3].cpu().numpy(), local-view[:3, 3].cpu().numpy()))
    cameras, targets, masks, baselines, view_counts = [], [], [], [], []
    projected_surfaces = []
    projection_regions = []
    if projection_mode:
        projection_target = front_image.copy(); projection_mask = np.zeros((2048, 2048), np.float32)
        for si, ti in pairs:
            source_image, source_face = sources[si]
            warped, weight, points = project_source_face(source_image, front_image, source_face, front[ti])
            projection_target = np.where((weight > 0)[:, :, None], warped, projection_target)
            projection_mask = np.maximum(projection_mask, weight)
            projection_regions.append(weight)
            yy, xx = np.nonzero(weight > .1)
            boundary = cv2.convexHull(np.column_stack([xx, yy]).astype(np.float32)).reshape(-1, 2)
            surface_points = np.concatenate([points, boundary])
            xy = np.rint(surface_points).astype(int); surface_depth = depth[xy[:, 1], xy[:, 0]]
            if np.count_nonzero(surface_depth > 0) < 22:
                raise ValueError('元絵の顔を写す3D表面が不足しています。')
            surface_depth = np.where(surface_depth > 0, surface_depth, np.median(surface_depth[surface_depth > 0]))
            local = np.column_stack([(surface_points-1024)/float(intrinsic[0, 0]), surface_depth])
            world = np.linalg.solve(view[:3, :3].cpu().numpy(), (local-view[:3, 3].cpu().numpy()).T).T
            projected_surfaces.append(world)
        stats['correspondence_method'] = 'source_shape_surface_projection'
    else:
        stats['correspondence_method'] = 'landmark_warp'

    for ci, (yaw, pitch) in enumerate(ANGLES):
        y, x = np.deg2rad([yaw, pitch])
        ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
        rx = np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]])
        cam = view.clone(); cam[:3, :3] = torch.tensor(rx@ry, device='cuda', dtype=view.dtype)@view[:3, :3]
        cam[:3, 3] = -(cam[:3, :3]@center); cam[2, 3] += distance
        with torch.no_grad(): image = render(base, cam)[0][0].cpu().numpy()
        predicted = detect_render_faces(detect, image); target = image.copy(); mask = np.zeros((2048, 2048), np.float32)
        projected = np.array(anchors)@cam[:3, :3].cpu().numpy().T+cam[:3, 3].cpu().numpy()
        expected = projected[:, :2]*float(intrinsic[0, 0])+1024
        current_pairs = match_faces(front, predicted, (2048, 2048), (2048, 2048), expected, .13)
        transferred = 0
        if projection_mode:
            box_pairs = match_face_boxes(front, predicted, (2048, 2048), (2048, 2048), expected, source_landmarks=False)
            # Confident landmark matches remain valid even when detector-box
            # confidence alone is below the stricter fallback threshold.
            current_pairs = list(current_pairs)
            used_front = {a for a, _ in current_pairs}; used_predicted = {b for _, b in current_pairs}
            current_pairs += [(a, b) for a, b in box_pairs if a not in used_front and b not in used_predicted]
            transferred = sum(fi in front_to_reference for fi, _ in current_pairs)
            for world in projected_surfaces:
                local = world@cam[:3, :3].cpu().numpy().T+cam[:3, 3].cpu().numpy()
                xy = local[:, :2]*float(intrinsic[0, 0])+1024
                hull = np.zeros((2048, 2048), np.uint8)
                cv2.fillConvexPoly(hull, cv2.convexHull(np.rint(xy).astype(np.int32)), 255)
                mask = np.maximum(mask, np.clip(cv2.distanceTransform(hull, cv2.DIST_L2, 5)/8, 0, 1))
            if ci == 0:
                target, mask = projection_target, projection_mask
        for fi, di in ([] if projection_mode else current_pairs):
            if fi not in front_to_reference:
                continue
            source_image, source_face = sources[front_to_reference[fi]]
            try:
                warped, weight = warp_face(source_image, image, source_face, predicted[di])
            except (ValueError, np.linalg.LinAlgError):
                continue
            target = np.where((weight > 0)[:, :, None], warped, target)
            mask = np.maximum(mask, weight); transferred += 1
        cameras.append(cam); targets.append(torch.tensor(target, device='cuda')[None])
        masks.append(torch.tensor(mask, device='cuda')); baselines.append(torch.tensor(image, device='cuda')[None])
        view_counts.append(transferred)
        if ci == 0:
            Image.fromarray((image.clip(0, 1)*255).astype(np.uint8)).save(root/'face_before.png')
            Image.fromarray((target.clip(0, 1)*255).astype(np.uint8)).save(root/'face_target.png')
        print(json.dumps({'stage': 'face_correspondence', 'view': ci, 'faces': transferred}), flush=True)
    if view_counts[0] != len(pairs) or sum(n > 0 for n in view_counts) < 3:
        raise ValueError('複数角度で顔の対応を確認できません。誤った顔の貼り付けを防ぐため中止しました。')
    del detector; torch.cuda.empty_cache()
    selected = torch.zeros(len(base), device='cuda', dtype=torch.bool)
    for cam, mask in zip(cameras, masks):
        visible = torch.ones_like(base, requires_grad=True); image = render(visible, cam)[0]
        inside = torch.autograd.grad((image*mask[None, :, :, None]).sum(), visible, retain_graph=True)[0][:, 0]
        outside = torch.autograd.grad((image*(1-mask)[None, :, :, None]).sum(), visible)[0][:, 0]
        selected |= (inside > .01)&(inside/(inside+outside+1e-8) > .65)
        if projection_mode:
            break  # Only the visible source-facing surface receives source texture.
    original_selection = selected.cpu().numpy()
    if not selected.any() or selected.sum() > min(len(selected)*.4, 40000):
        raise ValueError('顔の表面選択が不正です。')
    surface_offsets = np.zeros_like(before['means'].cpu().numpy())
    if projection_mode:
        surface_offsets = smooth_face_regions(values['means'].cpu().numpy(), original_selection,
            view.cpu().numpy(), intrinsic.cpu().numpy(), depth, projection_regions)
        values['means'] = values['means']+torch.tensor(surface_offsets,device='cuda')
    if projection_mode:
        from face_dense import build_dense_faces
        with torch.no_grad():
            fitted_depth = render(base, view, True)[0][0, :, :, 3].cpu().numpy()
        result, provenance = build_dense_faces(values, original_selection, view.cpu().numpy(),
            intrinsic.cpu().numpy(), fitted_depth, projection_regions, projection_target, request['model'])
        np.savez_compressed(root/'face_surface_provenance.npz', **provenance)
        values = {k: v.cuda() for k, v in result.items()}
        colors = (values['sh'][:, 0]*SH_C0+.5).clamp(0, 1)
        with torch.no_grad():
            final_image = render(colors, view)[0][0].cpu().numpy()
        Image.fromarray((final_image.clip(0, 1)*255).astype(np.uint8)).save(root/'face_after.png')
        stats.update(status='restored', geometry_method='dense_source_face', restored_faces=len(pairs),
            high_resolution_faces=len(source_pairs), view_face_counts=view_counts,
            original_points=len(before['means']), result_points=len(result['means']),
            selected_surface_points=int(original_selection.sum()),
            source_surface_points=len(provenance['pixels']), seconds=time.monotonic()-started)
        return result, stats
    values, selected, parent = subdivide_surface(values, selected)
    base = (values['sh'][:, 0]*SH_C0+.5).clamp(0, 1)
    color_prior = base.clone()
    if projection_mode:
        # Constrain each surface sample to the source's spatial color field.
        # A front-only pixel loss otherwise permits different depth layers to
        # carry opposing eye patterns which separate when the camera rotates.
        local = values['means']@view[:3, :3].T+view[:3, 3]
        xy = local[:, :2]*intrinsic[0, 0]+1024
        grid = (xy/2047*2-1).reshape(1, 1, -1, 2)
        source_texture = torch.tensor(projection_target, device='cuda').permute(2, 0, 1)[None]
        sampled = torch.nn.functional.grid_sample(source_texture, grid, mode='bilinear',
            padding_mode='border', align_corners=True)[0, :, 0].T
        color_prior[selected] = sampled[selected]
    colors = torch.nn.Parameter(color_prior.clone()); optimizer = torch.optim.Adam([colors], lr=.025)
    def face_loss(image, target, mask):
        return (((image-target)**2)*mask[None, :, :, None]).sum()/(mask.sum()*3+1e-6)
    before_losses = [float(face_loss(im, t, m)) for im, t, m in zip(baselines, targets, masks)]
    for step in range(1400):
        i = step % len(cameras); mask = masks[i]
        optimizer.param_groups[0]['lr'] = .025 if step < 1050 else .005
        optimizer.zero_grad(set_to_none=True); predicted = render(colors, cameras[i])[0]
        loss = ((4.0 if i == 0 else (0.0 if projection_mode else 1.0))*face_loss(predicted, targets[i], mask)
                + .5*((predicted-baselines[i])**2*(1-mask)[None, :, :, None]).mean()
                + (.08 if projection_mode else .0001)*(colors[selected]-color_prior[selected]).square().mean())
        if not torch.isfinite(loss):
            raise ValueError('顔復元の学習値が不正です。')
        loss.backward(); colors.grad[~selected] = 0; optimizer.step()
        with torch.no_grad(): colors.clamp_(0, 1); colors[~selected] = base[~selected]
    with torch.no_grad():
        after_losses = [float(face_loss(render(colors, c)[0], t, m)) for c, t, m in zip(cameras, targets, masks)]
        if (after_losses[0] >= before_losses[0]*(.4 if projection_mode else .65)
                or (not projection_mode and sum(after_losses) >= sum(before_losses)*.7)):
            raise ValueError('元絵の顔への一致度が十分に改善しませんでした。')
        front_result = render(colors, cameras[0])[0][0].cpu().numpy()
        Image.fromarray((front_result.clip(0, 1)*255).astype(np.uint8)).save(root/'face_after.png')
    result = {k: v.cpu() for k, v in values.items()}
    result['sh'][selected.cpu(), 0] = (colors.detach().cpu()[selected.cpu()]-.5)/SH_C0
    validate_restored_surface(before, result, parent.cpu().numpy(), original_selection, surface_offsets=surface_offsets, surface_basis=view[:3,:3].cpu().numpy())
    np.savez_compressed(root/'face_surface_provenance.npz', parent=parent.cpu().numpy(), selected=original_selection, surface_offsets=surface_offsets, surface_basis=view[:3,:3].cpu().numpy())
    stats.update(status='restored', restored_faces=len(pairs), high_resolution_faces=len(source_pairs),
                 view_face_counts=view_counts, before_losses=before_losses, after_losses=after_losses,
                 original_points=len(before['means']), result_points=len(result['means']),
                 selected_surface_points=int(original_selection.sum()), max_face_depth_adjustment=float(np.linalg.norm(surface_offsets,axis=1).max()), iterations=1400,
                 seconds=time.monotonic()-started)
    return result, stats
