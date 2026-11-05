"""Sharp fixed environment and a contact surface in the subject's coordinates."""
import math
import numpy as np
import torch
from scipy.ndimage import maximum_filter, map_coordinates, distance_transform_edt, gaussian_filter
from scipy.spatial.transform import Rotation
from scipy.spatial import cKDTree

SH_C0=.28209479177387814


def project_colors(points, images, masks, cameras, intrinsics, center, upright, ground=False, return_source=False):
    """Keep the best unmasked source, feathering only narrow view boundaries."""
    points,images,masks,cameras,intrinsics,center,upright=[np.asarray(x) for x in
        (points,images,masks,cameras,intrinsics,center,upright)]
    if (points.ndim!=2 or points.shape[1]!=3 or images.ndim!=4 or images.shape[-1]!=3
            or masks.shape!=images.shape[:3] or cameras.shape!=(len(images),4,4)
            or intrinsics.shape!=(len(images),3,3) or center.shape!=(3,) or upright.shape!=(3,3)):
        raise ValueError('背景の画像・マスク・カメラの対応が不正です。')
    if not all(np.isfinite(x).all() for x in (points,images,masks,cameras,intrinsics,center,upright)):
        raise ValueError('背景の画像・位置情報に非有限の値があります。')
    h,w=images.shape[1:3]
    color=np.zeros((len(points),3),np.float32);score=np.full(len(points),-np.inf,np.float32)
    second_color=color.copy();second_score=score.copy();owner=np.full(len(points),-1,np.int16)
    world=points@upright+center
    for source_index,(im,mask,pose,k) in enumerate(zip(images,masks,cameras,intrinsics)):
        expanded=maximum_filter(mask,size=9)
        foreground_y=np.where(mask>.5)[0]
        lower_limit=np.quantile(foreground_y,.99) if len(foreground_y) else h*.52
        for lo in range(0,len(points),131072):
            hi=min(lo+131072,len(points));p=(world[lo:hi]-pose[:3,3])@pose[:3,:3]
            z=p[:,2];uv=p[:,:2]/np.maximum(z[:,None],1e-8)
            uv=uv*k.diagonal()[:2]+k[:2,2]
            valid=(z>1e-5)&(uv[:,0]>=1)&(uv[:,0]<w-2)&(uv[:,1]>=1)&(uv[:,1]<h-2)
            px=np.clip(uv[:,0],0,w-1);py=np.clip(uv[:,1],0,h-1)
            valid &= map_coordinates(expanded,[py,px],order=0,mode='nearest')<.1
            if ground:valid &= py>=lower_limit
            quality=1-((px-w/2)/(w/2))**2-((py-h/2)/(h/2))**2*.35
            take=valid&(quality>second_score[lo:hi])
            if take.any():
                idx=np.where(take)[0];dest=lo+idx
                rgb=np.stack([map_coordinates(im[...,c],[py[idx],px[idx]],order=1,mode='nearest') for c in range(3)],1)
                best=quality[idx]>score[dest]
                second_color[dest[best]]=color[dest[best]];second_score[dest[best]]=score[dest[best]]
                color[dest[best]]=rgb[best];score[dest[best]]=quality[idx[best]];owner[dest[best]]=source_index
                second_color[dest[~best]]=rgb[~best];second_score[dest[~best]]=quality[idx[~best]]
    both=np.isfinite(second_score)
    mix=np.zeros(len(points),np.float32)
    mix[both]=.5*np.clip(1-(score[both]-second_score[both])/.06,0,1)
    color=color*(1-mix[:,None])+second_color*mix[:,None]
    return (color,np.isfinite(score),owner) if return_source else (color,np.isfinite(score))


def _ambient(images,masks,lower):
    h=images.shape[1];region=np.zeros(masks.shape,bool)
    if lower:region[:,int(h*.88):]=True
    else:region[:,:max(1,int(h*.12))]=True
    valid=region&(masks<.1)
    if not valid.any():valid=masks<.1
    if not valid.any():raise ValueError('背景として使える画素がありません。')
    return np.median(images[valid],axis=0)


