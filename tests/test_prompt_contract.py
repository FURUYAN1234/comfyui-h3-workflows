"""Focused offline checks for the v1.1 prompt and long-timeline contract."""
import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPT_NODE = ROOT / 'custom_nodes' / 'comfyui-h3-standard-prompt'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


standard = load_module('h3_standard', PROMPT_NODE / 'standard.py')
guard = load_module('h3_quality_guard', PROMPT_NODE / 'quality_guard.py')


class PromptContractTests(unittest.TestCase):
    def test_bracketed_timed_phases_are_segmented(self):
        prompt = '''Duration: 30 seconds

integrated_multimodal_description: [Shot 1] [00:00.000-00:15.000] A traveler walks along a park path. [00:15.000-00:30.000] The same traveler stops, looks at the trees, and smiles.

overall_soundscape: Wind.

non_diegetic_music: N/A'''
        parsed = standard.timeline(prompt, 30)
        self.assertIsNotNone(parsed)
        _, _, entries, _, _, is_range = parsed
        self.assertTrue(is_range)
        self.assertEqual([(start, end) for start, end, _ in entries], [(0.0, 15.0), (15.0, 30.0)])

    def test_quoted_clock_is_not_a_timeline_control(self):
        prompt = 'Duration: 15 seconds\nintegrated_multimodal_description: [Shot 1] A person says <d>[Japanese]00:00-00:15と読んで。</d> once.\noverall_soundscape: Quiet room.\nnon_diegetic_music: N/A'
        parsed = standard.timeline(prompt, 15)
        self.assertIsNotNone(parsed)
        self.assertFalse(parsed[-1])

    def test_production_direction_in_dialogue_is_rejected(self):
        prompt = '''integrated_multimodal_description: [Shot 1] A person says <d>[Japanese]カメラをズームして30秒の動画を作って。</d>.

overall_soundscape: Quiet room.

non_diegetic_music: N/A'''
        errors = guard.conversion_errors(prompt, '人物が室内に立つ。', [], 30)
        self.assertTrue(any('production/camera/reference' in error for error in errors))

    def test_untranslated_production_prose_is_rejected(self):
        prompt = '''integrated_multimodal_description: [Shot 1] 人物が静かな部屋をゆっくり歩く。カメラは固定し、窓から柔らかな光が入る。

overall_soundscape: Quiet room.

non_diegetic_music: N/A'''
        errors = guard.conversion_errors(prompt, '人物が部屋を歩く。', [], 15)
        self.assertTrue(any('Untranslated Japanese production prose' in error for error in errors))


if __name__ == '__main__':
    unittest.main()
