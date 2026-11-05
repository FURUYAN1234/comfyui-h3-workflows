"""Refine surface colors and restore matched original facial features."""
import numpy as np
from scipy.spatial import cKDTree

SH_C0 = .28209479177387814
DETAIL_VERSION = 4
DEFAULT_DETAIL_MODEL = 'RealESRGAN_x4plus.safetensors'


def enhance_surface_colors(points, colors, opacity, strength=1.):
    """Legacy color-only unsharp retained for existing callers."""
    p, c, o = [np.asarray(x, dtype=np.float32) for x in (points, colors, opacity)]
    if (p.ndim != 2 or p.shape[1] != 3 or c.shape != p.shape or o.shape != (len(p),)
            or not all(np.isfinite(x).all() for x in (p, c, o)) or not 0 <= strength <= 2):
        raise ValueError('人物の色・3D表面が不正です。')
    result = c.copy()
    ids = np.flatnonzero(o > .2)
    if len(ids) < 8 or strength == 0:
        return result
    tree = cKDTree(p[ids])
    k = min(64, len(ids))
    active_colors, active_opacity = c[ids], o[ids]
    for start in range(0, len(ids), 16384):
        end = min(start + 16384, len(ids))
        distance, neighbors = tree.query(p[ids[start:end]], k=k, workers=2)
        radius = np.maximum(distance[:, -1:], 1e-8)
        weights = np.exp(-2 * (distance / radius) ** 2) * active_opacity[neighbors]
        average = ((active_colors[neighbors] * weights[:, :, None]).sum(1)
                   / np.maximum(weights.sum(1)[:, None], 1e-8))
        detail = np.clip(c[ids[start:end]] - average, -.10, .10)
        result[ids[start:end]] = np.clip(c[ids[start:end]] + strength * detail, 0, 1)
    return result


def detail_cameras(points, opacity, basis, resolution=1024):
    """Ten independent orthographic views with a shared center and safe framing."""
    p, o, b = np.asarray(points), np.asarray(opacity).reshape(-1), np.asarray(basis)
    if (p.ndim != 2 or p.shape[1] != 3 or o.shape != (len(p),) or b.shape != (3, 3)
            or not all(np.isfinite(a).all() for a in (p, o, b))
            or not np.allclose(b @ b.T, np.eye(3), atol=1e-4)
            or not isinstance(resolution, int) or resolution < 64):
        raise ValueError('高精細化カメラの入力が不正です。')
    active = p[o > .2]
    if len(active) < 8:
        raise ValueError('高精細化できる3D表面がありません。')
    low, high = np.quantile(active, [.001, .999], axis=0)
    center = (low + high) / 2
    if np.max(high - low) < 1e-7:
        raise ValueError('3D表面の大きさが不正です。')
    rotations = []
    for yaw, pitch in [(0, 0), (-30, 0), (30, 0), (-70, 0), (70, 0),
                       (-120, 0), (120, 0), (180, 0), (0, -20), (0, 20)]:
        y, x = np.deg2rad([yaw, pitch])
        ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0],
                       [-np.sin(y), 0, np.cos(y)]])
        rx = np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)],
                       [0, np.sin(x), np.cos(x)]])
        rotations.append(rx @ ry @ b)
    local = [(active - center) @ r.T for r in rotations]
    extent = max(float(np.quantile(np.abs(q[:, :2]), .999, axis=0).max()) for q in local)
    span = 2 * extent * 1.15
    distance = float(np.linalg.norm(active - center, axis=1).max()) + 1.
    views = []
    for r in rotations:
        view = np.eye(4, dtype=np.float32)
        view[:3, :3] = r
        view[:3, 3] = -r @ center + np.array([0, 0, distance])
        views.append(view)
    intrinsic = np.array([[resolution/span, 0, resolution/2],
                          [0, resolution/span, resolution/2], [0, 0, 1]], np.float32)
    return np.stack(views), intrinsic