def textured_environment(images,masks,cameras,intrinsics,center,upright,radius,count=1048576):
    if count<100 or radius<=0 or not math.isfinite(radius):raise ValueError('背景の投影範囲が不正です。')
    i=np.arange(count,dtype=np.float64);ny=1-2*(i+.5)/count
    theta=i*(math.pi*(3-math.sqrt(5)));r=np.sqrt(1-ny*ny)
    normal=np.stack([np.cos(theta)*r,ny,np.sin(theta)*r],1).astype(np.float32)
    means=normal*radius
    rgb,observed=project_colors(means,images,masks,cameras,intrinsics,center,upright)
    blend=np.clip((normal[:,1]+.1)/.4,0,1);blend=blend**2*(3-2*blend)
    fallback=_ambient(images,masks,False)*(1-blend[:,None])+_ambient(images,masks,True)*blend[:,None]
    rgb[~observed]=fallback[~observed]
    if observed.any() and (~observed).any():
        distance,nearest=cKDTree(normal[observed]).query(normal[~observed],k=1,workers=2)
        weight=np.clip(1-distance/.09,0,1);weight=weight**2*(3-2*weight)
        rgb[~observed]=rgb[observed][nearest]*weight[:,None]+fallback[~observed]*(1-weight[:,None])
    tangent=np.cross(np.broadcast_to([0.,1.,0.],normal.shape),normal)
    tangent/=np.linalg.norm(tangent,axis=1,keepdims=True);vertical=np.cross(normal,tangent)
    q=Rotation.from_matrix(np.stack([tangent,vertical,normal],-1)).as_quat().astype(np.float32)
    spacing=radius*math.sqrt(4*math.pi/count)
    result={'means':torch.from_numpy(means),'scales':torch.tensor([spacing*.56,spacing*.56,radius*.00001]).repeat(count,1),
        'quats':torch.from_numpy(np.concatenate([q[:,3:],q[:,:3]],1)),
        'opacities':torch.full((count,),.98),'sh':torch.from_numpy(((rgb-.5)/SH_C0)[:,None].astype(np.float32))}
    return result,{'method':'high_resolution_source_projection','point_count':count,'radius':float(radius),'observed_fraction':float(observed.mean()),'source_size':[images.shape[2],images.shape[1]],'blend_views':'narrow_seams_only'}


def contact_ground(subject_points,images,masks,cameras,intrinsics,center,upright,eye_distance,resolution=768):
    points=np.asarray(subject_points)
    if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all() or len(points)<2 or eye_distance<=0 or resolution<16:
        raise ValueError('足元の位置・床の範囲が不正です。')
    floor=float(np.quantile(points[:,1],.998));height=max(float(np.ptp(points[:,1])),1e-5)
    feet=points[points[:,1]>=floor-height*.025]
    origin=np.median(feet[:,[0,2]],axis=0);radius=eye_distance*1.6
    axis=np.linspace(-radius,radius,resolution,dtype=np.float32);x,z=np.meshgrid(axis+origin[0],axis+origin[1]);spacing=float(axis[1]-axis[0])
    means=np.stack([x,np.full_like(x,floor),z],-1).reshape(-1,3)
    rgb,observed=project_colors(means,images,masks,cameras,intrinsics,center,upright,ground=True)
    if observed.any():
        # Fill only unobserved parts of this fixed floor from neighbouring valid floor texels.
        valid=observed.reshape(resolution,resolution)
        indices=distance_transform_edt(~valid,return_distances=False,return_indices=True)
        rgb=rgb.reshape(resolution,resolution,3);rgb[~valid]=rgb[indices[0][~valid],indices[1][~valid]];rgb=rgb.reshape(-1,3)
    else:rgb[:]=_ambient(images,masks,True)
    footprint=np.zeros((resolution,resolution),np.float32)
    fx=np.clip(np.rint((feet[:,0]-origin[0]+radius)/spacing).astype(int),0,resolution-1)
    fz=np.clip(np.rint((feet[:,2]-origin[1]+radius)/spacing).astype(int),0,resolution-1)
    footprint[fz,fx]=1
    shadow=gaussian_filter(footprint,max(1.,height*.02/spacing))
    if shadow.max()>0:shadow/=shadow.max()
    rgb*=1-.48*shadow.reshape(-1,1)
    radial=np.maximum(np.abs(x-origin[0]),np.abs(z-origin[1]))/radius
    opacity=.98*np.clip((1-radial)/.2,0,1)
    count=len(means);q=torch.tensor([math.sqrt(.5),math.sqrt(.5),0.,0.]).repeat(count,1)
    result={'means':torch.from_numpy(means),'scales':torch.tensor([spacing*.6,spacing*.6,spacing*.015]).repeat(count,1),
        'quats':q,'opacities':torch.from_numpy(opacity.reshape(-1).astype(np.float32)),
        'sh':torch.from_numpy(((rgb-.5)/SH_C0)[:,None].astype(np.float32))}
    return result,{'method':'source_projected_contact_ground','floor':floor,'radius':radius,'point_count':count,
        'observed_fraction':float(observed.mean()),'contact_shadow_points':len(feet),'resolution':resolution,
        'anchor':[float(origin[0]),floor,float(origin[1])]}

def contact_patch(subject_points,ground,floor):
    """A small feathered part of the floor shares each shoe's projection."""
    points=np.asarray(subject_points);height=max(float(np.ptp(points[:,1])),1e-5)
    feet=points[points[:,1]>=floor-height*.025]
    distance,_=cKDTree(feet[:,[0,2]]).query(ground['means'][:,[0,2]].numpy(),workers=2)
    weight=np.exp(-.5*(distance/(height*.012))**2)
    keep=weight>.01
    patch={k:v[keep].clone() for k,v in ground.items()}
    patch['opacities']*=torch.from_numpy(weight[keep].astype(np.float32))
    return patch
