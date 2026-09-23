from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing_extensions import override

import folder_paths
import numpy as np
import soundfile as sf
import torch
from PIL import Image, ImageOps
from comfy_api.latest import ComfyExtension, InputImpl, io, ui

from .mv_core import (
    align_asr_chunks,
    build_instruction,
    cues_to_srt,
    load_bundle_manifest,
    manifest_characters,
    resolve_duration_plan,
)


DEFAULT_ASR_MODEL_PATH = "models/whisper/whisper-large-v3-turbo"
_ASR = {}


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_image(path):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        array = np.asarray(image, dtype=np.float32) / 255.0
    return torch.from_numpy(array)[None, ...]


def _load_audio_slice(path, start_seconds, duration_seconds):
    info = sf.info(str(path))
    start = round(float(start_seconds) * info.samplerate)
    count = round(float(duration_seconds) * info.samplerate)
    data, rate = sf.read(
        str(path), start=start, frames=count, dtype="float32", always_2d=True
    )
    if len(data) == 0:
        raise ValueError("指定区間から音声を読み出せませんでした")
    waveform = torch.from_numpy(data.T.copy())[None, ...]
    return {"waveform": waveform, "sample_rate": int(rate)}, info


def _resolve_comfy_path(value, *, default=""):
    raw = str(value or default).strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(folder_paths.base_path) / path
    return path.resolve()


def _resolve_bundle_folder(value):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("MV素材バンドルフォルダーを指定してください")
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    output = Path(folder_paths.get_output_directory()).resolve()
    candidates = [output / path]
    if not path.parts or path.parts[0] != "mv-assets":
        candidates.append(output / "mv-assets" / path)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return candidates[0].resolve()


def _image_sha256(image):
    data = image.detach().to(device="cpu", dtype=torch.float32).contiguous().numpy()
    return hashlib.sha256(data.tobytes()).hexdigest()


def _recognizer(model_path):
    model_path = _resolve_comfy_path(model_path, default=DEFAULT_ASR_MODEL_PATH)
    if model_path is None or not model_path.is_dir():
        raise RuntimeError(
            "字幕整列用Whisperモデルがありません: "
            f"{model_path or DEFAULT_ASR_MODEL_PATH}。READMEのモデル配置手順を確認してください。"
        )
    cache_key = str(model_path)
    if cache_key not in _ASR:
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

        processor = AutoProcessor.from_pretrained(
            model_path, local_files_only=True, trust_remote_code=False
        )
        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_path,
            local_files_only=True,
            trust_remote_code=False,
            dtype=torch.float32,
            attn_implementation="sdpa",
        )
        _ASR[cache_key] = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=torch.device("cpu"),
        )
    return _ASR[cache_key]


def _transcribe_chunks(audio, model_path):
    import torchaudio.functional

    wave = audio["waveform"].detach().to(device="cpu", dtype=torch.float32)[0].mean(0)
    rate = int(audio["sample_rate"])
    if rate != 16000:
        wave = torchaudio.functional.resample(wave, rate, 16000)
    window = 28 * 16000
    overlap = int(0.5 * 16000)
    chunks = []
    offset = 0
    recognizer = _recognizer(model_path)
    previous_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(min(4, previous_threads))
        while offset < len(wave):
            sample = wave[offset:min(len(wave), offset + window)]
            result = recognizer(
                {"array": sample.numpy(), "sampling_rate": 16000},
                return_timestamps=True,
                generate_kwargs={
                    "language": "japanese",
                    "task": "transcribe",
                    "condition_on_prev_tokens": False,
                    "num_beams": 1,
                    "max_new_tokens": 256,
                },
            )
            base = offset / 16000
            for item in result.get("chunks", []):
                start, end = item.get("timestamp") or (None, None)
                if start is None:
                    continue
                absolute_start = base + float(start)
                absolute_end = None if end is None else base + float(end)
                if chunks and absolute_start < chunks[-1]["timestamp"][1] - 0.15:
                    continue
                chunks.append(
                    {"timestamp": (absolute_start, absolute_end), "text": item.get("text", "")}
                )
            if offset + window >= len(wave):
                break
            offset += window - overlap
    finally:
        torch.set_num_threads(previous_threads)
    return chunks


