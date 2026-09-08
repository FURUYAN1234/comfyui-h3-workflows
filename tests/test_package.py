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
from verify_dependencies import verify as verify_dependencies

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def copy_package(self, folder):
        target = pathlib.Path(folder) / 'package'
        shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns('.git', 'dist', '__pycache__'))
        return target

    def test_current_package(self):
        with contextlib.redirect_stdout(io.StringIO()):
            verify(ROOT)

    def test_missing_dependency_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(AssertionError, 'Missing dependency file'):
                verify_dependencies(folder)

    def test_tampering_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            target = self.copy_package(folder)
            path = target / 'VERSION'
            path.write_text('9.9.9\n', encoding='utf-8')
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
            path = target / 'workflows' / 'T2V_4step.json'
            graph = json.loads(path.read_text(encoding='utf-8'))
            next(n for n in graph['nodes'] if n['id'] == 124)['widgets_values'][1] = 9
            path.write_text(json.dumps(graph), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(AssertionError, 'Video steps changed'):
                    verify(target)


if __name__ == '__main__':
    unittest.main()
