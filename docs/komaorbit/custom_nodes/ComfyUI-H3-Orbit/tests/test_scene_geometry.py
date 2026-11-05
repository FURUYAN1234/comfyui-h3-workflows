import importlib.util
import math
from pathlib import Path
import sys
import unittest

import torch

PACK = Path(__file__).parents[1]
sys.path.insert(0, str(PACK.parents[1]))
spec = importlib.util.spec_from_file_location('scene_geometry_test', PACK / 'scene_geometry.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PlacementTests(unittest.TestCase):
    def test_reopening_a_fitted_close_orbit_does_not_change_saved_framing(self):
        camera=module._lookat_camera_info([0,0,.7],[0,0,0],35.,'cpu')
        points=torch.rand((1000,3),generator=torch.Generator().manual_seed(42))*.3-.15
        values={'means':points,'opacities':torch.ones(1000)};parts={'background':0,'subject':1000}
        fitted=module.fit_subject_camera(values,camera,parts,1672,941)
        close=dict(fitted,position=dict(x=0.,y=0.,z=.25),fov=69.,h3_subject_fitted=True)
        self.assertEqual(module.fit_subject_camera(values,close,parts,1672,941),close)

    def test_orbit_uses_one_parallel_scale_for_near_feet_and_far_head(self):
        camera=module._lookat_camera_info([0.,0.,.7],[0,0,0],35.,'cpu')
        views=module.rigid_camera_grid(camera,600,338)
        for view in views:
            c=view['camera']
            self.assertEqual(c['cameraType'],'orthographic')
            frustum=c['frustum']
            self.assertAlmostEqual((frustum['right']-frustum['left'])/(frustum['top']-frustum['bottom']),600/338)
            self.assertEqual(frustum,views[0]['camera']['frustum'])
        parallel=module.proportion_camera(camera,600,338)
        self.assertEqual(camera['cameraType'],'perspective')
        grid=module.rigid_camera_grid(parallel,600,338)
        self.assertAlmostEqual(grid[0]['camera']['frustum']['top'],parallel['frustum']['top']*2)
        self.assertEqual(module.proportion_camera(parallel,600,338),parallel)

    def test_parallel_camera_rejects_invalid_source_and_preserves_zoom(self):
        camera=module._lookat_camera_info([0,0,3],[0,0,0],35.,'cpu')
        camera['zoom']=2
        parallel=module.proportion_camera(camera,400,600)
        self.assertEqual(parallel['zoom'],2)
        with self.assertRaises(ValueError):module.proportion_camera(camera,0,600)
        with self.assertRaises(ValueError):module.proportion_camera(dict(camera,fov=float('nan')),400,600)

    def test_native_viewer_resize_does_not_change_parallel_scale(self):
        camera=module._lookat_camera_info([0,0,.7],[0,0,0],35.,'cpu')
        parallel=module.proportion_camera(camera,1672,941)
        native=module.native_proportion_camera(camera,1672,941)
        self.assertEqual(native['frustum']['top']-native['frustum']['bottom'],10)
        self.assertAlmostEqual(941/10*native['zoom'],941/(parallel['frustum']['top']-parallel['frustum']['bottom'])*parallel['zoom'])
        self.assertEqual(module.native_proportion_camera(native,1672,941),native)

    def test_rigid_grid_changes_only_camera_at_constant_radius_and_focal_length(self):
        camera=module._lookat_camera_info([0.,.3,3.],[0,0,0],35.,'cpu')
        views=module.rigid_camera_grid(camera)
        self.assertEqual(len(views),219)
        radius=math.sqrt(9.09)
        for v in views:
            c=v['camera'];p=c['position']
            self.assertAlmostEqual(sum(p[k]**2 for k in 'xyz')**.5,radius,places=5)
            self.assertAlmostEqual(math.degrees(math.asin(p['y']/radius)),v['elevation'],places=4)
            self.assertAlmostEqual(c['fov'],views[0]['camera']['fov'])
        for row in range(3):
            self.assertEqual(views[row*73]['camera'],views[row*73+72]['camera'])
        with self.assertRaises(ValueError):module.rigid_camera_grid(dict(camera,fov=float('nan')))

    def test_initial_portrait_framing_includes_the_whole_subject(self):
        points=torch.tensor([[-.4,-1.2,0.],[.4,1.2,0.]]).repeat(100,1)
        values={'means':points,'opacities':torch.ones(200)}
        camera=module._lookat_camera_info([0,0,3],[0,0,0],10,'cpu')
        fitted=module.fit_subject_camera(values,camera,{'background':0,'subject':200},400,600)
        self.assertGreater(fitted['fov'],10)
        self.assertLess(1.2/3/math.tan(math.radians(fitted['fov']/2)),.95)
        wide=dict(camera,fov=90.)
        self.assertEqual(module.fit_subject_camera(values,wide,{'background':0,'subject':200},400,600),wide)

    def test_portrait_viewer_and_rasterizer_have_the_same_vertical_projection(self):
        old = dict(camera={'fov': 20.}, width=682, height=1023)
        camera = module.viewer_camera(old)
        self.assertGreater(camera['fov'], 20.)
        converted = module.raster_camera(camera, 682, 1023)
        viewer_focal = 1023 / (2 * math.tan(math.radians(camera['fov']) / 2))
        raster_focal = 682 / (2 * math.tan(math.radians(converted['fov']) / 2))
        self.assertAlmostEqual(viewer_focal, raster_focal)
        self.assertAlmostEqual(converted['fov'], 20.)
        self.assertEqual(old['camera']['fov'], 20.)
        self.assertEqual(module.viewer_camera(dict(old, camera=camera, camera_fov_axis='vertical')), camera)

    def test_orthographic_zoom_uses_the_native_viewer_frustum(self):
        camera = dict(cameraType='orthographic', fov=45., zoom=2.,
                      position=dict(x=0, y=0, z=3), target=dict(x=0, y=0, z=0),
                      frustum=dict(top=5, bottom=-5))
        result = module.raster_camera(camera, 400, 600)
        pixels_per_unit = 200 / math.tan(math.radians(result['fov']) / 2) * camera['zoom'] / 3
        self.assertAlmostEqual(pixels_per_unit, 600 / 10 * 2)

    def test_browser_integer_identity_keeps_scene_unchanged(self):
        values = dict(means=torch.tensor([[1., 2., 3.]]), scales=torch.tensor([[.1, .2, .3]]),
                      quats=torch.tensor([[1., 0., 0., 0.]]))
        placement = dict(position=dict(x=0, y=0, z=0), quaternion=dict(x=0, y=0, z=0, w=1),
                         scale=dict(x=1, y=1, z=1))
        result = module.apply_viewer_transform(values, placement)
        for key in values:
            torch.testing.assert_close(result[key], values[key])

    def test_native_viewer_y_up_placement_is_applied_to_saved_scene(self):
        values = dict(means=torch.tensor([[1., 2., 3.]]), scales=torch.tensor([[.1, .2, .3]]),
                      quats=torch.tensor([[1., 0., 0., 0.]]))
        placement = dict(position=dict(x=4., y=5., z=6.), quaternion=dict(x=0., y=0., z=0., w=1.),
                         scale=dict(x=2., y=2., z=2.))
        result = module.apply_viewer_transform(values, placement)
        torch.testing.assert_close(result['means'], torch.tensor([[6., -1., 0.]]))
        torch.testing.assert_close(result['scales'], values['scales'] * 2)
        torch.testing.assert_close(values['means'], torch.tensor([[1., 2., 3.]]))
        placement['scale'] = dict(x=2., y=3., z=4.)
        result = module.apply_viewer_transform(values, placement)
        torch.testing.assert_close(result['scales'], torch.tensor([[.2, .6, 1.2]]))
        placement['quaternion']['w'] = float('nan')
        with self.assertRaises(ValueError):
            module.apply_viewer_transform(values, placement)


if __name__ == '__main__':
    unittest.main()