class MVAssetBundlePrepare(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MVAssetBundlePrepare",
            display_name="MV素材バンドル読込・可変尺・字幕整列",
            category="MV Workflow",
            description="MV素材バンドルを読み、任意区間または音源末尾までの音声、字幕、H3用LM指示を作ります。",
            inputs=[
                io.String.Input(
                    "bundle_folder",
                    display_name="MV素材バンドル（outputからの相対パス／実パス）",
                    multiline=False,
                    default="mv-assets/YOUR_BUNDLE_FOLDER",
                ),
                io.String.Input(
                    "character_sheet_path",
                    display_name="旧方式：キャラクターシート実パス（画像接続時は空欄）",
                    multiline=False,
                    default="",
                ),
                io.Image.Input(
                    "character",
                    display_name="キャラクターシート画像（推奨）",
                    optional=True,
                ),
                io.String.Input(
                    "llm_instruction",
                    display_name="LM StudioへのMV演出指示",
                    multiline=True,
                    default="MVの雰囲気や演出は全部おまかせ",
                ),
                io.Combo.Input(
                    "duration_mode",
                    display_name="尺モード",
                    options=["音源末尾まで（可変尺）", "任意秒数"],
                    default="任意秒数",
                ),
                io.Float.Input("start_seconds", display_name="元音源の開始位置（秒）", default=0.0, min=0.0, max=86400.0, step=0.5),
                io.Float.Input("duration_seconds", display_name="任意の長さ（秒）", default=10.0, min=0.1, max=86400.0, step=0.5),
                io.Float.Input("fade_out_seconds", display_name="末尾フェード（秒）", default=1.0, min=0.0, max=30.0, step=0.1),
                io.Combo.Input(
                    "subtitle_mode",
                    display_name="字幕時刻",
                    options=["音声解析で整列（推奨）", "既存整列データのみ", "字幕なし"],
                    default="音声解析で整列（推奨）",
                ),
                io.String.Input(
                    "whisper_model_path",
                    display_name="字幕整列用Whisper（ComfyUIからの相対パス／実パス）",
                    multiline=False,
                    default=DEFAULT_ASR_MODEL_PATH,
                ),
                io.Combo.Input(
                    "visual_style_mode",
                    display_name="画風維持（参照画像→MV）",
                    options=["参照画像に自動追従", "2Dアニメ", "3D CG", "実写"],
                    default="参照画像に自動追従",
                ),
                io.String.Input(
                    "character_identity_lock",
                    display_name="人物固定特徴（髪型・顔・衣装など／空欄は画像自動追従）",
                    multiline=True,
                    default="",
                ),
            ],
            outputs=[
                io.Image.Output("character", display_name="H3参照キャラクター"),
                io.Audio.Output("timeline_audio", display_name="元曲タイムライン音声"),
                io.String.Output("lm_instruction", display_name="LM Studioへ渡すMV指示"),
                io.Float.Output("target_duration", display_name="実際の出力秒数"),
                io.Float.Output("source_start", display_name="元音源の開始秒"),
                io.Float.Output("fade_out", display_name="フェード秒数"),
                io.String.Output("source_audio_path", display_name="元音源パス"),
                io.String.Output("subtitle_srt", display_name="整列済み字幕SRT"),
                io.String.Output("title", display_name="曲名"),
                io.String.Output("execution_plan", display_name="実行計画・検査情報"),
            ],
        )

    @classmethod
    def execute(
        cls,
        bundle_folder,
        character_sheet_path,
        llm_instruction,
        duration_mode,
        start_seconds,
        duration_seconds,
        fade_out_seconds,
        subtitle_mode,
        whisper_model_path,
        visual_style_mode,
        character_identity_lock,
        character=None,
    ):
        folder, manifest, audio_path, lyrics_path = load_bundle_manifest(
            _resolve_bundle_folder(bundle_folder)
        )
        characters = manifest_characters(manifest)
        visual = manifest.get("visual") or {}
        narrative = {}
        for field in ("relationships", "story", "ending"):
            value = visual.get(field, "")
            if not isinstance(value, str):
                raise ValueError(f"manifest.visual.{field} は文字列で指定してください")
            narrative[field] = " ".join(value.split())
        if character is not None:
            if not isinstance(character, torch.Tensor) or character.ndim != 4 or character.shape[0] < 1:
                raise ValueError("キャラクターシート画像が不正です")
            character_image = character[:1]
            character_source = "workflow IMAGE input"
            character_sha256 = _image_sha256(character_image)
        else:
            character_path = _resolve_comfy_path(character_sheet_path)
            if character_path is None or not character_path.is_file():
                raise FileNotFoundError(
                    "キャラクターシート画像を接続するか、旧方式の実パスを指定してください: "
                    f"{character_path or '(未指定)'}"
                )
            character_image = _load_image(character_path)
            character_source = str(character_path)
            character_sha256 = _sha256(character_path)
        audio_info = sf.info(str(audio_path))
        plan = resolve_duration_plan(
            audio_info.duration,
            start_seconds,
            duration_mode,
            duration_seconds,
            fade_out_seconds,
        )
        audio, _ = _load_audio_slice(audio_path, plan.start_seconds, plan.duration_seconds)
        display_lyrics = lyrics_path.read_text(encoding="utf-8")
        timing_status = manifest.get("lyrics", {}).get("timing_status")
        cues = []
        asr_chunks = []
        if subtitle_mode == "音声解析で整列（推奨）":
            asr_chunks = _transcribe_chunks(audio, whisper_model_path)
            cues = align_asr_chunks(asr_chunks, display_lyrics, plan.duration_seconds)
            if display_lyrics.strip() and not cues:
                raise RuntimeError("音声解析と既知歌詞を対応付けられず、字幕時刻を確定できませんでした")
        elif subtitle_mode == "既存整列データのみ":
            lyrics_json = json.loads((folder / manifest["lyrics"]["file"]).read_text(encoding="utf-8"))
            if timing_status != "aligned" or not lyrics_json.get("segments"):
                raise RuntimeError("歌詞は未整列です。『音声解析で整列』を選んでください")
            end_source = plan.start_seconds + plan.duration_seconds
            for item in lyrics_json["segments"]:
                start = float(item["start"])
                end = float(item["end"])
                if end <= plan.start_seconds or start >= end_source:
                    continue
                cues.append({
                    "start": max(0.0, start - plan.start_seconds),
                    "end": min(plan.duration_seconds, end - plan.start_seconds),
                    "text": item["text"],
                    "asr_text": item["text"],
                    "match_ratio": 1.0,
                })
        title = str(manifest.get("title") or folder.name)
        instruction = build_instruction(
            llm_instruction,
            title,
            str(manifest.get("style") or ""),
            plan,
            cues,
            visual_style_mode,
            character_identity_lock,
            characters,
            narrative["relationships"],
            narrative["story"],
            narrative["ending"],
        )
        report = {
            "schema": "comfyui.mv_execution_plan",
            "schema_version": 1,
            "bundle": str(folder),
            "bundle_id": manifest.get("bundle_id"),
            "manifest_schema_version": manifest.get("schema_version", 1),
            "multi_character_mode": len(characters) > 1,
            "character_count": len(characters) if characters else 1,
            "characters": [
                {"subject": f"Subject {index}", "speaker_id": f"S{index}", "id": item["id"], "name": item["name"]}
                for index, item in enumerate(characters, 1)
            ],
            "title": title,
            "source_audio": str(audio_path),
            "source_audio_sha256": _sha256(audio_path),
            "character_sheet": character_source,
            "character_sheet_sha256": character_sha256,
            "duration_mode": plan.mode,
            "source_duration_seconds": plan.source_duration,
            "source_start_seconds": plan.start_seconds,
            "target_duration_seconds": plan.duration_seconds,
            "fade_out_seconds": plan.fade_out_seconds,
            "subtitle_mode": subtitle_mode,
            "visual_style_mode": visual_style_mode,
            "character_identity_lock": " ".join(str(character_identity_lock or "").split()),
            "source_timing_status": timing_status,
            "subtitle_cue_count": len(cues),
            "subtitle_cues": cues,
            "asr_chunk_count": len(asr_chunks),
            "lip_sync_reference": "per-segment timeline audio",
        }
        return io.NodeOutput(
            character_image,
            audio,
            instruction,
            plan.duration_seconds,
            plan.start_seconds,
            plan.fade_out_seconds,
            str(audio_path),
            cues_to_srt(cues),
            title,
            json.dumps(report, ensure_ascii=False, indent=2),
        )


