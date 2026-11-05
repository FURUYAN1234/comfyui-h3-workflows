"""Real tensor/file contract checks; these are not evidence of generation quality."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

PACK = Path(__file__).parents[1]
sys.path.insert(0, str(PACK.parents[1]))
spec = importlib.util.spec_from_file_location('h3_orbit_grid_tests', PACK / '__init__.py', submodule_search_locations=[str(PACK)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from h3_orbit_grid_tests.camera import anchor_prompts, camera_plan, orbit_prompt
from h3_orbit_grid_tests.nodes import H3OrbitPrepare
from h3_orbit_grid_tests.grid_nodes import H3OrbitReferences, H3OrbitGridSave, checkpoint_path
from h3_orbit_grid_tests.backend import H3OrbitBackend
from comfy_execution.graph import ExecutionBlocker
import folder_paths
import torch
from PIL import Image


class GridTests(unittest.TestCase):
    def test_retired_height_generation_keeps_legacy_plan_readable(self):
        for profile in ('上下5段階', '上下7段階'):
            with self.assertRaises(ValueError):
                camera_plan(profile, '')
        self.assertEqual(camera_plan('上下3段階', '')['elevations'], [-30, 0, 30])
        plan = {'version': 1, 'elevations': [-45, -30, -15, 0, 15, 30, 45],
                'azimuths': [0, 90, 180, 270], 'scene_context': ''}
        prompts = anchor_prompts(plan)
        self.assertEqual(len(prompts), 28)
        for row, elevation in enumerate(plan['elevations']):
            for prompt in prompts[row*4:row*4+4]:
                self.assertIn(f'Camera elevation: {elevation} degrees', prompt)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_output = folder_paths.get_output_directory()
        folder_paths.set_output_directory(self.temp.name)
        self.original = torch.zeros((1, 61, 97, 3))
        self.padded, _, _, self.geometry = H3OrbitPrepare().prepare(self.original, 1024)
        self.plan = camera_plan('上下3段階', '')
        self.anchors = [torch.full_like(self.padded, i / 12) for i in range(12)]
        self.node = H3OrbitReferences()

    def tearDown(self):
        folder_paths.set_output_directory(self.old_output)
        self.temp.cleanup()

    def references(self, stage='周回まで生成', checkpoint='', images=None, original=None):
        return self.node.prepare([self.plan], [self.geometry], [self.original if original is None else original],
                                 [stage], [checkpoint], self.anchors if images is None else images,
                                 prompt=[{'test': {}}], extra_pnginfo=[{'workflow': {'id': 'source', 'nodes': [{'id': 31}]}}], unique_id=['31'])

    def test_camera_order_and_prompt_labels(self):
        prompts = anchor_prompts(self.plan)
        self.assertEqual(len(prompts), 12)
        self.assertTrue(prompts[0].startswith('<sks> front view low-angle shot'))
        self.assertTrue(prompts[6].startswith('<sks> back view eye-level shot'))
        self.assertTrue(prompts[11].startswith('<sks> left side view elevated shot'))
        for elevation in (-30, 0, 30):
            text = orbit_prompt(elevation, '')
            self.assertIn(f'elevation of {elevation} degrees', text)
            self.assertIn('<Picture 4>', text)
            self.assertIn('frame 123', text)
            self.assertNotIn('<Picture 5>', text)
        with self.assertRaises(ValueError): camera_plan('imaginary', '')

    def test_lazy_checkpoint_skips_qwen_and_missing_images_are_requested(self):
        self.assertEqual(self.node.check_lazy_status([''], None), ['images'])
        self.assertEqual(self.node.check_lazy_status([''], [None]), ['images'])
        self.assertEqual(self.node.check_lazy_status([''], self.anchors), [])
        self.assertEqual(self.node.check_lazy_status(['H3_ORB360/saved/manifest.json'], [None]), [])

    def test_reference_order_and_checkpoint_replay_are_identical(self):
        result = self.references()
        manifest = result['ui']['h3_orbit'][0]
        self.assertEqual(manifest['row_count'], 3)
        self.assertEqual(manifest['frame_count'], 4)
        target = Path(self.temp.name) / manifest['subfolder']
        self.assertEqual(len(list(target.glob('row_*/frame_*.png'))), 12)
        replay = self.references(checkpoint=manifest['subfolder'] + '/manifest.json', images=[None])
        for col in range(4):
            self.assertEqual(len(result['result'][col]), 3)
            for row in range(3):
                tensor = result['result'][col][row]
                self.assertTrue(torch.equal(tensor, replay['result'][col][row]))
                expected = round((row * 4 + col) / 12 * 255) / 255
                self.assertAlmostEqual(float(tensor[0, 0, 0, 0]), expected, places=6)
        wf = json.loads((target / 'workflow.json').read_text())
        self.assertNotEqual(wf['id'], 'source')
        self.assertEqual(wf['nodes'][0]['properties']['h3_orbit'], manifest)
        with Image.open(target / 'row_00/frame_000.png') as im:
            self.assertEqual(im.size, (97, 61))
            self.assertEqual(json.loads(im.info['workflow'])['id'], wf['id'])

    def test_reference_only_stage_blocks_h3_without_losing_preview(self):
        result = self.references(stage='参照画像だけ')
        self.assertEqual(result['ui']['h3_orbit'][0]['kind'], 'references')
        for output in result['result'][:5]: self.assertIsInstance(output[0], ExecutionBlocker)
        self.assertIsInstance(result['result'][5], ExecutionBlocker)

    def test_changed_source_and_plan_cannot_reuse_checkpoint(self):
        manifest = self.references()['ui']['h3_orbit'][0]
        path = manifest['subfolder'] + '/manifest.json'
        with self.assertRaisesRegex(ValueError, '入力画像'):
            self.references(checkpoint=path, original=torch.ones_like(self.original))
        self.plan = camera_plan('上下3段階', 'different surroundings')
        with self.assertRaisesRegex(ValueError, '背景説明'):
            self.references(checkpoint=path)

    def test_corrupt_reference_invalidates_cache_and_is_rejected(self):
        manifest = self.references()['ui']['h3_orbit'][0]
        value = manifest['subfolder'] + '/manifest.json'
        before = self.node.IS_CHANGED([value])
        frame = checkpoint_path(value).parent / 'conditioning/row_01_002.png'
        frame.write_bytes(b'corrupt')
        self.assertNotEqual(self.node.IS_CHANGED([value]), before)
        with self.assertRaisesRegex(ValueError, '破損'):
            self.references(checkpoint=value)

    def test_checkpoint_path_cannot_escape_output(self):
        for value in ('../input/manifest.json', '/tmp/manifest.json', 'H3_ORB360/a/../manifest.json', 'https://example.com'):
            with self.assertRaises(ValueError): checkpoint_path(value)
        outside = Path(self.temp.name).parent / (Path(self.temp.name).name + '_outside')
        outside.mkdir()
        try:
            root = Path(self.temp.name) / 'H3_ORB360'
            root.mkdir()
            (root / 'escape').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError): checkpoint_path('H3_ORB360/escape/manifest.json')
        finally:
            outside.rmdir()

    def test_bad_reference_count_or_shape_never_gets_manifest(self):
        with self.assertRaisesRegex(ValueError, '枚数'):
            self.references(images=self.anchors[:-1])
        bad = list(self.anchors)
        bad[2] = self.padded.repeat(2, 1, 1, 1)
        with self.assertRaisesRegex(ValueError, 'バッチ数'):
            self.references(images=bad)
        self.assertFalse(list(Path(self.temp.name).rglob('manifest.json')))

    def test_grid_rows_export_exact_ratio_and_bad_counts_do_not_commit(self):
        refs = self.references()['ui']['h3_orbit'][0]
        rows = [torch.full_like(self.padded, value).repeat(124, 1, 1, 1) for value in (0.2, 0.5, 0.8)]
        node = H3OrbitGridSave()
        result = node.save(rows, [refs], [self.geometry], [self.original])
        manifest = result['ui']['h3_orbit'][0]
        target = Path(self.temp.name) / manifest['subfolder']
        self.assertEqual(len(list(target.glob('row_*/frame_*.png'))), 372)
        for row, color in enumerate((51, 128, 204)):
            with Image.open(target / f'row_{row:02d}/frame_123.png') as im:
                self.assertEqual(im.size, (97, 61))
                self.assertEqual(im.getpixel((10, 10)), (color,) * 3)
        before = list(Path(self.temp.name).rglob('manifest.json'))
        with self.assertRaisesRegex(ValueError, '段数'):
            node.save(rows[:2], [refs], [self.geometry], [self.original])
        with self.assertRaisesRegex(ValueError, '124'):
            node.save([rows[0][:123], *rows[1:]], [refs], [self.geometry], [self.original])
        self.assertEqual(list(Path(self.temp.name).rglob('manifest.json')), before)

    def test_73_frame_grid_accepts_repeated_engine_settings(self):
        refs = self.references()['ui']['h3_orbit'][0]
        rows = [self.padded.repeat(73, 1, 1, 1) for _ in range(3)]
        node = H3OrbitGridSave()
        stale = {'subfolder': 'H3_ORB360/old'}
        workflow = {'id':'test', 'nodes':[
            {'id':16}, {'id':31,'properties':{'h3_orbit':stale}},
            {'id':32,'properties':{'h3_orbit':stale}}]}
        result = node.save(rows, [refs], [self.geometry], [self.original], frame_count=[73, 73, 73],
                           prompt=[{'16':{'inputs':{'references':['31',5]}}}],
                           extra_pnginfo=[{'workflow':workflow}], unique_id=['16'])
        m = result['ui']['h3_orbit'][0]
        self.assertEqual(m['frame_count'], 73)
        self.assertEqual(m['generation_profile'], 'FL2VA_73')
        self.assertIn('integrated_multimodal_description:', m['orbit_prompts'][0])
        self.assertEqual(len(list((Path(self.temp.name) / m['subfolder']).glob('row_*/frame_*.png'))), 219)
        restored=json.loads((Path(self.temp.name)/m['subfolder']/'workflow.json').read_text())
        self.assertEqual(restored['nodes'][1]['properties']['h3_orbit'], refs)
        self.assertEqual(restored['nodes'][2]['properties']['h3_orbit'], stale)
        self.assertEqual(workflow['nodes'][1]['properties']['h3_orbit'], stale)
        with self.assertRaisesRegex(ValueError, 'フレーム数'):
            node.save(rows, [refs], [self.geometry], [self.original], frame_count=[73, 124])
        with self.assertRaisesRegex(ValueError, 'フレーム数'):
            node.save(rows, [refs], [self.geometry], [self.original], frame_count=[])
        with self.assertRaisesRegex(ValueError, '124'):
            node.save(rows, [refs], [self.geometry], [self.original])

    def test_engine_gate_selects_only_the_requested_branch(self):
        node = H3OrbitBackend()
        self.assertEqual(node.check_lazy_status('背景用 FL2VA（73枚）'), ['loop_model', 'loop_positive', 'loop_latent'])
        self.assertEqual(node.check_lazy_status('背景用 FL2VA（73枚）', loop_model=1, loop_positive=2, loop_latent=3), [])
        self.assertEqual(node.select('背景用 FL2VA（73枚）', loop_model=1, loop_positive=2, loop_latent=3), (1, 2, 3, 28, 73))
        self.assertEqual(node.select('従来比較 Ref2VA＋ORB360（124枚）', ref_model=4, ref_positive=5, ref_latent=6), (4, 5, 6, 20, 124))
        with self.assertRaises(ValueError): node.select('unknown')
        with self.assertRaises(ValueError): node.select('背景用 FL2VA（73枚）', ref_model=4, ref_positive=5, ref_latent=6)


if __name__ == '__main__':
    unittest.main()
