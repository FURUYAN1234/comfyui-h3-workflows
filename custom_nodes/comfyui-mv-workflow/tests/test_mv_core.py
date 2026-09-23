import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mv_core import (
    align_asr_chunks,
    build_instruction,
    cues_to_srt,
    manifest_characters,
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

    def test_multi_character_sheet_gets_distinct_subject_contracts(self):
        plan = resolve_duration_plan(100.0, 0.0, "任意秒数", 20.0, 1.0)
        characters = [
            {"id": "akari", "name": "アカリ", "appearance": "red long hair", "role": "lead", "dialogue": "", "actions": "sings by the sea"},
            {"id": "hikari", "name": "ヒカリ", "appearance": "blonde hair and glasses", "role": "friend", "dialogue": "", "actions": "joins the lead"},
        ]
        instruction = build_instruction(
            "おまかせ", "曲名", "style", plan,
            [{"start": 1.0, "end": 3.0, "text": "二人の歌詞"}],
            "2Dアニメ", "", characters, "親友", "夏の思い出", "並んで笑う",
        )
        self.assertIn("<Subject 1> / stable speaker ID (S1) / manifest id=akari", instruction)
        self.assertIn("<Subject 2> / stable speaker ID (S2) / manifest id=hikari", instruction)
        self.assertIn("red long hair", instruction)
        self.assertIn("blonde hair and glasses", instruction)
        self.assertIn("融合・分裂・入れ替えず", instruction)
        self.assertIn("未登録人物や同一人物の分身を作らない", instruction)
        self.assertIn("AUTHORITATIVE_AUDIO_ONLY_VOCALS:", instruction)
        self.assertIn("VOCAL_ACTIVITY_CUE from 00:00:01.000 to 00:00:03.000", instruction)
        self.assertNotIn("二人の歌詞", instruction)
        self.assertNotIn("(S1) <d>[Japanese] 二人の歌詞</d>", instruction)
        self.assertIn(
            "subject_definitions、summary、retention_analysis、detailed_description、overall_soundscape、non_diegetic_music",
            instruction,
        )

    def test_manifest_character_validation_and_legacy_fallback(self):
        self.assertEqual(manifest_characters({"schema_version": 1}), [])
        valid = {
            "visual": {"characters": [
                {"id": "one", "name": "One", "appearance": "red", "role": "lead", "dialogue": "", "actions": "sing"},
                {"id": "two", "name": "Two", "appearance": "blue", "role": "friend", "dialogue": "", "actions": "dance"},
            ]}
        }
        self.assertEqual([item["id"] for item in manifest_characters(valid)], ["one", "two"])
        with self.assertRaises(ValueError):
            manifest_characters({"visual": {"characters": [{"id": "one"}, {"id": "one"}]}})
        with self.assertRaises(ValueError):
            manifest_characters({"visual": {"characters": [{"id": "one"}]}})
        with self.assertRaises(ValueError):
            manifest_characters({"visual": {"characters": []}})

    def test_unknown_style_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            visual_style_instruction("油絵")


if __name__ == "__main__":
    unittest.main()
