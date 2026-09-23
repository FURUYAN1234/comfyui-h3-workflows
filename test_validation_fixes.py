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
    nodes = types.ModuleType("nodes")
    nodes.NODE_CLASS_MAPPINGS = {}
    sys.modules.setdefault("nodes", nodes)
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

    def test_authoritative_audio_only_vocals_allow_no_dialogue_tags(self):
        prompt_package = load_prompt_package()
        brief = (
            "歌唱中は口形を音声に同期する。"
            "AUTHORITATIVE_AUDIO_ONLY_VOCALS: <Audio 1>だけを使い、歌詞を推測しない。"
        )
        prompt = (
            "detailed_description: [00:00-00:05] Subject 1 sings in sync with <Audio 1>.\n"
            "overall_soundscape: The authoritative source vocals.\n"
            "non_diegetic_music: N/A"
        )
        self.assertEqual(prompt_package.dialogue_format_errors(prompt, brief), [])

    def test_system_prompt_allows_declared_multiple_speaker_ids(self):
        prompt_package = load_prompt_package()
        rules = prompt_package.system_prompt("Ref2VA (R2V)", 20)
        self.assertIn("(S1) or (S2)", rules)
        self.assertIn("Use only IDs declared in the brief", rules)

    def test_system_prompt_spells_sixty_seconds_as_one_minute(self):
        prompt_package = load_prompt_package()
        rules = prompt_package.system_prompt("Ref2VA (R2V)", 60)
        self.assertIn("exact final timestamp for this video is 01:00.000", rules)
        self.assertIn("not 60:00", rules)

    def test_auto_style_infers_one_unambiguous_2d_medium(self):
        prompt_package = load_prompt_package()
        brief = "MV_VISUAL_STYLE_LOCK: SOURCE_MATCH_AUTO."
        prompt = (
            "subject_definitions: <Subject 1> is a hand-drawn 2D anime character with clean line art.\n"
            "summary: A short performance.\nretention_analysis: fully_preserved.\n"
            "detailed_description: [00:00-00:05] Flat cel animation continues.\n"
            "overall_soundscape: Room tone.\nnon_diegetic_music: N/A"
        )
        result = prompt_package.enforce_visual_style_lock(prompt, brief)
        self.assertEqual(result.count("RENDERING_MEDIUM=2D_ANIME"), 2)
        self.assertEqual(prompt_package.visual_style_lock_errors(result, brief), [])

    def test_auto_style_keeps_ambiguous_output_unmarked(self):
        prompt_package = load_prompt_package()
        brief = "MV_VISUAL_STYLE_LOCK: SOURCE_MATCH_AUTO."
        prompt = (
            "subject_definitions: <Subject 1> is visible.\n"
            "summary: A short performance.\nretention_analysis: fully_preserved.\n"
            "detailed_description: [00:00-00:05] The subject performs.\n"
            "overall_soundscape: Room tone.\nnon_diegetic_music: N/A"
        )
        result = prompt_package.enforce_visual_style_lock(prompt, brief)
        self.assertNotIn("RENDERING_MEDIUM=", result)
        self.assertTrue(prompt_package.visual_style_lock_errors(result, brief))


if __name__ == "__main__":
    unittest.main()
