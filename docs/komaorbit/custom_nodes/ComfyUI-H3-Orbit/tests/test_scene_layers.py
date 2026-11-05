import importlib.util
import math
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('scene_layers',Path(__file__).parents[1]/'scene_layers.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class LayerTests(unittest.TestCase):
    def test_low_camera_stays_above_floor_for_different_scene_scales(self):
        for scale in [0.1,1,100]:
            camera=dict(position=dict(x=0,y=0,z=.7*scale),target=dict(x=0,y=0,z=0))
            metadata=dict(camera=camera,alignment=dict(orbit_clearance=dict(floor=.16*scale)))
            elevation=m.minimum_elevation(metadata)
            self.assertEqual(elevation,-11)
            self.assertGreater(.7*scale*math.sin(math.radians(elevation)),-.16*scale)
        self.assertEqual(m.minimum_elevation({}),-30)
        with self.assertRaises(ValueError):m.minimum_elevation(dict(camera=camera,alignment=dict(orbit_clearance=dict(floor=-1))))

    def test_projection_preserves_target_scale_and_restores_distant_field(self):
        camera=dict(cameraType='orthographic',position=dict(x=0,y=0,z=.7),target=dict(x=0,y=0,z=0),
                    zoom=24.,frustum=dict(top=5.,bottom=-5.))
        bg=m.background_camera(camera)
        ortho_focal=1000/10*24
        focal=1000/(2*math.tan(math.radians(bg['fov'])/2))
        self.assertAlmostEqual(focal/.7,ortho_focal)
        self.assertLess(focal/7,ortho_focal*.11)
        self.assertEqual(camera['cameraType'],'orthographic')
        self.assertEqual(bg['position'],camera['position'])

    def test_zoom_is_applied_once_and_perspective_preserved(self):
        c=dict(cameraType='orthographic',position=dict(x=0,y=-3,z=4),target=dict(x=0,y=0,z=0),
               zoom=2,frustum=dict(top=1.,bottom=-1.))
        self.assertAlmostEqual(math.tan(math.radians(m.background_camera(c)['fov'])/2),.1)
        p=dict(cameraType='perspective',fov=50,zoom=2)
        self.assertEqual(m.background_camera(p),p)
        with self.assertRaises(ValueError):m.background_camera(dict(c,zoom=0))

    def test_floor_is_perspective_and_body_bounds_stay_independent(self):
        self.assertTrue(hasattr(m,'foreground_mask'),'Contact ground layer selection is missing')
        parts=dict(background=2,subject=3,environment=5,ground=2)
        self.assertEqual(m.component_bounds(parts,12),(2,5))
        self.assertEqual(m.foreground_mask(parts,12).tolist(),[False,False,True,True,True,False,False,False,False,False,False,False])
        self.assertEqual(m.foreground_mask(dict(background=2,subject=3),6).tolist(),[False,False,True,True,True,False])
        with self.assertRaises(ValueError):m.foreground_mask(dict(parts,ground=8),12)

    def test_contact_patch_uses_body_projection_without_changing_body_bounds(self):
        parts=dict(background=0,subject=3,environment=2,ground=4,contact_patch=2)
        self.assertEqual(m.foreground_mask(parts,11).tolist(),[True,True,True,False,False,False,False,False,False,True,True])
        self.assertEqual(m.component_bounds(parts,11),(0,3))
        with self.assertRaises(ValueError):m.foreground_mask(dict(parts,contact_patch=10),11)

    def test_anchored_orthographic_foot_matches_ground_perspective(self):
        self.assertTrue(hasattr(m,'anchor_shift'),'Foot contact projection correction is missing')
        for x,y,z in [(.2,.1,1.),(-.1,.2,.6),(.1,-.2,1.4)]:
            dx,dy=m.anchor_shift(x,y,z,1.,1000.)
            self.assertAlmostEqual(1000*x+dx,1000*x/z)
            self.assertAlmostEqual(1000*y+dy,1000*y/z)

    def test_low_orbit_preserves_body_scale_and_stays_above_floor_at_thirty(self):
        self.assertTrue(hasattr(m,'low_orbit_camera'),'Low-angle camera placement is missing')
        c=dict(position=dict(x=0.,y=0.,z=.6),target=dict(x=0.,y=0.,z=0.),fov=35.,zoom=1)
        result=m.low_orbit_camera(c,.15)
        self.assertTrue(result['h3_subject_fitted'])
        r=math.sqrt(sum(result['position'][k]**2 for k in 'xyz'))
        self.assertLess(r*.5,.15)
        self.assertAlmostEqual(r*math.tan(math.radians(result['fov'])/2),.6*math.tan(math.radians(35)/2))

    def test_invalid_components_fail_without_rendering_empty_subject(self):
        self.assertEqual(m.component_bounds({'background':20,'subject':10},35),(20,30))
        for c in [None,{},dict(background=-1,subject=2),dict(background=1,subject=0),dict(background=1,subject=100),dict(background=True,subject=2)]:
            with self.assertRaises(ValueError):m.component_bounds(c,35)


if __name__=='__main__':unittest.main()