def _safe_name(value):
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", str(value)).strip(" ._")
    return value[:80] or "MV"


def _subtitle_filter(path):
    escaped = str(path).replace("\\", "/").replace("'", "\\'").replace(":", "\\:")
    font_candidates = (
        (Path("/usr/share/fonts/opentype/noto"), "Noto Sans CJK JP"),
        (Path("/usr/share/fonts/opentype/noto-cjk"), "Noto Sans CJK JP"),
        (Path("/usr/share/fonts/truetype/droid"), "Droid Sans Fallback"),
        (Path("/usr/share/fonts/truetype/noto"), "Noto Sans Mono"),
        (Path("/usr/share/fonts"), "sans-serif"),
    )
    fonts, font_name = next(
        ((folder, name) for folder, name in font_candidates if folder.is_dir()),
        (Path("/usr/share/fonts"), "sans-serif"),
    )
    style = (
        f"FontName={font_name},FontSize=20,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00101010,BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=42"
    )
    return f"subtitles=filename='{escaped}':fontsdir='{fonts}':force_style='{style}'"


def _filter_path(path):
    return str(path).replace("\\", "/").replace("'", "\\'").replace(":", "\\:")


def _overlay_font_path():
    for path in (
        Path("/mnt/c/Windows/Fonts/meiryo.ttc"),
        Path("/mnt/c/Windows/Fonts/YuGothM.ttc"),
        Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ):
        if path.is_file():
            return path
    raise RuntimeError("タイトル・リンク表示に使えるフォントがありません")


