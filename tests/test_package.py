"""Offline checks: python -m unittest discover -s tests -v"""
import contextlib
import copy
import hashlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest

from verify_package import verify

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def copy_package(self, folder):
        target = pathlib.Path(folder) / 'package'
        root_exclusions = {
            '.gitattributes', '.gitignore', 'CHANGELOG.md', 'LICENSE',
            'README.md', 'VERSION', 'THIRD_PARTY_NOTICES.md',
            'build_package.py', 'build_release.py', 'dependencies.lock.json',
            'verify_dependencies.py'
        }

        def ignore_payload(directory, names):
            relative = pathlib.Path(directory).relative_to(ROOT)
            excluded_dirs = {'.git', 'dist', '__pycache__', 'docs', 'patches', 'tests'}
            ignored = {name for name in names if name in excluded_dirs or name.endswith('.pyc')}
            if relative == pathlib.Path('.'):
                ignored |= set(names) & root_exclusions
            return ignored

        shutil.copytree(
            ROOT,
            target,
            ignore=ignore_payload
        )
        return target

    def test_current_package(self):
        with tempfile.TemporaryDirectory() as folder:
            with contextlib.redirect_stdout(io.StringIO()):
                verify(self.copy_package(folder))

    def test_missing_portable_module_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            target = self.copy_package(folder)
            (target / 'custom_nodes' / 'comfyui-h3-standard-prompt' / 'quality_guard.py').unlink()
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(AssertionError, 'Missing portable dependency'):
                    verify(target)

    def test_tampering_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            target = self.copy_package(folder)
            path = target / 'VERSION.json'
            payload = json.loads(path.read_text(encoding='utf-8'))
            payload['built_at_jst'] = '20990101000000'
            path.write_text(json.dumps(payload), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(AssertionError, 'Checksum mismatch'):
                    verify(target)

    def test_model_weight_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = self.copy_package(folder)
            (target / 'test.safetensors').write_bytes(b'not-real-weights')
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(AssertionError, 'Unexpected model'):
                    verify(target)

    def test_broken_graph_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = self.copy_package(folder)
            path = next((target / 'workflows').glob('T2V_4step_*.json'))
            graph = json.loads(path.read_text(encoding='utf-8'))
            next(n for n in graph['nodes'] if n['id'] == 124)['widgets_values'][1] = 9
            path.write_text(json.dumps(graph), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(AssertionError, 'Video steps changed'):
                    verify(target)


if __name__ == '__main__':
    unittest.main()
