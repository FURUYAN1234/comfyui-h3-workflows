"""Portable-path and public-input checks for the MV workflow nodes."""
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest

import torch


ROOT = pathlib.Path(__file__).resolve().parent
PACKAGE = ROOT / "custom_nodes" / "comfyui-mv-workflow"
COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
if COMFY_ROOT and pathlib.Path(COMFY_ROOT).is_dir():
    sys.path.insert(0, COMFY_ROOT)


def load_package():
    spec = importlib.util.spec_from_file_location(
        "mv_portability_package",
        PACKAGE / "__init__.py",
        submodule_search_locations=[str(PACKAGE)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return sys.modules[spec.name + ".nodes"]


class MVPortabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = load_package()

    def test_relative_bundle_resolves_below_comfy_output(self):
        original = self.nodes.folder_paths.get_output_directory
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp)
            bundle = output / "mv-assets" / "song_bundle"
            bundle.mkdir(parents=True)
            self.nodes.folder_paths.get_output_directory = lambda: str(output)
            try:
                self.assertEqual(
                    self.nodes._resolve_bundle_folder("song_bundle"),
                    bundle.resolve(),
                )
                self.assertEqual(
                    self.nodes._resolve_bundle_folder("mv-assets/song_bundle"),
                    bundle.resolve(),
                )
            finally:
                self.nodes.folder_paths.get_output_directory = original

    def test_image_hash_is_stable_and_does_not_require_a_private_path(self):
        image = torch.zeros((1, 16, 16, 3), dtype=torch.float32)
        self.assertEqual(self.nodes._image_sha256(image), self.nodes._image_sha256(image.clone()))

    def test_public_defaults_use_comfy_relative_paths(self):
        schema = self.nodes.MVAssetBundlePrepare.define_schema()
        values = {item.id: item for item in schema.inputs}
        self.assertEqual(values["bundle_folder"].default, "mv-assets/YOUR_BUNDLE_FOLDER")
        self.assertEqual(
            values["whisper_model_path"].default,
            "models/whisper/whisper-large-v3-turbo",
        )
        self.assertTrue(values["character"].optional)


if __name__ == "__main__":
    unittest.main()
