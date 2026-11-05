"""Continuous background textures and one physical camera for body and floor."""
import math
from pathlib import Path
import numpy as np
import cv2
from scipy.ndimage import gaussian_filter, maximum_filter
from scipy.spatial.transform import Rotation
from PIL import Image


def rgb_array(image):
    a=np.asarray(image,dtype=np.float32)
    if a.ndim==4 and a.shape[0]==1:a=a[0]
    if a.ndim!=3 or a.shape[-1] not in (3,4) or not np.isfinite(a).all():raise ValueError('背景画像の画素・形式が不正です。')
    return np.clip(a[:,:,:3]*a[:,:,3:]+(1-a[:,:,3:]) if a.shape[-1]==4 else a,0,1)


def panoramic_points(width,height,radius):
    yy,xx=np.mgrid[:height,:width];theta=xx/width*2*np.pi-np.pi;phi=(yy/(height-1)-.5)*np.pi
    return (np.stack([np.sin(theta)*np.cos(phi),np.sin(phi),np.cos(theta)*np.cos(phi)],-1)*radius).astype(np.float32)


def prepare_wrap(image):
    a=rgb_array(image);h,w=a.shape[:2]
    if w!=2*h or w%4:raise ValueError('背景パノラマは幅が高さの2倍である必要があります。')
    return np.roll(a,w//2,axis=1)[:,w//4:3*w//4].copy()


def merge_wrap(image,repair):
    a=rgb_array(image);b=rgb_array(repair);h,w=a.shape[:2]
    if b.shape!=(h,w//2,3):raise ValueError('周回の継ぎ目の修復画像の寸法が一致しません。')
    rolled=np.roll(a,w//2,axis=1);x=np.abs(np.arange(w//2)-(w//2-1)/2)/(w//4)
    weight=np.clip((.65-x)/.25,0,1);weight=weight*weight*(3-2*weight)
    rolled[:,w//4:3*w//4]=b*weight[None,:,None]+rolled[:,w//4:3*w//4]*(1-weight[None,:,None])
    return np.roll(rolled,-w//2,axis=1)


def ground_shadows(image,points,floor,origin,radius,*,contact=True):
    a=rgb_array(image).copy();p=np.asarray(points);h,w=a.shape[:2]
    if min(h,w)<16 or radius<=0 or not np.isfinite(p).all():raise ValueError('接地影の範囲が不正です。')
    height=max(float(np.ptp(p[:,1])),1e-5);projected=p[:,[0,2]]+(floor-p[:,1,None])*np.array([-.6,-.35])
    def mask_at(coords):
        ix=np.rint((coords[:,0]-origin[0]+radius)/(2*radius)*(w-1)).astype(int)
        iy=np.rint((coords[:,1]-origin[1]+radius)/(2*radius)*(h-1)).astype(int)
        valid=(ix>=0)&(ix<w)&(iy>=0)&(iy<h);m=np.zeros((h,w),np.float32);m[iy[valid],ix[valid]]=1
        return m
    shadow=gaussian_filter(maximum_filter(mask_at(projected),size=3),max(1.,h*height/(2*radius)*.015))
    darkness=.25*shadow
    if contact:
        feet=p[p[:,1]>=floor-height*.025]
        contact_mask=gaussian_filter(mask_at(feet[:,[0,2]]),max(1.,h*height/(2*radius)*.012))
        if contact_mask.max()>0:darkness+=.35*contact_mask/contact_mask.max()
    a*=1-np.clip(darkness,0,.6)[:,:,None]
    return a


def sample_texture(image,u,v,wrap=False):
    h,w=image.shape[:2];x=(np.mod(u,1) if wrap else np.clip(u,0,1))*w-.5;y=np.clip(v*h-.5,0,h-1)
    if wrap:
        image=np.concatenate([image[:,-1:],image,image[:,:1]],axis=1);x=x+1
    else:x=np.clip(x,0,w-1)
    return cv2.remap(image,x.astype(np.float32),y.astype(np.float32),cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)


def mip_chain(image):
    levels=[rgb_array(image)]
    while min(levels[-1].shape[:2])>1:levels.append(cv2.pyrDown(levels[-1]))
    return levels


def sample_filtered_texture(levels,u,v):
    # Trilinear mip sampling integrates subpixel ground patterns at grazing angles.
    h,w=levels[0].shape[:2]
    uy,ux=np.gradient(u*w);vy,vx=np.gradient(v*h)
    footprint=np.maximum(np.hypot(ux,vx),np.hypot(uy,vy))
    lod=np.clip(np.log2(np.maximum(footprint,1)),0,len(levels)-1);lower=np.floor(lod).astype(np.int32)
    fraction=lod-lower;result=np.zeros((*u.shape,3),np.float32)
    for i,level in enumerate(levels):
        weight=np.where(lower==i,1-fraction,0)+np.where(lower+1==i,fraction,0)
        if np.any(weight>0):result+=sample_texture(level,u,v)*weight[:,:,None]
    return result


def model_inverse(model):
    if not model:return np.eye(3,dtype=np.float32),np.zeros(3,np.float32)
    q=model['quaternion'];rotation=Rotation.from_quat([q[k] for k in 'xyzw']).as_matrix();flip=np.diag([1.,-1.,-1.]);scale=np.array([model['scale'][k] for k in 'xyz']);translation=flip@np.array([model['position'][k] for k in 'xyz'])
    affine=flip@rotation@flip@np.diag(scale);return np.linalg.inv(affine).astype(np.float32),translation.astype(np.float32)


class TextureEnvironment:
    def __init__(self,directory,info,model=None):
        root=Path(directory);self.info=info;self.inverse,self.translation=model_inverse(model)
        self.panorama=np.array(Image.open(root/info['panorama']).convert('RGB'),np.float32)/255
        self.ground=np.array(Image.open(root/info['ground']).convert('RGB'),np.float32)/255
        self.ground_mips=mip_chain(self.ground)
    def render(self,eye,axes,camera,width,height):
        if camera.get('cameraType')!='perspective':raise ValueError('背景と人物には同じ透視投影カメラを使用してください。')
        focal=height/(2*math.tan(math.radians(camera['fov'])/2))*camera.get('zoom',1)
        y,x=np.mgrid[:height,:width];local=np.stack([(x+.5-width/2)/focal,(y+.5-height/2)/focal,np.ones((height,width))],-1).astype(np.float32)
        direction=(local@np.asarray(axes))@self.inverse.T;eye=(np.asarray(eye)-self.translation)@self.inverse.T
        radius=self.info['radius'];a=np.sum(direction*direction,axis=-1);b=np.sum(direction*eye,axis=-1);c=np.sum(eye*eye)-radius**2
        ts=(-b+np.sqrt(np.maximum(b*b-a*c,0)))/a;points=eye+direction*ts[:,:,None];length=np.maximum(np.linalg.norm(points,axis=-1),1e-9)
        u=np.arctan2(points[:,:,0],points[:,:,2])/(2*np.pi)+.5;v=np.arcsin(np.clip(points[:,:,1]/length,-1,1))/np.pi+.5
        color=sample_texture(self.panorama,u,v,True)
        dy=direction[:,:,1];tg=(self.info['floor']-eye[1])/np.where(np.abs(dy)>1e-9,dy,1e-9);gp=eye+direction*tg[:,:,None]
        origin=self.info['ground_origin'];r=self.info['ground_radius'];gu=(gp[:,:,0]-origin[0]+r)/(2*r);gv=(gp[:,:,2]-origin[1]+r)/(2*r)
        radial=np.maximum(np.abs(gu-.5),np.abs(gv-.5))*2;alpha=np.clip((1-radial)/.12,0,1)*((tg>0)&(tg<ts)&(dy>1e-9))
        floor=sample_filtered_texture(self.ground_mips,gu,gv)
        return color*(1-alpha[:,:,None])+floor*alpha[:,:,None]


def safe_elevation(camera,floor,elevation):
    radius=math.sqrt(sum((camera['position'][k]-camera['target'][k])**2 for k in 'xyz'))
    relative=float(floor)+camera['target']['y']
    if not all(math.isfinite(x) for x in [radius,relative,elevation]) or radius<=0 or relative<=0:
        raise ValueError('カメラと床の位置が不正です。')
    # Keep the orbit outside the model. Moving a low camera inward can put it
    # inside outstretched limbs and create extreme perspective distortion.
    minimum=math.degrees(math.asin(-min(1.,relative*.86/radius)))
    return max(elevation,minimum)


def safe_orbit(camera,floor,elevation):
    radius=math.sqrt(sum((camera['position'][k]-camera['target'][k])**2 for k in 'xyz'))
    effective=safe_elevation(camera,floor,elevation)
    margin=1+.12*max(0.,min(1.,-math.sin(math.radians(effective))/.5))
    return radius,math.degrees(2*math.atan(math.tan(math.radians(camera['fov'])/2)*margin))


def textured_camera_grid(metadata,width,height):
    from .scene_geometry import _lookat_camera_info
    camera=metadata['camera'];floor=metadata['texture_environment']['floor'];target=camera['target'];v=[camera['position'][k]-target[k] for k in 'xyz'];yaw=math.atan2(v[0],v[2]);result=[]
    for row,requested in enumerate([-30,0,30]):
        elevation=safe_elevation(camera,floor,requested)
        radius,fov=safe_orbit(camera,floor,elevation);fov=math.degrees(2*math.atan(math.tan(math.radians(fov)/2)*2));e=math.radians(elevation)
        for i in range(73):
            angle=0 if i==72 else i*math.tau/72;t=yaw+angle;eye=[target['x']+radius*math.cos(e)*math.sin(t),target['y']+radius*math.sin(e),target['z']+radius*math.cos(e)*math.cos(t)]
            c=_lookat_camera_info(eye,[target[k] for k in 'xyz'],fov,'cpu');result.append(dict(row=row,index=i,elevation=elevation,azimuth=i*5,camera=c))
    return result
