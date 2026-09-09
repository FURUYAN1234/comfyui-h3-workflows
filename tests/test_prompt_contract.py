"""Focused offline checks for the v1.1 prompt and long-timeline contract."""
import ast
import re
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
timeline_module = load_module(
    'h3_timeline',
    ROOT / 'custom_nodes' / 'ComfyUI-MiniMax-H3-Long-Video' / 'minimax_h3_long_video' / 'timeline.py'
)


tree=ast.parse((PROMPT_NODE/'__init__.py').read_text(encoding='utf-8'))
policy=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='split_conversion_issues')
namespace={'re':re}
exec(compile(ast.Module(body=[policy],type_ignores=[]),'<actual policy>','exec'),namespace)
split_issues=namespace['split_conversion_issues']

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

    def test_mixed_japanese_is_warning_and_preserved(self):
        prompt = '''integrated_multimodal_description: [Shot 1] 人物が静かな部屋をゆっくり歩く。カメラは固定し、窓から柔らかな光が入る。

overall_soundscape: Quiet room.

non_diegetic_music: N/A'''
        errors = guard.conversion_errors(prompt, '人物が部屋を歩く。', [], 15)
        blocking,warnings=split_issues(errors,prompt,0)
        self.assertEqual(blocking,[])
        self.assertTrue(warnings)

    def test_missing_reference_label_is_warning_but_invalid_id_blocks(self):
        for prompt,blocked in [('<Picture 1>',False),('<Picture 9>',True)]:
            errors=standard.reference_format_errors(prompt,2)
            blocking,warnings=split_issues(errors,prompt,2)
            self.assertEqual(bool(blocking),blocked)
            self.assertTrue(warnings)

    def test_20_seconds_or_less_is_one_pass_for_both_context_sizes(self):
        for duration in (5, 10, 15, 16, 20, 30, 60):
            for context_frames in (22, 39):
                requested = round(duration * 24)
                grid = timeline_module._h3_grid_frames(requested)
                max_raw = grid if 15 < duration <= 20 else 362
                segments = timeline_module.plan_segments(
                    grid, context_frames, False, max_raw,
                    exact_output_frames=requested,
                )
                self.assertEqual(sum(segment.output_frames for segment in segments), requested)
                self.assertEqual(len(segments) == 1, duration <= 20)

    def test_phase_crossing_a_generation_boundary_is_rejected(self):
        straddling = '''Duration: 30 seconds

integrated_multimodal_description: [Shot 1] [00:00.000-00:30.000] A traveler walks from the gate to the distant tree without restarting.

overall_soundscape: Wind.

non_diegetic_music: N/A'''
        errors = standard.boundary_errors(straddling, 30, [15])
        self.assertTrue(any('15.000' in error for error in errors))


if __name__ == '__main__':
    unittest.main()
