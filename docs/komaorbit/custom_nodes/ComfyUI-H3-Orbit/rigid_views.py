"""Render the saved rigid scene into the same mouse/save/export grid contract."""
import json
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image
import comfy.model_management as mm
from .camera import camera_plan
from .grid_nodes import new_target, grid_manifest, write_metadata, attach_metadata, commit_manifest
from .rigid_scene import read_scene
from .scene_geometry import rigid_camera_grid
from .scene_layers import minimum_elevation


class H3OrbitRigidViews:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'scene_prefix': ('STRING', {'forceInput': True}), 'original': ('IMAGE',)},
                'hidden': {'prompt': 'PROMPT', 'extra_pnginfo': 'EXTRA_PNGINFO', 'unique_id': 'UNIQUE_ID'}}
    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('orbit_manifest',)
    OUTPUT_NODE = True
    FUNCTION = 'render'
    CATEGORY = 'MiniMax H3/Orbit'

    def render(self, scene_prefix, original, prompt=None, extra_pnginfo=None, unique_id=None):
        if not scene_prefix.endswith('/scene'):
            raise ValueError('固定3Dノードの出力を接続してください。')
        scene_id = scene_prefix[:-6]
        source, scene = read_scene(scene_id+'/manifest.json', original)
        width, height = source['width'], source['height']
        if source.get('projection')=='unified_textured_scene':
            from .scene_textures import textured_camera_grid
            views=textured_camera_grid(source,width,height)
        else:views = rigid_camera_grid(source['camera'],width,height,minimum_elevation(source))
        source_h, source_w = original.shape[1:3]
        if width*source_h != height*source_w:
            raise ValueError('固定3Dと元画像の縦横比が一致しません。')
        target, subfolder = new_target()
        geometry = dict(schema_version=1,source_width=source_w,source_height=source_h,
                        canvas_width=width,canvas_height=height,content_width=width,content_height=height,
                        export_width=width,export_height=height,left=0,top=0,frame_basis_zoom=.5)
        manifest = grid_manifest(subfolder,geometry,original,camera_plan('上下3段階','',.5),'orbit_grid',73)
        manifest.update(generation_profile='fixed3d_camera_grid',pose_status='fixed_geometry',projection=source.get('projection','orthographic_subject_perspective_background'),
                        elevations=[views[0]['elevation'],0,30],requested_elevations=[-30,0,30],
                        angle_basis='fixed_scene_camera_degrees',scene_id=scene_id,
                        scene_sha256=source['scene_sha256'],quality_status='requires_visual_review')
        jobs=[]
        for view in views:
            folder=target/f"row_{view['row']:02d}"
            folder.mkdir(exist_ok=True)
            jobs.append({'camera':view['camera'],'png':str(folder/f"frame_{view['index']:03d}.png")})
        request={'scene':str(scene),'width':width,'height':height,'jobs':jobs}
        request_path=target/'render_request.json';request_path.write_text(json.dumps(request))
        (target/'camera_grid.json').write_text(json.dumps(views,indent=2))
        mm.unload_all_models();mm.soft_empty_cache()
        with (target/'renderer.log').open('w') as log:
            process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('scene_render.py')),str(request_path)],stdout=log,stderr=subprocess.STDOUT)
            try:
                started=time.monotonic()
                while process.poll() is None:
                    mm.throw_exception_if_processing_interrupted()
                    if time.monotonic()-started > 600:
                        raise RuntimeError('固定3Dの全視点描画が10分以内に完了しませんでした。')
                    time.sleep(.2)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
        if process.returncode != 0:
            raise RuntimeError('固定3Dの視点描画に失敗しました。'+(target/'renderer.log').read_text()[-1800:])
        for job in jobs:
            with Image.open(job['png']) as image:
                if image.size != (width,height):raise ValueError('固定3D画像の寸法が不正です。')
                image.verify()
        pnginfo=write_metadata(target,manifest,original,prompt,extra_pnginfo,unique_id)
        attach_metadata(target/'row_00/frame_000.png',pnginfo)
        commit_manifest(target,manifest)
        return {'ui':{'h3_orbit':[manifest]},'result':(subfolder+'/manifest.json',)}
