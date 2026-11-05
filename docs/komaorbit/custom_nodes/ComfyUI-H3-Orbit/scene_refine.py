"""Normal-graph preparation and finishing for a continuous textured 3D environment."""
import json,math
from pathlib import Path
import numpy as np
import torch
from PIL import Image
from safetensors.torch import load_file,save_file
import folder_paths
from .rigid_scene import read_scene,splat_from_arrays,IDENTITY_MODEL
from .grid_nodes import new_target,commit_manifest,file_digest
from .scene_surfaces import project_colors,_ambient
from .scene_textures import rgb_array,panoramic_points,prepare_wrap,merge_wrap,ground_shadows
from .scene_grounding import support_rotation
from .scene_geometry import _mat_to_quat,_quat_mul,fit_subject_camera

PANO_PROMPT=('Create a seamless 360-degree equirectangular panorama of the EMPTY environment in the reference. '
 'Remove the character completely. Expand and reimagine the surroundings into one continuous coherent '
 'full-circle scene with intact complete buildings, continuous rooflines and walls, natural landscape '
 'features and consistent ground. Match the existing architectural style, colors, materials, lighting, '
 'and illustration or photographic aesthetic. The original front-facing environment remains at the '
 'center. The left and right edges show the same back direction and connect seamlessly. Detailed sky '
 'above and ground below. No people, characters, human shadows, text, panels or floating fragments.')
FLOOR_PROMPT=('Create a square seamless material texture showing this same ground from directly overhead. '
 'The entire image is filled with the same detailed ground material and a coherent arrangement of its '
 'surface pattern, extending all the way to all four edges. Match the reference color palette and '
 'illustration or photographic style. Flat orthographic top-down view, evenly lit. No blank regions, '
 'people, cast shadows, objects, walls, sky, perspective, frame or text.')
WRAP_PROMPT=('Reconnect the broken central join of this panoramic environment into complete plausible '
 'structures. Make the central wall and roof lines continuous and supported, repair disconnected '
 'landscape fragments and ground lines. Keep the surrounding scene, colors, lighting and style. '
 'Empty environment with no people, animals, foreground frames, fences, bars or text.')


def scene_source(prefix):
    if not prefix.endswith('/scene'):raise ValueError('固定3Dノードの出力を接続してください。')
    return read_scene(prefix[:-6]+'/manifest.json')


def tensor_image(a):return torch.from_numpy(np.ascontiguousarray(rgb_array(a)))[None]


class H3OrbitSceneTexturePrepare:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'scene_prefix':('STRING',{'forceInput':True})}}
    RETURN_TYPES=('IMAGE','IMAGE','STRING','STRING')
    RETURN_NAMES=('panorama','ground','panorama_prompt','ground_prompt')
    FUNCTION='prepare';CATEGORY='MiniMax H3/Orbit'
    def prepare(self,scene_prefix):
        m,scene=scene_source(scene_prefix);root=Path(folder_paths.get_output_directory());z=np.load(scene.parent/'projection_sources.npz')
        paths=[(root/x).resolve() for x in m['input_files']]
        if len(paths)!=len(z['cameras']) or not all(p.is_relative_to(root.resolve()) for p in paths):raise ValueError('背景参照の対応が不正です。')
        for p,digest in zip(paths,m['input_sha256']):
            if file_digest(p)!=digest:raise ValueError('背景参照画像が変更されています。')
        image=Image.open(paths[0]).convert('RGB');w,h=image.size
        # Start from the intact source view, never the already broken reconstructed mosaic.
        crop_h=min(h,w/2);crop_w=crop_h*2
        panorama=image.crop(((w-crop_w)/2,(h-crop_h)/2,(w+crop_w)/2,(h+crop_h)/2)).resize((1536,768),Image.Resampling.LANCZOS)
        mask=z['masks'][0];mh,mw=mask.shape
        candidates=[(.1,.72,.4,1),(.6,.72,.9,1),(0,.65,.3,.95),(.7,.65,1,.95)]
        def coverage(box):
            x0,y0,x1,y1=box
            return float(mask[int(y0*mh):int(y1*mh),int(x0*mw):int(x1*mw)].mean())
        box=min(candidates,key=coverage)
        floor=image.crop(tuple(round(v*(w if i%2==0 else h)) for i,v in enumerate(box))).resize((768,768),Image.Resampling.LANCZOS)
        return tensor_image(np.asarray(panorama,dtype=np.float32)/255),tensor_image(np.asarray(floor,dtype=np.float32)/255),PANO_PROMPT,FLOOR_PROMPT



class H3OrbitTextureWrap:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'panorama':('IMAGE',)}}
    RETURN_TYPES=('IMAGE','STRING');RETURN_NAMES=('seam_image','prompt');FUNCTION='prepare';CATEGORY='MiniMax H3/Orbit'
    def prepare(self,panorama):return tensor_image(prepare_wrap(panorama)),WRAP_PROMPT


