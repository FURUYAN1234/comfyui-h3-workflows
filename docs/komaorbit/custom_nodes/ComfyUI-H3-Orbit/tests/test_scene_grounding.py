import importlib.util,unittest
from pathlib import Path
import numpy as np

class GroundingTests(unittest.TestCase):
 def module(self):
  p=Path(__file__).parents[1]/'scene_grounding.py';self.assertTrue(p.exists())
  s=importlib.util.spec_from_file_location('grounding',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
 def test_tilted_two_feet_share_ground_without_shape_change(self):
  m=self.module();points=[]
  for cx in [-.55,.55]:
   for x in np.linspace(cx-.2,cx+.2,12):
    for z in np.linspace(-.35,.35,12):points.extend([[x,1,z],[x,.88,z]])
  points.extend([[0,-1,0],[0,0,0]])
  points=np.array(points);a=np.radians(20);tilt=np.array([[1,0,0],[0,np.cos(a),-np.sin(a)],[0,np.sin(a),np.cos(a)]])
  original=points@tilt.T;r,info=m.support_rotation(original)
  fitted=original@r.T
  self.assertLess(abs(np.ptp(fitted[points[:,1]==1,1])),1e-5)
  np.testing.assert_allclose(np.linalg.norm(fitted[1:]-fitted[:-1],axis=1),np.linalg.norm(original[1:]-original[:-1],axis=1),atol=1e-6)
  self.assertAlmostEqual(info['angle_degrees'],20,places=4);self.assertLessEqual(float(fitted[:,1].max()),info['floor']+1e-5)
 def test_upright_and_invalid_inputs(self):
  m=self.module();x,z=np.meshgrid(np.linspace(-1,1,12),np.linspace(-1,1,12));p=np.stack([x.ravel(),np.ones(x.size),z.ravel()],1);p=np.concatenate([p,[[0,-2,0]]]);r,info=m.support_rotation(p);np.testing.assert_allclose(r,np.eye(3),atol=1e-5)
  for p in [np.ones((1,3)),np.full((200,3),np.nan)]:
   with self.assertRaises(ValueError):m.support_rotation(p)
 def test_cropped_subject_keeps_original_orientation_and_scene_floor(self):
  m=self.module()
  x,z=np.meshgrid(np.linspace(-.8,.8,15),np.linspace(-.4,.4,12))
  # A cropped foreground has one slanted lower edge instead of two soles.
  y=1+.8*x
  edge=np.column_stack([x.ravel(),y.ravel(),z.ravel()])
  points=np.concatenate([edge,edge-[0,.12,0],[[0,-1,0]]])
  floor=float(np.quantile(points[:,1],.998))
  with self.assertRaisesRegex(ValueError,'接地面'):
   m.support_rotation(points)
  r,info=m.support_rotation(points,fallback_floor=floor)
  np.testing.assert_array_equal(r,np.eye(3))
  self.assertEqual(info['method'],'unaltered_subject_estimated_floor')
  self.assertEqual(info['floor'],floor)
  with self.assertRaises(ValueError):
   m.support_rotation(points,fallback_floor=float('nan'))
 def test_planar_cutout_preserves_pose_with_existing_floor(self):
  m=self.module()
  x,y=np.meshgrid(np.linspace(-1,1,20),np.linspace(-1,1,20))
  points=np.column_stack([x.ravel(),y.ravel(),np.zeros(x.size)])
  r,info=m.support_rotation(points,fallback_floor=1.)
  np.testing.assert_array_equal(r,np.eye(3))
  self.assertEqual(info['method'],'unaltered_subject_estimated_floor')
 def test_corruption_is_not_hidden_by_fallback(self):
  m=self.module()
  for points in [np.full((100,3),np.nan),np.ones((100,3)),np.ones((1,3))]:
   with self.assertRaises(ValueError):
    m.support_rotation(points,fallback_floor=1.)
if __name__=='__main__':unittest.main()
