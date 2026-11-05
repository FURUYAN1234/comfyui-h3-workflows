import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).parents[1]))
from face_restore import match_faces, warp_face, validate_restored_surface, match_face_boxes, project_source_face, valid_face, detect_render_faces, smooth_face_depth, smooth_face_regions

class FaceCorrespondenceTests(unittest.TestCase):
 def face(self,x,confidence=.95):
  t=np.linspace(0,2*np.pi,28,endpoint=False);k=np.stack([x+20*np.cos(t),50+25*np.sin(t),np.full(28,confidence)],1)
  return {'bbox':np.array([x-25,20,x+25,80,.99]),'keypoints':k}
 def test_reordered_faces_match_by_location(self):
  a=[self.face(60),self.face(180)];b=[self.face(185),self.face(65)]
  self.assertEqual(match_faces(a,b,(256,128),(256,128)),[(0,1),(1,0)])
 def test_original_crop_and_padding_do_not_lose_high_resolution_faces(self):
  a=[self.face(60),self.face(180)];b=[self.face(140),self.face(260)]
  self.assertEqual(match_faces(a,b,(256,128),(400,128),align_frame=True),[(0,0),(1,1)])
 def test_ambiguous_duplicate_faces_do_not_project(self):
  a=[self.face(60),self.face(61)];b=[self.face(60),self.face(61)]
  self.assertEqual(match_faces(a,b,(256,128),(256,128)),[])
 def test_missing_low_confidence_or_invalid_faces_do_not_project(self):
  self.assertEqual(match_faces([],[],(256,128),(256,128)),[])
  self.assertEqual(match_faces([self.face(60,.2)],[self.face(60)],(256,128),(256,128)),[])
  bad=self.face(60);bad['keypoints'][0,0]=np.nan
  self.assertEqual(match_faces([bad],[self.face(60)],(256,128),(256,128)),[])
 def test_identity_warp_preserves_source_and_leaves_outside_mask_unchanged(self):
  y,x=np.mgrid[:128,:256];source=np.stack([x/256,y/128,np.full_like(x,.5,dtype=float)],2).astype(np.float32);base=np.zeros_like(source);original=source.copy()
  face=self.face(60);target,mask=warp_face(source,base,face,face)
  np.testing.assert_array_equal(source,original);np.testing.assert_array_equal(target[mask==0],base[mask==0]);self.assertGreater(mask.sum(),500)
  self.assertTrue(np.isfinite(target).all())

class SourceProjectionTests(FaceCorrespondenceTests):
 def test_projection_retains_source_geometry_without_using_broken_eye_landmarks(self):
  source=np.ones((128,256,3),np.float32);base=np.zeros_like(source);sf=self.face(60);tf=self.face(160,.1)
  self.assertFalse(valid_face(tf));target,mask,points=project_source_face(source,base,sf,tf)
  np.testing.assert_allclose(points-sf['keypoints'][:,:2],np.tile([100,0],(28,1)),atol=1e-5)
  np.testing.assert_array_equal(target[mask==0],base[mask==0]);self.assertGreater(mask.sum(),100)
  with self.assertRaises(ValueError):project_source_face(source,base,self.face(60,.1),tf)
 def test_box_gate_rejects_far_ambiguous_and_low_confidence_candidates(self):
  sf=self.face(60);tf=self.face(65,.1)
  self.assertEqual(match_face_boxes([sf],[tf],(256,128),(256,128)),[(0,0)])
  self.assertEqual(match_face_boxes([sf],[self.face(180,.1)],(256,128),(256,128)),[])
  self.assertEqual(match_face_boxes([sf],[tf,self.face(66,.1)],(256,128),(256,128)),[])
  tf['bbox'][4]=.1;self.assertEqual(match_face_boxes([sf],[tf],(256,128),(256,128)),[])
 def test_tiled_detector_restores_global_coordinates_and_deduplicates(self):
  def detect(image):
   if image.shape[0]==256:return []
   return [self.face(60)] if image[0,0,0]==1 else []
  image=np.zeros((256,256,3));image[64,64,0]=1
  faces=detect_render_faces(detect,image);self.assertEqual(len(faces),1)
  np.testing.assert_allclose(faces[0]['bbox'][:4],[99,84,149,144])



