"""Orient a rigid subject by its broad lower support surface, without deforming it."""
import math
import numpy as np
from scipy.spatial import ConvexHull, QhullError
from scipy.spatial.transform import Rotation


def support_rotation(points, *, fallback_floor=None):
    p=np.asarray(points,dtype=np.float64)
    if p.ndim!=2 or p.shape[1]!=3 or len(p)<30 or not np.isfinite(p).all():
        raise ValueError('人物の接地面を調べるための3D点が不足・破損しています。')
    def unaltered_subject():
        if fallback_floor is None or not math.isfinite(fallback_floor):
            raise ValueError('両側を支える接地面が見つかりません。')
        rotation=np.eye(3)
        return rotation,{'method':'unaltered_subject_estimated_floor','angle_degrees':0.,
                         'normal_before':None,'support_area':0.,'floor':float(fallback_floor),
                         'rotation':rotation.tolist()}

    lo,hi=np.quantile(p[:,1],[.001,.999]);height=hi-lo
    if height<=1e-8:raise ValueError('人物の高さがありません。')
    feet=p[p[:,1]>=hi-height*.18]
    if len(feet)<20:return unaltered_subject()
    normal=np.array([0.,1.,0.]);area=0.
    if np.ptp(feet[:,1])>height*1e-6:
        try:
            hull=ConvexHull(feet)
            _,_,vh=np.linalg.svd(feet[:,[0,2]]-feet[:,[0,2]].mean(0),full_matrices=False)
            along=feet[:,[0,2]]@vh[0];span=np.ptp(along)
            for face,equation in zip(hull.simplices,hull.equations):
                if equation[1]<math.cos(math.radians(25)) or np.ptp(along[face])<span*.45:continue
                tri=feet[face];size=float(np.linalg.norm(np.cross(tri[1]-tri[0],tri[2]-tri[0])))/2
                if size>area:normal=equation[:3];area=size
            if area==0:return unaltered_subject()
        except QhullError:
            return unaltered_subject()
    r=Rotation.align_vectors([[0.,1.,0.]],[normal])[0].as_matrix()
    floor=float((p@r.T)[:,1].max())
    return r,{'method':'rigid_support_plane','angle_degrees':math.degrees(math.acos(np.clip(normal[1],-1,1))),
              'normal_before':normal.tolist(),'support_area':area,'floor':floor,'rotation':r.tolist()}