def validate_detail_result(before, after):
    """Refuse geometry changes, invalid tensors, or excessive texture changes."""
    if set(before) != set(after):
        raise ValueError('高精細化の出力項目が一致しません。')
    arrays = lambda values: {k: np.asarray(v) for k, v in values.items()}
    b, a = arrays(before), arrays(after)
    for key in b:
        if b[key].shape != a[key].shape or not np.isfinite(a[key]).all():
            raise ValueError('高精細化の出力が不正です。')
        if key != 'sh' and not np.array_equal(b[key], a[key]):
            raise ValueError('高精細化による3D形状の変更を検出しました。')
    if a['sh'].ndim != 3 or a['sh'].shape[1] < 1:
        raise ValueError('高精細化の色配列が不正です。')
    if not np.array_equal(b['sh'][:, 1:], a['sh'][:, 1:]):
        raise ValueError('高精細化による視点依存色の変更を検出しました。')
    original = np.clip(b['sh'][:, 0] * SH_C0 + .5, 0, 1)
    result = a['sh'][:, 0] * SH_C0 + .5
    if (result.min() < -1e-5 or result.max() > 1.00001
            or np.max(np.abs(result-original)) > .1501):
        raise ValueError('高精細化による色の変更量が上限を超えました。')


class H3OrbitSubjectDetail:
    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths
        models = folder_paths.get_filename_list('upscale_models')
        default = DEFAULT_DETAIL_MODEL if DEFAULT_DETAIL_MODEL in models else (models[0] if models else '')
        return {'required': {'scene_prefix': ('STRING', {'forceInput': True})},
                'optional': {'reference_image': ('IMAGE', {'tooltip': '3D化へ渡した人物画像。顔の位置対応に使います。'}),
                             'original_image': ('IMAGE', {'tooltip': '取り込み元の高解像度画像。元の瞳・口の形を復元します。'}),
                             'detail_model': (models, {'default': default,
                  'tooltip': '10方向の表面高精細化に続いて、接続された元絵の顔を7方向で合わせます。顔の表面だけを細分化し、身体の姿勢と背景は維持します。'})}}

    RETURN_TYPES = ('SPLAT', 'LOAD3D_CAMERA', 'INT', 'INT', 'STRING', 'LOAD3D_MODEL_INFO')
    RETURN_NAMES = ('scene', 'camera', 'width', 'height', 'filename_prefix', 'model_placement')
    FUNCTION = 'enhance'
    CATEGORY = 'MiniMax H3/Orbit'

    def enhance(self, scene_prefix, detail_model=DEFAULT_DETAIL_MODEL, reference_image=None, original_image=None):
        import json
        import shutil
        import subprocess
        import sys
        import time
        from pathlib import Path
        import folder_paths
        import comfy.model_management as mm
        from safetensors.torch import load_file
        from .scene_refine import scene_source
        from .rigid_scene import splat_from_arrays, IDENTITY_MODEL
        from .grid_nodes import new_target, commit_manifest, file_digest, source_hash
        from .scene_layers import prepare_viewer_layers
        import hashlib
        from PIL import Image
        def image_bytes(value):
            if value is None:
                return None
            if value.ndim != 4 or value.shape[0] != 1 or value.shape[-1] not in (3, 4):
                raise ValueError('顔復元には一枚のRGB画像を接続してください。')
            array = value[0, :, :, :3].detach().cpu().numpy()
            if not np.isfinite(array).all():
                raise ValueError('顔復元の元画像に不正な値があります。')
            return np.round(np.clip(array, 0, 1)*255).astype(np.uint8)
        reference_array, original_array = image_bytes(reference_image), image_bytes(original_image)
        reference_key = hashlib.sha256(reference_array.tobytes()).hexdigest() if reference_array is not None else None
        original_key = hashlib.sha256(original_array.tobytes()).hexdigest() if original_array is not None else None
        model_path = folder_paths.get_full_path('upscale_models', detail_model)
        if not model_path or not Path(model_path).is_file():
            raise ValueError('人物高精細化用の超解像モデルが見つかりません: ' + detail_model)
        model_digest = file_digest(Path(model_path))
        m, source = scene_source(scene_prefix)
        if original_image is not None and m.get('source_sha256') != source_hash(original_image):
            raise ValueError('顔復元の元画像が、この3Dを作った画像と異なります。正しい元画像を接続してください。')
        if m.get('projection') != 'unified_textured_scene':
            raise ValueError('連続背景と床を合成した固定3Dを接続してください。')
        from .face_restore import FACE_METHOD_VERSION
        detail = m.get('color_detail', {})
        if (detail.get('version') == DETAIL_VERSION and detail.get('model_sha256') == model_digest
                and detail.get('reference_sha256') == reference_key and detail.get('original_sha256') == original_key
                and (reference_array is None or detail.get('face_restore', {}).get('method_version') == FACE_METHOD_VERSION)):
            v = load_file(str(source))
            return splat_from_arrays(v), m['camera'], m.get('snapshot_width',m['width']), m.get('snapshot_height',m['height']), scene_prefix, [IDENTITY_MODEL]
        # Reuse the unenhanced source when switching models or upgrading the algorithm.
        # Applying SR to a previously sharpened result would compound artifacts.
        for _ in range(4):
            if not m.get('color_detail'):
                break
            scene_prefix = m['source_scene'] + '/scene'
            m, source = scene_source(scene_prefix)
        if m.get('color_detail'):
            raise ValueError('高精細化前の3Dデータを特定できません。')
        before = load_file(str(source))
        target, subfolder = new_target()
        request = {'scene': str(source), 'result': str(target/'scene.safetensors'),
                   'model': detail_model, 'stats': str(target/'detail_stats.json')}
        if reference_array is not None:
            Image.fromarray(reference_array).save(target/'face_reference.png')
            request['reference_image'] = str(target/'face_reference.png')
            if original_array is not None:
                Image.fromarray(original_array).save(target/'face_original.png')
                request['original_image'] = str(target/'face_original.png')
            ancestor = source.parent
            for _ in range(8):
                projection = ancestor/'projection_sources.npz'
                if projection.is_file():
                    request['projection_file'] = str(projection)
                    break
                record = json.loads((ancestor/'manifest.json').read_text())
                if not record.get('source_scene'):
                    break
                ancestor = (Path(folder_paths.get_output_directory())/record['source_scene']).resolve()
                if not ancestor.is_relative_to(Path(folder_paths.get_output_directory()).resolve()):
                    raise ValueError('元の3Dカメラの保存先が不正です。')
        request_path = target/'detail_request.json'
        request_path.write_text(json.dumps(request), encoding='utf-8')
        mm.unload_all_models()
        mm.soft_empty_cache()
        log_path = target/'detail.log'
        with log_path.open('w') as log:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name('subject_detail_worker.py')), str(request_path)],
                stdout=log, stderr=subprocess.STDOUT)
            try:
                started = time.monotonic()
                while process.poll() is None:
                    mm.throw_exception_if_processing_interrupted()
                    if time.monotonic()-started > 900:
                        raise RuntimeError('3D高精細化が15分以内に完了しませんでした。')
                    time.sleep(.2)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        if process.returncode != 0:
            raise RuntimeError('3D高精細化に失敗しました。' + log_path.read_text()[-1800:])
        v = load_file(str(target/'scene.safetensors'))
        stats = json.loads((target/'detail_stats.json').read_text())
        restored = stats.get('face_restore', {}).get('status') == 'restored'
        if restored:
            from .face_restore import validate_restored_surface
            with np.load(target/'face_surface_provenance.npz') as provenance:
                if 'kind' in provenance and str(provenance['kind']) == 'dense_source_face':
                    from .face_dense import validate_dense_faces
                    validate_dense_faces(before, v, provenance, allow_sr_colors=True)
                else:
                    validate_restored_surface(before, v, provenance['parent'], provenance['selected'], allow_sr_colors=True,
                        surface_offsets=provenance['surface_offsets'] if 'surface_offsets' in provenance else None,
                        surface_basis=provenance['surface_basis'] if 'surface_basis' in provenance else None)
        else:
            validate_detail_result(before, v)
        for name in m['texture_sha256']:
            shutil.copy2(source.parent/name, target/name)
        stats = json.loads((target/'detail_stats.json').read_text())
        result = dict(m, subfolder=subfolder, source_scene=scene_prefix[:-6],
                      scene_sha256=file_digest(target/'scene.safetensors'),
                      color_detail={'version': DETAIL_VERSION, 'method': 'multiview_sr_and_source_face_recovery',
                                    'model': detail_model, 'model_sha256': model_digest,
                                    'geometry_changed': restored, 'body_geometry_changed': False,
                                    'reference_sha256': reference_key, 'original_sha256': original_key, **stats})
        if original_array is not None:
            result['snapshot_width']=int(original_array.shape[1])
            result['snapshot_height']=int(original_array.shape[0])
        if restored:
            result['alignment'] = json.loads(json.dumps(m['alignment']))
            result['alignment']['components'] = {'background': 0, 'subject': len(v['means'])}
        commit_manifest(target, result)
        prepare_viewer_layers(target/'scene.safetensors', result, v)
        return splat_from_arrays(v), m['camera'], result.get('snapshot_width',m['width']), result.get('snapshot_height',m['height']), subfolder+'/scene', [IDENTITY_MODEL]
