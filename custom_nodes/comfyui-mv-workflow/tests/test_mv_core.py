import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mv_core import (
    align_asr_chunks,
    build_instruction,
    cues_to_srt,
    resolve_duration_plan,
    visual_style_instruction,
)


class DurationTests(unittest.TestCase):
    def test_full_mode_uses_remaining_audio(self):
        plan = resolve_duration_plan(164.0, 11.0, "音源末尾まで（可変尺）", 10, 1)
        self.assertEqual(plan.duration_seconds, 153.0)

    def test_arbitrary_mode_clamps_to_remaining_audio(self):
        plan = resolve_duration_plan(20.0, 18.0, "任意秒数", 10, 3)
        self.assertEqual(plan.duration_seconds, 2.0)
        self.assertEqual(plan.fade_out_seconds, 2.0)

    def test_invalid_start_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_duration_plan(20.0, 20.0, "任意秒数", 10, 1)


class SubtitleTests(unittest.TestCase):
    def test_asr_typo_maps_to_known_lyric_without_fake_chunk(self):
        chunks = [
            {"timestamp": (0.0, 2.0), "text": "作詞作曲初音ミク"},
            {"timestamp": (2.0, 6.0), "text": "ソファで横たわる金髪の猫耳症状"},
            {"timestamp": (6.0, 10.0), "text": "白い服にピンクのブーツが映える"},
        ]
        lyrics = "[Verse]\nソファで横たわる金髪の猫耳少女\n白い服にピンクのブーツが映える"
        cues = align_asr_chunks(chunks, lyrics, 10.0)
        self.assertEqual([item["text"] for item in cues], [
            "ソファで横たわる金髪の猫耳少女",
            "白い服にピンクのブーツが映える",
        ])
        self.assertIn("00:00:02,000 --> 00:00:06,000", cues_to_srt(cues))


class StyleLockTests(unittest.TestCase):
    def test_all_modes_have_explicit_rendering_contract(self):
        expected = {
            "参照画像に自動追従": "SOURCE_MATCH_AUTO",
            "2Dアニメ": "RENDERING_MEDIUM=2D_ANIME",
            "3D CG": "RENDERING_MEDIUM=3D_CGI",
            "実写": "RENDERING_MEDIUM=PHOTOREAL_LIVE_ACTION",
        }
        for mode, marker in expected.items():
            self.assertIn(marker, visual_style_instruction(mode))

    def test_instruction_carries_2d_lock_and_exact_speech(self):
        plan = resolve_duration_plan(100.0, 11.0, "任意秒数", 20.0, 1.0)
        instruction = build_instruction(
            "MVの雰囲気や演出は全部おまかせ",
            "曲名",
            "style",
            plan,
            [{"start": 0.5, "end": 3.0, "text": "正確な歌詞"}],
            "2Dアニメ",
        )
        self.assertIn("RENDERING_MEDIUM=2D_ANIME", instruction)
        self.assertIn("(S1) <d>[Japanese] 正確な歌詞</d>", instruction)
        self.assertIn("汎用顔へ置き換えず", instruction)
        self.assertIn("顔を比較できる中近景または近景", instruction)

    def test_instruction_carries_explicit_character_identity_lock(self):
        plan = resolve_duration_plan(100.0, 11.0, "任意秒数", 20.0, 1.0)
        notes = "chin-length blonde bob, green eyes, blue headband, yellow cat ears, pink boots"
        instruction = build_instruction("おまかせ", "曲名", "style", plan, [], "2Dアニメ", notes)
        self.assertIn("MV_CHARACTER_IDENTITY_LOCK: " + notes, instruction)

    def test_unknown_style_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            visual_style_instruction("油絵")


if __name__ == "__main__":
    unittest.main()
