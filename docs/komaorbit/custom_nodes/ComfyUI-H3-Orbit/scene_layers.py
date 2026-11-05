"""One rigid subject and its surroundings use separate projection cameras."""
import json
import math
from pathlib import Path


def component_bounds(components, count):
    if not isinstance(components, dict):
        raise ValueError('人物と背景の分離情報がありません。固定3Dを再構成してください。')
    start, length = components.get('background'), components.get('subject')
    if type(start) is not int or type(length) is not int or start < 0 or length <= 0 or start + length > count:
        raise ValueError('人物と背景の分離情報が不正です。')
    return start, start + length


def foreground_mask(components,count):
    """Select the body; ground remains in the perspective environment."""
    import torch
    start,end=component_bounds(components,count)
    selection=torch.zeros(count,dtype=torch.bool);selection[start:end]=True
    ground=components.get('ground',0)
    if type(ground) is not int or ground<0 or end+ground>count:
        raise ValueError('固定3Dの床の分離情報が不正です。')
    patch=components.get('contact_patch',0)
    if type(patch) is not int or patch<0 or end+ground+patch>count:
        raise ValueError('固定3Dの接地部分の分離情報が不正です。')
    if patch:selection[count-patch:]=True
    return selection


def anchor_shift(x,y,depth,radius,focal):
    if not all(math.isfinite(v) for v in [x,y,depth,radius,focal]) or min(depth,radius,focal)<=0:
        raise ValueError('足元の投影位置が不正です。')
    factor=focal*(radius/depth-1)
    return x*factor,y*factor


def low_orbit_camera(camera,floor):
    """Move the orbit inward while preserving orthographic subject size."""
    eye=[camera['position'][k]-camera['target'][k] for k in 'xyz']
    radius=math.sqrt(sum(v*v for v in eye));height=float(floor)+camera['target']['y']
    if not math.isfinite(height) or height<=0 or radius<=0:raise ValueError('床からのカメラ高さが不正です。')
    close=min(radius,height*.86/math.sin(math.radians(30)))
    new_fov=math.degrees(2*math.atan(radius/close*math.tan(math.radians(camera['fov'])/2)))
    return dict(camera,h3_subject_fitted=True,position={k:camera['target'][k]+eye[i]*close/radius for i,k in enumerate('xyz')},fov=new_fov)


def background_camera(camera):
    """Match the target-plane scale; keep perspective for distant surroundings."""
    if camera.get('cameraType') != 'orthographic':
        return dict(camera)
    radius = math.sqrt(sum((camera['position'][k]-camera['target'][k])**2 for k in 'xyz'))
    span = camera['frustum']['top']-camera['frustum']['bottom']
    zoom = camera.get('zoom', 1)
    if not all(math.isfinite(v) and v > 0 for v in [radius,span,zoom]):
        raise ValueError('背景カメラの表示範囲が不正です。')
    return dict(camera,cameraType='perspective',zoom=1.,fov=math.degrees(2*math.atan(span/(2*radius*zoom))))


def minimum_elevation(metadata):
    """Keep the orbit camera above the reconstructed floor, not below the roof."""
    floor=metadata.get('alignment',{}).get('orbit_clearance',{}).get('floor')
    if floor is None:
        return -30
    camera=metadata['camera']
    radius=math.sqrt(sum((camera['position'][k]-camera['target'][k])**2 for k in 'xyz'))
    # Stored splat Y points down; viewer/world Y points up.
    relative_floor=-float(floor)-camera['target']['y']
    if not math.isfinite(relative_floor) or radius<=0 or relative_floor>=0:
        raise ValueError('固定3Dの床とカメラ中心の位置関係が不正です。')
    clearance=max(abs(relative_floor)*.12,radius*.01)
    safe_sine=min(-.001,max(-1.,(relative_floor+clearance)/radius))
    return min(-1,max(-30,math.ceil(math.degrees(math.asin(safe_sine)))))


def prepare_viewer_layers(scene, metadata, values):
    """Cache native viewer derivatives without replacing the original scene."""
    from comfy_extras.nodes_gaussian_splat import _gaussian_ply_bytes
    if metadata.get('projection')=='unified_textured_scene':
        root=Path(scene).parent
        expected={'schema_version':5,'scene_sha256':metadata['scene_sha256'],'projection':'unified_textured_scene','subject':'subject.ply','texture_environment':metadata['texture_environment']}
        record=root/'viewer_layers.json'
        if record.is_file() and json.loads(record.read_text())==expected and (root/'subject.ply').is_file():return
        payload=_gaussian_ply_bytes(values['means'],values['scales'],values['quats'],values['opacities'],values['sh'])
        temp=root/'subject.ply.tmp';temp.write_bytes(payload);temp.replace(root/'subject.ply');temp=record.with_suffix('.tmp');temp.write_text(json.dumps(expected));temp.replace(record);return
    start,end=component_bounds(metadata.get('alignment',{}).get('components'),len(values['means']))
    root=Path(scene).parent
    record=root/'viewer_layers.json'
    expected={'schema_version':4,'scene_sha256':metadata['scene_sha256'],
              'projection':'orthographic_subject_perspective_background',
              'subject':'subject.ply','background':'background.ply'}
    if record.is_file() and json.loads(record.read_text())==expected and all((root/expected[k]).is_file() for k in ['subject','background']):
        return
    import torch
    selection=foreground_mask(metadata.get('alignment',{}).get('components'),len(values['means']))
    for name,mask in [('subject',selection),('background',~selection)]:
        v={k:t[mask] for k,t in values.items()}
        payload=_gaussian_ply_bytes(v['means'],v['scales'],v['quats'],v['opacities'],v['sh'])
        temp=root/f'{name}.ply.tmp';temp.write_bytes(payload);temp.replace(root/f'{name}.ply')
    temp=record.with_suffix('.tmp');temp.write_text(json.dumps(expected));temp.replace(record)
