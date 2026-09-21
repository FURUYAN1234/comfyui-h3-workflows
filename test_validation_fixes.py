"""Regression checks for the v1.1.9 prompt-validation fixes."""
import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
PACKAGE = ROOT / "custom_nodes" / "comfyui-h3-standard-prompt"


def load_standard():
    spec = importlib.util.spec_from_file_location("h3_validation_standard", PACKAGE / "standard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_prompt_package():
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.base_path = str(ROOT)
    sys.modules.setdefault("folder_paths", folder_paths)
    spec = importlib.util.spec_from_file_location(
        "h3_validation_prompt",
        PACKAGE / "__init__.py",
        submodule_search_locations=[str(PACKAGE)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ValidationFixTests(unittest.TestCase):
    def test_single_segment_rejects_timeline_beyond_requested_duration(self):
        standard = load_standard()
        prompt = (
            "detailed_description: [Shot 1] [00:00-00:45] A robot waves once and stops.\n"
            "overall_soundscape: Room tone.\nnon_diegetic_music: N/A"
        )
        errors = standard.boundary_errors(prompt, 5, [])
        self.assertTrue(any("extends to 45" in error for error in errors))

    def test_mixed_speech_song_music_ban_is_global_silence(self):
        prompt_package = load_prompt_package()
        self.assertTrue(prompt_package.requests_global_silence("台詞・歌・音楽はなし。"))

    def test_song_only_ban_does_not_suppress_requested_dialogue(self):
        prompt_package = load_prompt_package()
        self.assertFalse(prompt_package.requests_global_silence("歌はなし。女性が「こんにちは」と話す。"))


if __name__ == "__main__":
    unittest.main()