def _title_drawtext(text_path, font_path, show_seconds, fade_seconds):
    show_seconds = max(0.0, float(show_seconds))
    fade_seconds = min(max(0.05, float(fade_seconds)), max(0.05, show_seconds))
    hold_end = max(0.0, show_seconds - fade_seconds)
    alpha = (
        "if(lt(t\\,0.25)\\,t/0.25\\,"
        f"if(lt(t\\,{hold_end:.6f})\\,1\\,max(0\\,({show_seconds:.6f}-t)/{fade_seconds:.6f})))"
    )
    return (
        f"drawtext=textfile='{_filter_path(text_path)}':fontfile='{_filter_path(font_path)}':"
        "fontsize=h*0.072:fontcolor=black:borderw=2:bordercolor=white@0.95:"
        f"x=w*0.025:y=h*0.035:alpha='{alpha}':enable='between(t,0,{show_seconds:.6f})'"
    )


def _end_credit_drawtext(text_path, font_path, start_seconds, duration_seconds):
    start_seconds = max(0.0, float(start_seconds))
    duration_seconds = max(start_seconds, float(duration_seconds))
    alpha = f"min(1\\,max(0\\,(t-{start_seconds:.6f})/0.35))"
    return (
        f"drawtext=textfile='{_filter_path(text_path)}':fontfile='{_filter_path(font_path)}':"
        "fontsize=h*0.042:fontcolor=white:line_spacing=8:borderw=2:bordercolor=black@0.95:"
        "box=1:boxcolor=black@0.60:boxborderw=14:"
        f"x=w-tw-w*0.025:y=h-th-h*0.045:alpha='{alpha}':"
        f"enable='between(t,{start_seconds:.6f},{duration_seconds:.6f})'"
    )


