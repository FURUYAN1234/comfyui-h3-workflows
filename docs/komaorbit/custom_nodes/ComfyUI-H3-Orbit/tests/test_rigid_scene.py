"""Saved scene loading must reject changed inputs before expensive dependencies run."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import torch
from safetensors.torch import save_file

PACK = Path(__file__).parents[1]
sys.path.insert(0, str(PACK.parents[1]))
spec = importlib.util.spec_from_file_location('rigid_scene_tests', PACK / '__init__.py',
                                             submodule_search_locations=[str(PACK)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from rigid_scene_tests.rigid_scene import H3OrbitRigidScene
from rigid_scene_tests.grid_nodes import source_hash, file_digest
import folder_paths


class SceneReplayTests(unittest.TestCase):
    def test_replay_preserves_scene_and_rejects_wrong_source_or_modified_file(self):
        old_output = folder_paths.get_output_directory()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                folder_paths.set_output_directory(tmp)
                target = Path(tmp) / 'H3_ORB360/saved'
                target.mkdir(parents=True)
                original = torch.zeros(1, 10, 20, 3)
                values = dict(means=torch.zeros(2, 3), scales=torch.ones(2, 3),
                              quats=torch.tensor([[1., 0., 0., 0.],[1.,0.,0.,0.]]),
                              opacities=torch.ones(2), sh=torch.zeros(2, 1, 3))
                save_file(values, str(target / 'scene.safetensors'))
                manifest = dict(kind='rigid_scene', status='complete', subfolder='H3_ORB360/saved',
                                source_sha256=source_hash(original), width=20, height=10,
                                camera={'fov':45,'position':{'x':0,'y':0,'z':3},'target':{'x':0,'y':0,'z':0}},
                                alignment={'components':{'background':1,'subject':1}},
                                scene_sha256=file_digest(target / 'scene.safetensors'))
                (target / 'manifest.json').write_text(json.dumps(manifest))
                args = dict(mode='固定3Dを開く（3D ID指定）', original=original,
                            checkpoint='H3_ORB360/saved/manifest.json')
                node = H3OrbitRigidScene()
                self.assertEqual(node.check_lazy_status(**args), [])
                output = node.build(**args)
                result = output['result']
                self.assertEqual(output['ui']['h3_scene'], [{'width': 20, 'height': 10}])
                torch.testing.assert_close(result[0].positions[0], values['means'])
                self.assertEqual(result[2:4], (20, 10))
                self.assertEqual(result[1]['cameraType'],'orthographic')
                layers=json.loads((target/'viewer_layers.json').read_text())
                self.assertEqual(layers['scene_sha256'],manifest['scene_sha256'])
                for name in ('subject','background'):
                    self.assertIn(b'element vertex 1\n',(target/layers[name]).read_bytes())
                # Textured scenes must validate auxiliary files as well as geometry.
                texture=target/'panorama.png';texture.write_bytes(b'fixture texture')
                manifest['texture_sha256']={'panorama.png':file_digest(texture)}
                (target/'manifest.json').write_text(json.dumps(manifest))
                self.assertEqual(node.check_lazy_status(**args),[])
                texture.write_bytes(b'changed texture')
                with self.assertRaisesRegex(ValueError,'破損'):node.check_lazy_status(**args)
                manifest.pop('texture_sha256');(target/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, '入力画像'):
                    node.check_lazy_status(**dict(args, original=original + 1))
                with (target / 'scene.safetensors').open('ab') as stream:
                    stream.write(b'changed')
                with self.assertRaisesRegex(ValueError, '破損'):
                    node.check_lazy_status(**args)
            finally:
                folder_paths.set_output_directory(old_output)


if __name__ == '__main__':
    unittest.main()