class DepthRegularizationTests(unittest.TestCase):
 def test_depth_smoothing_preserves_tangent_coordinates_and_body(self):
  y,x=np.mgrid[:64,:64];depth=np.ones((64,64),np.float32);mask=np.zeros((64,64),np.float32);mask[16:48,16:48]=1
  means=np.array([[-.8,-.8,1.2],[.8,-.8,.8],[-.8,.8,1.1],[.8,.8,.9],[0,3,7]],np.float32)
  selected=np.array([True,True,True,True,False]);k=np.array([[10,0,32],[0,10,32],[0,0,1]],np.float32)
  delta=smooth_face_depth(means,selected,np.eye(4,dtype=np.float32),k,depth,mask)
  np.testing.assert_array_equal(delta[:,:2],0);np.testing.assert_array_equal(delta[~selected],0)
  np.testing.assert_allclose((means+delta)[selected,2],1,atol=1e-6)
 def test_separate_people_keep_independent_depths_and_single_case_is_identical(self):
  depth=np.ones((64,128),np.float32);depth[:,64:]=3
  a=np.zeros_like(depth);a[16:48,16:48]=1;b=np.zeros_like(depth);b[16:48,80:112]=1
  means=np.array([[-.8,-.8,1.2],[.8,.8,.8],[5.6,-.8,3.2],[7.2,.8,2.8],[0,3,7]],np.float32)
  selected=np.array([True,True,True,True,False]);k=np.array([[10,0,32],[0,10,32],[0,0,1]],np.float32);view=np.eye(4,dtype=np.float32)
  delta=smooth_face_regions(means,selected,view,k,depth,[a,b])
  np.testing.assert_allclose((means+delta)[:,2],[1,1,3,3,7],atol=1e-6)
  np.testing.assert_array_equal(smooth_face_regions(means,selected,view,k,depth,[a]),smooth_face_depth(means,selected,view,k,depth,a))
 def test_provenance_rejects_sideways_or_excessive_face_change(self):
  means=np.array([[0,0,0],[.1,0,0],[.2,0,0]],np.float32)
  b={'means':means,'quats':np.tile([1.,0,0,0],(3,1)).astype(np.float32),'scales':np.ones((3,3),np.float32),'opacities':np.ones(3,np.float32),'sh':np.zeros((3,1,3),np.float32)}
  selected=np.array([False,True,True]);parents=np.array([0]+[1]*64+[2]*64);a={k:v[parents].copy() for k,v in b.items()};a['scales'][1:]*=.65**3;a['opacities'][1:]*=.65**3
  offsets=np.zeros((3,3),np.float32);offsets[1:,2]=.02;a['means']+=offsets[parents]
  validate_restored_surface(b,a,parents,selected,surface_offsets=offsets,surface_basis=np.eye(3))
  for bad in [np.array([[0,0,0],[.01,0,.02],[0,0,.02]]),np.array([[0,0,0],[0,0,.04],[0,0,.02]])]:
   with self.assertRaises(ValueError):validate_restored_surface(b,a,parents,selected,surface_offsets=bad,surface_basis=np.eye(3))

class SurfaceValidationTests(unittest.TestCase):
 def setUp(self):
  self.base={'means':np.zeros((2,3),np.float32),'quats':np.tile([1.,0,0,0],(2,1)).astype(np.float32),'scales':np.ones((2,3),np.float32),'opacities':np.ones(2,np.float32),'sh':np.zeros((2,1,3),np.float32)}
  self.parents=np.array([0]+[1]*64);self.selected=np.array([False,True]);self.out={k:v[self.parents].copy() for k,v in self.base.items()};self.out['scales'][1:]*=.65**3;self.out['opacities'][1:]*=.65**3
 def test_local_subdivision_preserves_body_and_parent_centers(self):
  validate_restored_surface(self.base,self.out,self.parents,self.selected)
 def test_body_move_or_missing_parent_rejected(self):
  bad={k:v.copy() for k,v in self.out.items()};bad['means'][0,0]=.01
  with self.assertRaises(ValueError):validate_restored_surface(self.base,bad,self.parents,self.selected)
  bad={k:v.copy() for k,v in self.out.items()};bad['means'][1:,0]=.01
  with self.assertRaises(ValueError):validate_restored_surface(self.base,bad,self.parents,self.selected)
 def test_nonface_sr_color_bound_is_enforced(self):
  self.out['sh'][0,0]=.1
  validate_restored_surface(self.base,self.out,self.parents,self.selected,allow_sr_colors=True)
  self.out['sh'][0,0]=1
  with self.assertRaises(ValueError):validate_restored_surface(self.base,self.out,self.parents,self.selected,allow_sr_colors=True)

if __name__=='__main__':unittest.main()
