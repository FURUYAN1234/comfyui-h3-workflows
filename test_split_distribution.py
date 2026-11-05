"""Independent kit builds, exact inventories, and rejection of mixed payloads."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

import build_individual


class SplitDistributionTests(unittest.TestCase):
    def test_each_kit_is_independent_and_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for kind, expected in [('video', {'T2V', 'I2V', 'Ref2V'}), ('mv', {'MV'})]:
                with self.subTest(kind=kind):
                    archive = base/(kind+'.zip')
                    again = base/(kind+'-again.zip')
                    build_individual.build(kind, archive, '20261105183000')
                    build_individual.build(kind, again, '20261105183000')
                    self.assertEqual(archive.read_bytes(), again.read_bytes())
                    extract = base/kind
                    with zipfile.ZipFile(archive) as z:
                        z.extractall(extract)
                    root = next(extract.iterdir())
                    self.assertEqual({f.name.split('_')[0] for f in (root/'workflows').glob('*.json')}, expected)
                    self.assertFalse((root/'docs/komaorbit').exists())
                    self.assertEqual((root/'custom_nodes/comfyui-mv-workflow').exists(), kind=='mv')
                    for name, digest in json.loads((root/'RUNTIME_SHA256.json').read_text()).items():
                        self.assertEqual(hashlib.sha256((build_individual.ROOT/name).read_bytes()).hexdigest(), digest)
                    def verify():
                        return subprocess.run([sys.executable, '-B', str(root/'verify_package.py')], capture_output=True).returncode
                    self.assertEqual(verify(), 0)
                    foreign = next((build_individual.ROOT/'workflows').glob('MV*.json' if kind=='video' else 'T2V*.json'))
                    shutil.copy2(foreign, root/'workflows'/foreign.name)
                    self.assertNotEqual(verify(), 0, 'A mixed workflow package must be rejected')
                    (root/'workflows'/foreign.name).unlink()
                    required = root/'custom_nodes/ComfyUI-H3-AudioRefine/LICENSE'
                    original = required.read_bytes()
                    required.unlink()
                    self.assertNotEqual(verify(), 0, 'Missing license must be rejected')
                    required.write_bytes(original)
                    self.assertEqual(verify(), 0)


if __name__ == '__main__':
    unittest.main()
