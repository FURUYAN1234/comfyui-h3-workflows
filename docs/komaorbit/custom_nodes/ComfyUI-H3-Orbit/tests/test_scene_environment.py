import importlib.util
from pathlib import Path
import unittest
import numpy as np
import torch

spec = importlib.util.spec_from_file_location('scene_environment', Path(__file__).parents[1]/'scene_environment.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EnvironmentTests(unittest.TestCase):
    def test_orbit_clearance_removes_overhead_fog_and_preserves_floor_and_distant_walls(self):
        points=torch.tensor([[0.,-2.,0.], [0.,2.,0.], [3.,0.,0.], [2.,0.,0.]])
        scales=torch.tensor([[.01]*3,[.01]*3,[.01]*3,[.5]*3])
        subject=torch.tensor([[0.,-1.,0.],[0.,1.,0.]])
        keep,info=module.clear_orbit_space({'means':points,'scales':scales},subject,1.)
        self.assertEqual(keep.tolist(),[False,True,True,False])
        self.assertEqual(info['removed'],2)

    def test_background_cannot_cover_the_known_subject_but_distant_surfaces_remain(self):
        means=torch.tensor([[0.,0.,1.], [0.,0.,5.], [1.2,0.,1.], [1.2,0.,1.]])
        scales=torch.tensor([[.01]*3,[.01]*3,[.01]*3,[.6]*3])
        mask=torch.zeros((1,64,64));mask[:,20:44,20:44]=1
        camera=torch.eye(4)[None];intrinsics=torch.tensor([[[30.,0,32],[0,30,32],[0,0,1]]])
        depth=torch.full((1,64,64,1),2.)
        kept,info=module.prune_background_occluders({'means':means,'scales':scales},mask,camera,intrinsics,depth)
        torch.testing.assert_close(kept['means'],means[[1,2]])
        self.assertEqual(info,{'removed':2,'retained':2})

    def inputs(self):
        image = np.full((1, 64, 64, 3), [.1, .2, .3], dtype=np.float32)
        mask = np.zeros((1, 64, 64), dtype=np.float32)
        image[:, 20:44, 20:44] = [1., 0., 0.]
        mask[:, 20:44, 20:44] = 1
        return [image, mask, np.eye(4)[None], np.array([[[40.,0,32],[0,40,32],[0,0,1]]]),
                np.zeros(3), np.eye(3), 10.]

    def test_foreground_cannot_leak_into_background_or_unseen_directions(self):
        shell, info = module.projected_shell(*self.inputs(), count=512)
        rgb = shell['sh'][:, 0].numpy()*.28209479177387814+.5
        np.testing.assert_allclose(rgb, np.broadcast_to([.1,.2,.3], rgb.shape), atol=1e-6)
        np.testing.assert_allclose(shell['means'].norm(dim=-1).numpy(), 10., atol=1e-5)
        self.assertGreater(info['observed_fraction'], 0)
        self.assertLess(info['observed_fraction'], 1)

    def test_unseen_ground_uses_lower_background_while_sky_stays_upper(self):
        args=self.inputs()
        args[0][:,:32]=[.1,.7,.9]
        args[0][:,32:]=[.8,.6,.3]
        args[0][:,20:44,20:44]=[1.,0.,0.]
        shell,info=module.projected_shell(*args,count=512)
        rgb=shell['sh'][:,0].numpy()*.28209479177387814+.5
        y=shell['means'][:,1].numpy()
        np.testing.assert_allclose(rgb[y>9],np.broadcast_to([.8,.6,.3],rgb[y>9].shape),atol=1e-6)
        np.testing.assert_allclose(rgb[y<-9],np.broadcast_to([.1,.7,.9],rgb[y<-9].shape),atol=1e-6)

    def test_missing_background_and_mismatched_inputs_are_rejected(self):
        args=self.inputs();args[1][:]=1
        with self.assertRaisesRegex(ValueError, '不足'): module.projected_shell(*args,count=512)
        args=self.inputs();args[1]=args[1][:,:32]
        with self.assertRaisesRegex(ValueError, '対応'): module.projected_shell(*args,count=512)
        args=self.inputs();args[0][0,0,0,0]=float('nan')
        with self.assertRaisesRegex(ValueError, '非有限'): module.projected_shell(*args,count=512)
