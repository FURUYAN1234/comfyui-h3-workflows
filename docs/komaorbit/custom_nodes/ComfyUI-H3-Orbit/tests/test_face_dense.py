import unittest
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).parents[1]))
from face_dense import quadratic_surface,validate_dense_faces

class DenseFaceTests(unittest.TestCase):
    def fixture(self):
        yy,xx=np.mgrid[10:30,10:30];uv=np.c_[xx.ravel(),yy.ravel()].astype(float);n=len(uv)
        face=np.c_[uv/100,np.ones(n)];body=np.tile([4.,4.,4.],(1001,1));means=np.r_[face,body].astype(np.float32)
        b=dict(means=means,quats=np.tile([1.,0.,0.,0.],(len(means),1)).astype(np.float32),scales=np.full((len(means),3),.006,np.float32),opacities=np.full(len(means),.99,np.float32),sh=np.zeros((len(means),1,3),np.float32))
        selected=np.arange(len(means))<n;kept=np.flatnonzero(~selected);a={k:np.r_[v[kept],v[:n]] for k,v in b.items()};a['means'][len(kept):,2]-=.00015
        mask=np.zeros((40,40),np.float32);mask[10:30,10:30]=1
        p=dict(selected=selected,kept=kept,pixels=uv,regions=np.zeros(n,int),view=np.eye(4),intrinsic=np.diag([100.,100.,1.]),masks=mask[None],extents=np.array([19.]),origins=np.array([[19.5,19.5]]),coefficients=np.array([[1.,0.,0.,0.,0.,0.]]))
        return b,a,p
    def test_valid_fixed_source_surface(self):
        validate_dense_faces(*self.fixture())
    def test_subpixel_face_edge_is_valid(self):
        b,a,p=self.fixture();p['pixels'][-1,0]=29.5;a['means'][-1,0]=.295
        validate_dense_faces(b,a,p)
    def test_actual_outside_face_is_rejected(self):
        b,a,p=self.fixture();p['pixels'][-1,0]=30.5;a['means'][-1,0]=.305
        with self.assertRaisesRegex(ValueError,'顔の外'):validate_dense_faces(b,a,p)
    def test_body_change_is_rejected(self):
        b,a,p=self.fixture();a['means'][0,0]+=.01
        with self.assertRaisesRegex(ValueError,'顔以外'):validate_dense_faces(b,a,p)
    def test_eye_displacement_is_rejected(self):
        b,a,p=self.fixture();a['means'][-1,0]+=.001
        with self.assertRaisesRegex(ValueError,'位置'):validate_dense_faces(b,a,p)
    def test_protruding_depth_is_rejected(self):
        b,a,p=self.fixture();a['means'][-1,2]+=.01
        with self.assertRaisesRegex(ValueError,'深度'):validate_dense_faces(b,a,p)
    def test_foreshortened_face_keeps_real_depth_slope(self):
        y,x=np.mgrid[:40,:40];d=1+x/50
        origin,extent,c=quadratic_surface(d,np.ones_like(d),100)
        self.assertAlmostEqual(c[1]/extent,.02,places=5)
    def test_robust_surface_rejects_missing_depth(self):
        with self.assertRaises(ValueError):quadratic_surface(np.zeros((40,40)),np.ones((40,40)),100)
    def test_robust_surface_ignores_sparse_depth_outliers(self):
        y,x=np.mgrid[:40,:40];d=1+.02*((x-20)/40)**2;d[::9,::9]=10
        origin,extent,c=quadratic_surface(d,np.ones_like(d),100)
        self.assertLess(abs(c[0]-1),.002)
if __name__=='__main__':unittest.main()
