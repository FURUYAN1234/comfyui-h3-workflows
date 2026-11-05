"""Uses the installed ComfyUI runtime and actual tensors/PNG files; no GPU generation."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

PACK = Path(__file__).parents[1]
sys.path.insert(0, str(PACK.parents[1]))
spec = importlib.util.spec_from_file_location("h3_orbit_test_package", PACK / "__init__.py", submodule_search_locations=[str(PACK)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from h3_orbit_test_package.nodes import H3OrbitPrepare, H3OrbitSave
import folder_paths
import torch
from PIL import Image


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_output = folder_paths.get_output_directory()
        folder_paths.set_output_directory(self.temp.name)

    def tearDown(self):
        folder_paths.set_output_directory(self.old_output)
        self.temp.cleanup()

    def test_full_input_survives_padding_and_export(self):
        image = torch.zeros((1, 61, 97, 3))
        image[:, 0, :, 0] = 1
        image[:, -1, :, 1] = 1
        image[:, :, 0, 2] = 1
        padded, w, h, g = H3OrbitPrepare().prepare(image, 1024)
        content = padded[:, g["top"]:g["top"] + 61, g["left"]:g["left"] + 97, :]
        self.assertTrue(torch.equal(content, image))
        frames = padded.repeat(124, 1, 1, 1)
        result = H3OrbitSave().save(frames, g, image, {}, {"workflow": {"id": "test"}})
        manifest = result["ui"]["h3_orbit"][0]
        target = Path(self.temp.name) / manifest["subfolder"]
        self.assertEqual(len(list(target.glob("frame_*.png"))), 124)
        with Image.open(target / "frame_000.png") as frame:
            self.assertEqual(frame.size, (97, 61))
            self.assertEqual(frame.getpixel((48, 0)), (255, 0, 0))
            self.assertEqual(frame.getpixel((48, 60)), (0, 255, 0))
            self.assertIn("workflow", frame.info)
        self.assertEqual(json.loads((target / "manifest.json").read_text()), manifest)
        second = H3OrbitSave().save(frames, g, image)["ui"]["h3_orbit"][0]
        self.assertNotEqual(manifest["subfolder"], second["subfolder"])

    def test_bad_frame_count_does_not_write_partial_success(self):
        image = torch.zeros((1, 32, 32, 3))
        prepared, _, _, g = H3OrbitPrepare().prepare(image, 1024)
        with self.assertRaisesRegex(ValueError, "124"):
            H3OrbitSave().save(prepared.repeat(123, 1, 1, 1), g, image)
        self.assertFalse(list(Path(self.temp.name).rglob("manifest.json")))

    def test_batches_are_not_silently_reduced_to_first_image(self):
        with self.assertRaises(ValueError):
            H3OrbitPrepare().prepare(torch.zeros((2, 32, 32, 3)), 1024)

    def test_portrait_and_odd_size_roundtrip_aspect(self):
        for w, h in [(67, 101), (128, 192), (192, 128)]:
            image = torch.rand((1, h, w, 3))
            padded, pw, ph, g = H3OrbitPrepare().prepare(image, 96)
            self.assertEqual(tuple(padded.shape), (1, ph, pw, 3))
            self.assertEqual(g["export_width"] * h, g["export_height"] * w)


if __name__ == "__main__":
    unittest.main()
