import importlib.util
from pathlib import Path
import unittest
import numpy as np

PATH=Path(__file__).parents[1]/'scene_surfaces.py'

class SurfaceTests(unittest.TestCase):
 def module(self):
  self.assertTrue(PATH.exists(),'High-resolution surface projection is missing')
  spec=importlib.util.spec_from_file_location('scene_surfaces',PATH);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
 def sources(self):
  images=np.ones((1,64,64,3),np.float32)*[.8,.6,.3];images[:,:32]=[.1,.7,.9]
  masks=np.zeros((1,64,64),np.float32)
  camera=np.eye(4)[None];k=np.array([[[40.,0,32],[0,40,32],[0,0,1]]])
  return images,masks,camera,k
 def test_projected_edges_stay_sharp_and_masked_foreground_is_rejected(self):
  m=self.module();im,mask,cam,k=self.sources();im[:,:32]=[0,0,0];im[:,32:]=[1,1,1]
  points=np.array([[0,-.2,2],[0,.2,2],[.8,.8,2]],np.float32);mask[:,45:51,45:51]=1
  rgb,valid=m.project_colors(points,im,mask,cam,k,np.zeros(3),np.eye(3))
  np.testing.assert_allclose(rgb[:2],[[0,0,0],[1,1,1]],atol=1e-6);self.assertEqual(valid.tolist(),[True,True,False])
 def test_ground_lies_at_soles_has_contact_shadows_and_rejects_sky(self):
  m=self.module();im,mask,cam,k=self.sources()
  # Upright Y-down scene: camera at y=-1 looking along +Z; ground projects into lower half.
  cam[0,1,3]=-1;feet=np.array([[-.12,0,2],[.12,0,2]],np.float32)
  points=np.concatenate([feet,np.array([[0,-1,2],[0,-.5,2]],np.float32)])
  ground,info=m.contact_ground(points,im,mask,cam,k,np.zeros(3),np.eye(3),1.,resolution=64)
  np.testing.assert_allclose(ground['means'][:,1],info['floor'],atol=1e-6)
  self.assertLess(abs(info['floor']),.01);self.assertGreater(info['observed_fraction'],0)
  rgb=ground['sh'][:,0].numpy()*.28209479177387814+.5
  self.assertGreater(float(rgb[:,0].mean()),float(rgb[:,2].mean()))
  self.assertGreater(info['contact_shadow_points'],0)
 def test_contact_patch_is_local_translucent_and_keeps_fixed_floor(self):
  m=self.module();im,mask,cam,k=self.sources();cam[0,1,3]=-1
  points=np.array([[-.12,0,2],[.12,0,2],[0,-1,2],[0,-.5,2]],np.float32)
  ground,info=m.contact_ground(points,im,mask,cam,k,np.zeros(3),np.eye(3),1.,resolution=256)
  patch=m.contact_patch(points,ground,info['floor'])
  self.assertGreater(len(patch['means']),0);self.assertLess(len(patch['means']),len(ground['means'])//4)
  np.testing.assert_allclose(patch['means'][:,1],info['floor'],atol=1e-6)
  self.assertGreater(float(patch['opacities'].max()),.5);self.assertLess(float(patch['opacities'].min()),.1)
 def test_inconsistent_source_arrays_fail_before_projection(self):
  m=self.module();im,mask,cam,k=self.sources()
  with self.assertRaises(ValueError):m.project_colors(np.zeros((2,3)),im,mask[:,:32],cam,k,np.zeros(3),np.eye(3))

if __name__=='__main__':unittest.main()