class H3OrbitSceneTextureFinish:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'scene_prefix':('STRING',{'forceInput':True}),
        'panorama':('IMAGE',{'lazy':True}),'seam':('IMAGE',{'lazy':True}),'ground':('IMAGE',{'lazy':True}),
        'upscale_model':('UPSCALE_MODEL',{'lazy':True})}}
    RETURN_TYPES=('SPLAT','LOAD3D_CAMERA','INT','INT','STRING','LOAD3D_MODEL_INFO')
    RETURN_NAMES=('scene','camera','width','height','filename_prefix','model_placement')
    FUNCTION='finish';CATEGORY='MiniMax H3/Orbit'
    def check_lazy_status(self,scene_prefix,panorama=None,seam=None,ground=None,upscale_model=None):
        m,_=scene_source(scene_prefix)
        if m.get('projection')=='unified_textured_scene':return []
        return [k for k,v in [('panorama',panorama),('seam',seam),('ground',ground),('upscale_model',upscale_model)] if v is None]
    def finish(self,scene_prefix,panorama=None,seam=None,ground=None,upscale_model=None):
        m,source=scene_source(scene_prefix);values=load_file(str(source))
        if m.get('projection')=='unified_textured_scene':return splat_from_arrays(values),m['camera'],m['width'],m['height'],scene_prefix,[IDENTITY_MODEL]
        parts=m['alignment']['components'];start=parts['background'];n=parts['subject'];body={k:v[start:start+n].clone() for k,v in values.items()};del values
        ground_info=m['alignment']['ground']
        active=body['opacities']>.5
        r,support=support_rotation(body['means'][active].numpy(),fallback_floor=ground_info['floor'])
        has_support=support['method']=='rigid_support_plane'
        if has_support:
            rotation=torch.from_numpy(r.astype(np.float32))
            body['means']=body['means']@rotation.T
            body['quats']=_quat_mul(_mat_to_quat(rotation[None]).expand(n,4),body['quats'])
        camera=dict(m['camera']);position=np.array([camera['position'][k]-camera['target'][k] for k in 'xyz']);old_radius=float(np.linalg.norm(position));base_radius=m['alignment']['orbit_clearance']['radius']/1.1
        camera['position']={k:camera['target'][k]+position[i]*base_radius/old_radius for i,k in enumerate('xyz')};camera['fov']=math.degrees(2*math.atan(old_radius/base_radius*math.tan(math.radians(camera['fov'])/2)));camera.pop('h3_subject_fitted',None);camera['cameraType']='perspective';camera.pop('frustum',None);camera['zoom']=1
        camera=fit_subject_camera(body,camera,{'background':0,'subject':n},m['width'],m['height']);camera['h3_subject_fitted']=True
        pano=merge_wrap(panorama,seam);floor_image=rgb_array(ground)
        from .upscale_compat import upscale_image
        pano=rgb_array(upscale_image(upscale_model,tensor_image(pano)).cpu().numpy());floor_image=rgb_array(upscale_image(upscale_model,tensor_image(floor_image)).cpu().numpy())
        origin=[ground_info['anchor'][0],ground_info['anchor'][2]]
        floor_image=ground_shadows(floor_image,body['means'][active].numpy(),support['floor'],origin,
                                   ground_info['radius'],contact=has_support)
        target,subfolder=new_target();save_file(body,str(target/'scene.safetensors'))
        for name,array in [('panorama',pano),('ground',floor_image)]:Image.fromarray(np.rint(array.clip(0,1)*255).astype(np.uint8)).save(target/(name+'.png'))
        env={'panorama':'panorama.png','ground':'ground.png','radius':m['alignment']['environment']['radius'],'floor':support['floor'],'ground_origin':origin,'ground_radius':ground_info['radius']}
        alignment=dict(m['alignment'],support=support,components={'background':0,'subject':n},ground=dict(ground_info,floor=support['floor']),orbit_clearance=dict(m['alignment']['orbit_clearance'],floor=support['floor']))
        result=dict(m,subfolder=subfolder,projection='unified_textured_scene',camera=camera,alignment=alignment,texture_environment=env,
                    texture_sha256={name+'.png':file_digest(target/(name+'.png')) for name in ['panorama','ground']},
                    source_scene=scene_prefix[:-6],scene_sha256=file_digest(target/'scene.safetensors'),quality_status='requires_visual_review')
        commit_manifest(target,result)
        from .scene_layers import prepare_viewer_layers
        prepare_viewer_layers(target/'scene.safetensors',result,body)
        return splat_from_arrays(body),camera,m['width'],m['height'],subfolder+'/scene',[IDENTITY_MODEL]
