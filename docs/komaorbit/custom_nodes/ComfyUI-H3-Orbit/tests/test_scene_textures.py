import importlib.util,unittest,tempfile,math
from pathlib import Path
import numpy as np
from PIL import Image
s=importlib.util.spec_from_file_location('scene_textures',Path(__file__).parents[1]/'scene_textures.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class TextureTests(unittest.TestCase):
 def test_floor_and_background_use_one_perspective_camera(self):
  with tempfile.TemporaryDirectory() as d:
   Image.new('RGB',(32,16),(255,0,0)).save(Path(d)/'p.png');Image.new('RGB',(32,32),(0,255,0)).save(Path(d)/'g.png')
   e=m.TextureEnvironment(d,dict(panorama='p.png',ground='g.png',radius=10,floor=1,ground_origin=[0,0],ground_radius=9))
   c=dict(cameraType='perspective',fov=60,zoom=1);a=e.render([0,0,-3],np.eye(3),c,64,64)
   np.testing.assert_allclose(a[0,32],[1,0,0],atol=1e-6);np.testing.assert_allclose(a[-1,32],[0,1,0],atol=1e-6)
   with self.assertRaises(ValueError):e.render([0,0,-3],np.eye(3),dict(c,cameraType='orthographic'),64,64)
 def test_wrap_repair_preserves_unaffected_front_and_places_join_inside_repair(self):
  a=np.zeros((32,64,3),np.float32);a[:,32:]=1;b=np.ones((32,32,3),np.float32)*.5
  self.assertEqual(m.prepare_wrap(a).shape,(32,32,3));r=m.merge_wrap(a,b)
  np.testing.assert_array_equal(r[:,20:44],a[:,20:44]);np.testing.assert_allclose(r[:,0],r[:,-1],atol=1e-6)
  with self.assertRaises(ValueError):m.merge_wrap(a,b[:16])
 def test_low_camera_stays_above_floor_and_preserves_target_scale(self):
  c=dict(position=dict(x=0,y=0,z=.6),target=dict(x=0,y=0,z=0),fov=35)
  for a in [-30,0,30]:
   r,f=m.safe_orbit(c,.15,a);self.assertAlmostEqual(r,.6)
   actual=m.safe_elevation(c,.15,a);self.assertGreater(r*math.sin(math.radians(actual)),-.15)
   self.assertGreaterEqual(actual,a)
   self.assertGreaterEqual(r*math.tan(math.radians(f)/2)+1e-9,.6*math.tan(math.radians(35)/2))
 def test_minified_ground_filters_aliasing_but_preserves_close_detail(self):
  yy,xx=np.mgrid[:256,:256];a=np.repeat(((xx+yy)%2)[:,:,None],3,axis=2).astype(np.float32)
  u,v=np.meshgrid(np.arange(8)/8+.5/256,np.arange(8)/8+.5/256)
  raw=m.sample_texture(a,u,v);filtered=m.sample_filtered_texture(m.mip_chain(a),u,v)
  self.assertLess(float(raw.mean()),.01);np.testing.assert_allclose(filtered,.5,atol=.01)
  u,v=np.meshgrid(.4+np.arange(8)/4096,.4+np.arange(8)/4096)
  np.testing.assert_allclose(m.sample_filtered_texture(m.mip_chain(a),u,v),m.sample_texture(a,u,v),atol=1e-6)
 def test_contact_shadow_is_grounded_and_not_a_blanket_darken(self):
  p=np.array([[-.1,1,0],[.1,1,0],[0,0,0]],np.float32);a=np.ones((256,256,3),np.float32)
  b=m.ground_shadows(a,p,1,[0,0],2)
  self.assertLess(b[128,134,0],.9);self.assertEqual(float(b[0,0,0]),1.)
 def test_alpha_is_flattened_and_corrupt_texture_rejected(self):
  a=np.ones((8,8,4),np.float32);a[:,:,:3]=0;a[:,:,3]=.5;np.testing.assert_allclose(m.rgb_array(a),.5)
  with self.assertRaises(ValueError):m.rgb_array(np.full((8,8,3),np.nan))
 def test_uncertain_support_omits_contact_shadow(self):
  p=np.array([[-.1,1,0],[.1,1,0],[0,0,0]],np.float32)
  image=np.ones((256,256,3),np.float32)
  with_contact=m.ground_shadows(image,p,1,[0,0],2)
  projected=m.ground_shadows(image,p,1,[0,0],2,contact=False)
  self.assertTrue(np.all(projected>=with_contact))
  self.assertGreater(float(projected[128,134,0]),float(with_contact[128,134,0]))
  self.assertLess(float(projected.min()),1.)
if __name__=='__main__':unittest.main()
