import importlib.util,unittest
from pathlib import Path
import numpy as np
s=importlib.util.spec_from_file_location('subject_detail',Path(__file__).parents[1]/'subject_detail.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class DetailTests(unittest.TestCase):
 def test_edges_gain_contrast_without_geometry_or_input_mutation(self):
  y,x=np.mgrid[:20,:20];points=np.stack([x.ravel(),y.ravel(),np.zeros(400)],1).astype(np.float32);original=points.copy()
  colors=np.repeat(np.where(x.ravel()<10,.35,.65)[:,None],3,1).astype(np.float32);backup=colors.copy()
  result=m.enhance_surface_colors(points,colors,np.ones(400))
  self.assertLess(result[10*20+9,0],colors[10*20+9,0]);self.assertGreater(result[10*20+10,0],colors[10*20+10,0])
  np.testing.assert_array_equal(points,original);np.testing.assert_array_equal(colors,backup)
  self.assertLessEqual(float(np.abs(result-colors).max()),.100001)
 def test_uniform_surface_and_low_opacity_remain_unchanged(self):
  p=np.random.default_rng(1).normal(size=(100,3));c=np.full((100,3),.4,np.float32);o=np.ones(100);o[0]=0;c[0]=.8
  result=m.enhance_surface_colors(p,c,o);np.testing.assert_allclose(result,c,atol=1e-6)
  np.testing.assert_array_equal(m.enhance_surface_colors(p,c,o,0),c)
 def test_bad_inputs_rejected(self):
  with self.assertRaises(ValueError):m.enhance_surface_colors(np.full((10,3),np.nan),np.ones((10,3)),np.ones(10))

class MultiViewDetailTests(unittest.TestCase):
 def test_views_are_distinct_and_centered_with_margin(self):
  p=np.random.default_rng(4).normal(size=(300,3)).astype(np.float32)*[.1,.2,.08]
  cameras,intrinsic=m.detail_cameras(p,np.ones(300),np.eye(3),1024)
  self.assertEqual(cameras.shape,(10,4,4))
  self.assertEqual(len({x.tobytes() for x in cameras}),10)
  for view in cameras:
   np.testing.assert_allclose(view[:3,:3]@view[:3,:3].T,np.eye(3),atol=1e-6)
   q=p@view[:3,:3].T+view[:3,3]
   uv=q[:,:2]*intrinsic[0,0]+512
   self.assertTrue((q[:,2]>0).all())
   self.assertGreater(float(np.mean((uv>0)&(uv<1024))),.98)
 def test_camera_rejects_nan_and_empty_subject(self):
  for p,o in [(np.full((20,3),np.nan),np.ones(20)),(np.zeros((20,3)),np.zeros(20))]:
   with self.assertRaises(ValueError):m.detail_cameras(p,o,np.eye(3),1024)
 def test_result_validation_preserves_geometry_and_bounds_color(self):
  base={'means':np.ones((20,3),np.float32),'quats':np.ones((20,4),np.float32),'scales':np.ones((20,3),np.float32),'opacities':np.ones(20,np.float32),'sh':np.zeros((20,1,3),np.float32)}
  good={k:v.copy() for k,v in base.items()};good['sh']+=.1
  m.validate_detail_result(base,good)
  bad={k:v.copy() for k,v in good.items()};bad['means'][0,0]+=.001
  with self.assertRaises(ValueError):m.validate_detail_result(base,bad)
  bad={k:v.copy() for k,v in good.items()};bad['sh'][0,0]=np.nan
  with self.assertRaises(ValueError):m.validate_detail_result(base,bad)
  bad={k:v.copy() for k,v in good.items()};bad['sh'][0,0]=4
  with self.assertRaises(ValueError):m.validate_detail_result(base,bad)

if __name__=='__main__':unittest.main()
