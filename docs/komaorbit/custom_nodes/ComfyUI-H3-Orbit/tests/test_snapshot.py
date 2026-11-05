import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

spec = importlib.util.spec_from_file_location('snapshot_under_test', Path(__file__).parents[1] / 'snapshot.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SnapshotTests(unittest.TestCase):
    def test_fractional_safe_angle_is_saved_and_named(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'H3_ORB360'/'fractional';(base/'row_00').mkdir(parents=True)
            Image.new('RGB',(120,80),'red').save(base/'row_00/frame_000.png')
            manifest=dict(schema_version=2,status='complete',subfolder='H3_ORB360/fractional',row_count=3,frame_count=73,width=120,height=80,frame_basis_zoom=.5,elevations=[-9.254486974537064,0,30])
            (base/'manifest.json').write_text(json.dumps(manifest))
            result=module.save_snapshot(root,dict(subfolder=manifest['subfolder'],row=0,index=0,zoom=1,original=False))
            self.assertIn('高さ-09.3deg',result['path'])
            self.assertTrue((root/result['path']).is_file())

    def test_wide_frame_uses_real_background_instead_of_empty_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'H3_ORB360' / 'wide'
            (base / 'row_00').mkdir(parents=True)
            image = Image.new('RGB', (120, 80), 'red')
            image.paste('blue', (30, 20, 90, 60))
            image.save(base / 'row_00/frame_000.png')
            manifest = dict(schema_version=2, status='complete', subfolder='H3_ORB360/wide',
                row_count=1, frame_count=73, width=120, height=80, frame_basis_zoom=.5)
            (base / 'manifest.json').write_text(json.dumps(manifest))
            request = dict(subfolder='H3_ORB360/wide', row=0, index=0, zoom=.5, original=False)
            result = module.save_snapshot(root, request)
            with Image.open(root / result['path']) as saved:
                self.assertEqual(saved.getpixel((1, 1)), (255, 0, 0))
                self.assertEqual(saved.getpixel((60, 40)), (0, 0, 255))
            result = module.save_snapshot(root, request | {'zoom': 1})
            with Image.open(root / result['path']) as saved:
                self.assertEqual(saved.getpixel((10, 10)), (0, 0, 255))
            manifest.pop('frame_basis_zoom')
            (base / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, '背景がありません'):
                module.save_snapshot(root, request)

    def test_folder_export_seven_heights_writes_cropped_pixels_and_keeps_failed_export_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'H3_ORB360' / 'seven'
            for row in range(7):
                (base / f'row_{row:02d}').mkdir(parents=True)
                for frame in range(4):
                    im = Image.new('RGB', (120, 80), 'red')
                    im.paste((frame * 60, row * 35, 200), (30, 20, 90, 60))
                    im.save(base / f'row_{row:02d}/frame_{frame:03d}.png')
            (base / 'manifest.json').write_text(json.dumps(dict(schema_version=2, status='complete',
                kind='references', subfolder='H3_ORB360/seven', row_count=7, frame_count=4,
                elevations=[-45, -30, -15, 0, 15, 30, 45], width=120, height=80)))
            request = dict(subfolder='H3_ORB360/seven', row=3, index=0, zoom=2, original=False)
            result = module.export_folder(root, request)
            target = root / result['path']
            self.assertEqual(result['count'], 28)
            self.assertEqual(len(list(target.glob('*.png'))), 28)
            self.assertEqual(json.loads((target / 'manifest.json').read_text())['status'], 'complete')
            with Image.open(target / '方位090.0deg_高さ+15deg_f001_倍率2.000x.png') as image:
                self.assertEqual(image.size, (120, 80))
                self.assertEqual(image.getpixel((10, 10)), (60, 140, 200))
            (base / 'row_06/frame_003.png').unlink()
            with self.assertRaises(OSError):
                module.export_folder(root, request)
            states = [json.loads(p.read_text())['status'] for p in (root/'H3_ORB360/exports').glob('*/manifest.json')]
            self.assertCountEqual(states, ['complete', 'failed'])

    def test_save_zoom_preserves_ratio_and_source_and_rejects_invalid_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'H3_ORB360' / 'test'
            (base / 'row_01').mkdir(parents=True)
            image = Image.new('RGB', (120, 80), 'red')
            image.paste('blue', (30, 20, 90, 60))
            source = base / 'row_01/frame_002.png'
            image.save(source)
            before = source.read_bytes()
            (base / 'manifest.json').write_text(json.dumps(dict(schema_version=2, status='complete',
                subfolder='H3_ORB360/test', row_count=3, frame_count=73, width=120, height=80)))
            request = dict(subfolder='H3_ORB360/test', row=1, index=2, zoom=2, original=False)
            result = module.save_snapshot(root, request)
            with Image.open(root / result['path']) as saved:
                self.assertEqual(saved.size, (120, 80))
                self.assertEqual(saved.getpixel((60, 40)), (0, 0, 255))
                self.assertEqual(json.loads(saved.info['h3_orbit_view']), request)
            self.assertEqual(source.read_bytes(), before)
            for change in [dict(subfolder='../outside'), dict(zoom=float('nan')), dict(zoom=5),
                           dict(index=73), dict(row=-1), dict(original='false')]:
                with self.assertRaises(ValueError): module.save_snapshot(root, request | change)


if __name__ == '__main__':
    unittest.main()