def _probe(path):
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,duration",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class MVFinalize(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MVFinalize",
            display_name="MV仕上げ：元曲・字幕・タイトル・リンク・フェード",
            category="MV Workflow",
            description="H3映像へ元曲を再合成し、字幕、左上タイトル、終盤右下リンク、末尾フェードを適用します。",
            inputs=[
                io.String.Input("generated_master_path", display_name="H3生成マスター（自動入力）", multiline=False),
                io.String.Input("source_audio_path", display_name="元音源（自動入力）", multiline=False),
                io.String.Input("subtitle_srt", display_name="整列済み字幕（自動入力）", multiline=True),
                io.String.Input("title", display_name="曲名（自動入力）", multiline=False),
                io.Float.Input("source_start", display_name="元音源開始秒（自動入力）", default=0.0, min=0.0, max=86400.0),
                io.Float.Input("target_duration", display_name="出力秒数（自動入力）", default=10.0, min=0.1, max=86400.0),
                io.Float.Input("fade_out", display_name="末尾フェード秒（自動入力）", default=1.0, min=0.0, max=30.0),
                io.Float.Input("title_seconds", display_name="左上タイトル表示（秒）", default=3.7, min=0.0, max=30.0, step=0.1),
                io.Float.Input("title_fade_seconds", display_name="タイトルのフェードアウト（秒）", default=0.8, min=0.1, max=10.0, step=0.1),
                io.Float.Input("end_link_seconds", display_name="右下リンク表示（末尾から秒）", default=3.0, min=0.0, max=30.0, step=0.1),
                io.String.Input("end_credit_line", display_name="リンク上の説明", default="ネームから全自動の自律式統合AI漫画システム", multiline=False),
                io.String.Input("end_link_url", display_name="右下リンク", default="https://note.com/happy_duck780", multiline=False),
                io.String.Input("filename_prefix", display_name="保存先（outputからの相対）", default="video/MV", multiline=False),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
            is_output_node=True,
            outputs=[
                io.Video.Output("video", display_name="完成MV"),
                io.String.Output("final_path", display_name="完成MVパス"),
                io.String.Output("verification", display_name="仕上げ検査結果"),
            ],
        )

    @classmethod
    def execute(
        cls,
        generated_master_path,
        source_audio_path,
        subtitle_srt,
        title,
        source_start,
        target_duration,
        fade_out,
        title_seconds,
        title_fade_seconds,
        end_link_seconds,
        end_credit_line,
        end_link_url,
        filename_prefix,
    ):
        generated = Path(generated_master_path).expanduser().resolve()
        source_audio = Path(source_audio_path).expanduser().resolve()
        if not generated.is_file():
            raise FileNotFoundError(f"H3生成マスターがありません: {generated}")
        if not source_audio.is_file():
            raise FileNotFoundError(f"元音源がありません: {source_audio}")
        target_duration = float(target_duration)
        fade_out = min(float(fade_out), target_duration)
        fade_start = max(0.0, target_duration - fade_out)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        relative_folder = Path(str(filename_prefix).strip(" /\\") or "video/MV")
        output_folder = Path(folder_paths.get_output_directory()) / relative_folder / f"{_safe_name(title)}_{timestamp}"
        output_folder.mkdir(parents=True, exist_ok=False)
        srt_path = output_folder / "subtitles.srt"
        report_path = output_folder / "mv_report.json"
        output_path = output_folder / "final.mp4"
        srt_path.write_text(str(subtitle_srt or ""), encoding="utf-8")
        title_path = output_folder / "title.txt"
        end_credit_path = output_folder / "end_link.txt"
        title_path.write_text(str(title or "").strip(), encoding="utf-8")
        end_lines = [str(end_credit_line or "").strip(), str(end_link_url or "").strip()]
        end_credit_path.write_text("\n".join(line for line in end_lines if line), encoding="utf-8")
        font_path = _overlay_font_path()
        title_seconds = min(max(0.0, float(title_seconds)), target_duration)
        title_fade_seconds = min(max(0.1, float(title_fade_seconds)), max(0.1, title_seconds))
        end_link_seconds = min(max(0.0, float(end_link_seconds)), target_duration)
        end_link_start = max(0.0, target_duration - end_link_seconds)

        video_filters = [
            "tpad=stop_mode=clone:stop_duration=2",
            f"trim=duration={target_duration:.6f}",
            "setpts=PTS-STARTPTS",
        ]
        if subtitle_srt.strip():
            video_filters.append(_subtitle_filter(srt_path))
        if title_path.read_text(encoding="utf-8").strip() and title_seconds > 0:
            video_filters.append(_title_drawtext(title_path, font_path, title_seconds, title_fade_seconds))
        if fade_out > 0:
            video_filters.append(f"fade=t=out:st={fade_start:.6f}:d={fade_out:.6f}:color=black")
        if end_credit_path.read_text(encoding="utf-8").strip() and end_link_seconds > 0:
            video_filters.append(_end_credit_drawtext(end_credit_path, font_path, end_link_start, target_duration))
        audio_filters = [f"atrim=duration={target_duration:.6f}", "asetpts=PTS-STARTPTS"]
        if fade_out > 0:
            audio_filters.append(f"afade=t=out:st={fade_start:.6f}:d={fade_out:.6f}")
        filter_complex = "[0:v]{}[v];[1:a]{}[a]".format(
            ",".join(video_filters), ",".join(audio_filters)
        )
        command = [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-i", str(generated),
            "-ss", f"{float(source_start):.6f}", "-t", f"{target_duration:.6f}", "-i", str(source_audio),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "24",
            "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
            "-t", f"{target_duration:.6f}", "-movflags", "+faststart", str(output_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError("MV仕上げFFmpegが失敗しました:\n" + completed.stderr[-4000:])
        probe = _probe(output_path)
        actual_duration = float(probe.get("format", {}).get("duration") or 0.0)
        tolerance = max(0.08, 1.0 / 24.0 + 0.02)
        if abs(actual_duration - target_duration) > tolerance:
            raise RuntimeError(
                f"完成MVの尺が指定と一致しません: target={target_duration}, actual={actual_duration}"
            )
        streams = probe.get("streams", [])
        if not any(item.get("codec_type") == "video" for item in streams):
            raise RuntimeError("完成MVに映像ストリームがありません")
        if not any(item.get("codec_type") == "audio" for item in streams):
            raise RuntimeError("完成MVに音声ストリームがありません")
        report = {
            "schema": "comfyui.mv_final_report",
            "schema_version": 1,
            "generated_master": str(generated),
            "source_audio": str(source_audio),
            "source_start_seconds": float(source_start),
            "target_duration_seconds": target_duration,
            "actual_duration_seconds": actual_duration,
            "fade_out_seconds": fade_out,
            "fade_start_seconds": fade_start,
            "video_fade": "ffmpeg fade to black",
            "audio_fade": "ffmpeg afade to silence",
            "subtitles_burned": bool(subtitle_srt.strip()),
            "subtitle_file": str(srt_path),
            "title_overlay": {
                "enabled": bool(title_path.read_text(encoding="utf-8").strip()) and title_seconds > 0,
                "text": str(title or "").strip(),
                "show_seconds": title_seconds,
                "fade_out_seconds": title_fade_seconds,
                "position": "top_left",
            },
            "end_link_overlay": {
                "enabled": bool(end_credit_path.read_text(encoding="utf-8").strip()) and end_link_seconds > 0,
                "credit_line": str(end_credit_line or "").strip(),
                "url": str(end_link_url or "").strip(),
                "start_seconds": end_link_start,
                "show_seconds": end_link_seconds,
                "position": "bottom_right",
                "overlaps_video_fade": True,
            },
            "output": str(output_path),
            "probe": probe,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        relative = output_path.relative_to(Path(folder_paths.get_output_directory()))
        subfolder = relative.parent.as_posix()
        video = InputImpl.VideoFromFile(str(output_path))
        return io.NodeOutput(
            video,
            str(output_path),
            json.dumps(report, ensure_ascii=False, indent=2),
            ui=ui.PreviewVideo([ui.SavedResult(relative.name, subfolder, io.FolderType.output)]),
        )


class MVWorkflowExtension(ComfyExtension):
    @override
    async def get_node_list(self):
        return [MVAssetBundlePrepare, MVFinalize]


async def comfy_entrypoint():
    return MVWorkflowExtension()
