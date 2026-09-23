from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


@dataclass(frozen=True)
class DurationPlan:
    source_duration: float
    start_seconds: float
    duration_seconds: float
    fade_out_seconds: float
    mode: str


def resolve_duration_plan(source_duration, start_seconds, mode, requested_seconds, fade_out_seconds):
    source_duration = float(source_duration)
    start_seconds = float(start_seconds)
    requested_seconds = float(requested_seconds)
    fade_out_seconds = float(fade_out_seconds)
    if source_duration <= 0:
        raise ValueError("音源の長さが0秒以下です")
    if start_seconds < 0 or start_seconds >= source_duration:
        raise ValueError("開始秒は0以上、音源末尾より前にしてください")
    remaining = source_duration - start_seconds
    if mode == "音源末尾まで（可変尺）":
        duration = remaining
    elif mode == "任意秒数":
        if requested_seconds <= 0:
            raise ValueError("任意秒数モードでは長さを0秒より大きくしてください")
        duration = min(requested_seconds, remaining)
    else:
        raise ValueError(f"未対応の尺モードです: {mode}")
    if fade_out_seconds < 0:
        raise ValueError("フェード秒数は0以上にしてください")
    fade = min(fade_out_seconds, duration)
    return DurationPlan(source_duration, start_seconds, duration, fade, mode)


def lyric_lines(text):
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"\[[^]]+\]", line):
            continue
        lines.append(line)
    return lines


def _normalise(text):
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    return "".join(ch for ch in text if ch.isalnum())


def align_asr_chunks(chunks, known_lyrics, duration, minimum_ratio=0.34):
    """Map noisy ASR chunks to exact known lyric lines without inventing timing."""
    known = lyric_lines(known_lyrics)
    result = []
    cursor = 0
    duration = float(duration)
    for chunk in chunks or []:
        timestamp = chunk.get("timestamp") or (None, None)
        if len(timestamp) != 2 or timestamp[0] is None:
            continue
        start = max(0.0, float(timestamp[0]))
        end = duration if timestamp[1] is None else min(duration, float(timestamp[1]))
        if end <= start:
            continue
        observed = str(chunk.get("text") or "").strip()
        observed_key = _normalise(observed)
        if not observed_key:
            continue
        best = None
        for index in range(cursor, len(known)):
            ratio = SequenceMatcher(None, observed_key, _normalise(known[index])).ratio()
            if best is None or ratio > best[0]:
                best = (ratio, index, known[index])
            if ratio >= 0.92:
                break
        if best is None or best[0] < minimum_ratio:
            continue
        ratio, index, exact = best
        cursor = index + 1
        result.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": exact,
            "asr_text": observed,
            "match_ratio": round(ratio, 4),
        })
    return result


def _srt_time(seconds):
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def cues_to_srt(cues):
    blocks = []
    for index, cue in enumerate(cues or [], 1):
        blocks.append(
            f"{index}\n{_srt_time(cue['start'])} --> {_srt_time(cue['end'])}\n{cue['text']}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def manifest_characters(manifest):
    """Read the portable character contract, preserving v1 bundle fallback."""
    visual = manifest.get("visual")
    if visual is None:
        return []
    if not isinstance(visual, dict):
        raise ValueError("manifest.visual はJSONオブジェクトで指定してください")
    characters = visual.get("characters")
    if not isinstance(characters, list) or not 1 <= len(characters) <= 12:
        raise ValueError("manifest.visual.characters は1～12人の配列で指定してください")
    fields = ("id", "name", "appearance", "role", "dialogue", "actions")
    result = []
    seen_ids = set()
    for index, character in enumerate(characters, 1):
        if not isinstance(character, dict):
            raise ValueError(f"manifest.visual.characters[{index}] が不正です")
        if not set(fields).issubset(character):
            raise ValueError(f"manifest.visual.characters[{index}] の項目が不足しています")
        item = {}
        for field in fields:
            value = character.get(field, "")
            if not isinstance(value, str):
                raise ValueError(f"manifest.visual.characters[{index}].{field} は文字列で指定してください")
            item[field] = " ".join(value.split())
        if not item["id"] or item["id"] in seen_ids:
            raise ValueError("manifest.visual.characters のidが空または重複しています")
        seen_ids.add(item["id"])
        result.append(item)
    return result


def format_h3_timeline(cues, characters=None):
    characters = list(characters or [])
    if len(characters) <= 1:
        return "\n".join(
            "[{:.3f}-{:.3f}] (S1) <d>[Japanese] {}</d>".format(
                cue["start"], cue["end"], cue["text"]
            )
            for cue in cues or []
        )
    return "\n".join(
        "VOCAL_ACTIVITY_CUE from {start} to {end}: synchronize exactly one visible registered "
        "subject to <Audio 1>; this is timing metadata, not a visual-shot range or dialogue text.".format(
            start=_srt_time(cue["start"]).replace(",", "."),
            end=_srt_time(cue["end"]).replace(",", "."),
        )
        for cue in cues or []
    )


STYLE_MODES = (
    "参照画像に自動追従",
    "2Dアニメ",
    "3D CG",
    "実写",
)


def visual_style_instruction(style_mode):
    rules = {
        "参照画像に自動追従": (
            "MV_VISUAL_STYLE_LOCK: SOURCE_MATCH_AUTO. <Picture 1>の描画媒体を画像から判定し、"
            "2Dアニメ／セル画なら RENDERING_MEDIUM=2D_ANIME、3Dレンダーなら "
            "RENDERING_MEDIUM=3D_CGI、写真なら RENDERING_MEDIUM=PHOTOREAL_LIVE_ACTION を"
            "最終H3プロンプトのsubject_definitionsとdetailed_descriptionへ明記する。"
            "選んだ媒体を全カットで維持し、別媒体へ変換しない。"
        ),
        "2Dアニメ": (
            "MV_VISUAL_STYLE_LOCK: 2D_ANIME. RENDERING_MEDIUM=2D_ANIME を最終H3プロンプトへ明記する。"
            "平面的な2Dセルアニメ、明瞭な線画、セル塗り、手描き調を全カットで維持する。"
            "3D CGI、プラスチック状の立体シェーディング、フォトリアル、実写化は禁止。"
        ),
        "3D CG": (
            "MV_VISUAL_STYLE_LOCK: 3D_CGI. RENDERING_MEDIUM=3D_CGI を最終H3プロンプトへ明記する。"
            "参照画像の3Dモデル、材質、立体照明、レンダー様式を全カットで維持する。"
            "2Dセル画化、手描き化、実写化は禁止。"
        ),
        "実写": (
            "MV_VISUAL_STYLE_LOCK: PHOTOREAL_LIVE_ACTION. "
            "RENDERING_MEDIUM=PHOTOREAL_LIVE_ACTION を最終H3プロンプトへ明記する。"
            "参照写真の実在感、肌・髪・衣装の質感、撮影レンズと自然な照明を全カットで維持する。"
            "2Dアニメ化、イラスト化、3D CGI化は禁止。"
        ),
    }
    if style_mode not in rules:
        raise ValueError(f"未対応の画風維持モードです: {style_mode}")
    return rules[style_mode]


def build_instruction(
    user_instruction,
    title,
    style,
    plan,
    cues,
    visual_style_mode="参照画像に自動追従",
    character_identity_lock="",
    characters=None,
    relationships="",
    story="",
    ending="",
):
    characters = list(characters or [])
    timeline = format_h3_timeline(cues, characters)
    if len(characters) > 1:
        audio_only_rule = (
            "AUTHORITATIVE_AUDIO_ONLY_VOCALS: <Audio 1> is the only vocal performance. "
            "Do not transcribe, quote, invent, or emit lyrics in <d> tags. The exact Japanese text is "
            "reserved for the separate subtitle renderer. Use the timed vocal-activity ranges only to "
            "animate one visible registered subject at a time unless manifest dialogue explicitly assigns a group."
        )
        timeline = audio_only_rule + ("\n" + timeline if timeline else "")
    elif not timeline:
        timeline = (
            "AUTHORITATIVE_AUDIO_ONLY_VOCALS: この区間は歌詞字幕なし。<Audio 1> の歌声だけを同期参照し、"
            "歌詞を推測・転記した <d> タグや別の歌声を追加しない。歌唱していない口を無理に動かさない。"
        )
    identity_notes = " ".join(str(character_identity_lock or "").split())
    identity_instruction = (
        "MV_CHARACTER_IDENTITY_LOCK: " + identity_notes
        if identity_notes
        else (
            "MV_CHARACTER_IDENTITY_LOCK: <Picture 1>の顔の輪郭と比率、目の形・間隔、眉、鼻と口の位置、"
            "顎、髪の長さと形、前髪、頭部アクセサリー、目の色、体格、衣装各部、靴、尻尾を"
            "画面から正確に読み取り、汎用的な別人顔へ置き換えず全カットで固定する。"
        )
    )
    if characters:
        subject_lines = []
        for index, character in enumerate(characters, 1):
            subject_lines.append(
                "<Subject {index}> / stable speaker ID (S{index}) / manifest id={id}: "
                "name={name}; appearance={appearance}; role={role}; dialogue plan={dialogue}; actions={actions}. "
                "This is one distinct person visible in <Picture 1>.".format(
                    index=index,
                    id=character["id"],
                    name=character["name"] or "unspecified",
                    appearance=character["appearance"] or "read exact traits from the sheet",
                    role=character["role"] or "unspecified",
                    dialogue=character["dialogue"] or "not preassigned",
                    actions=character["actions"] or "unspecified",
                )
            )
        subject_contract = "\n".join(subject_lines)
        narrative = "\n".join(
            line for line in (
                f"Relationships: {' '.join(str(relationships or '').split())}" if relationships else "",
                f"Story: {' '.join(str(story or '').split())}" if story else "",
                f"Ending: {' '.join(str(ending or '').split())}" if ending else "",
            ) if line
        )
        casting_instruction = f"""MV_MULTI_CHARACTER_CONTRACT: manifestに登録された{len(characters)}人を、同じ <Picture 1> にある別人物として扱う。
{subject_contract}
同じ人物の表情差分・ポーズ差分・前後左右図は追加人物ではなく、その人物の別ビューである。人物同士を融合・分裂・入れ替えず、髪・顔・眼鏡・衣装・体格とSubject番号を全カットで固定する。未登録人物や同一人物の分身を作らない。
{narrative}
全員を同時に出す必要はないが、尺が許す範囲で定義した各Subjectを少なくとも一度は識別可能に見せる。ソロの歌詞区間で口を動かす人物は必ず一人だけとし、そのカットで選んだSubjectの (S番号) を最終プロンプトに一つだけ付ける。明示的にグループ歌唱を設計した箇所以外で、同じ歌詞を複数の口に割り当てない。音声だけから声質と人物の対応を捏造せず、manifestのdialogue planが明示する場合のみ優先する。"""
    else:
        casting_instruction = """参照画像の同じ人物を一人だけ登場させ、顔の輪郭と各パーツの比率・配置、髪型、頭部の特徴、衣装、アクセサリーを最後まで維持する。髪色や衣装だけ似た汎用顔へ置き換えず、各生成区間に少なくとも一度は顔を比較できる中近景または近景を入れる。人物の分身を作らない。
歌唱者は参照画像の人物 <Subject 1>、安定話者IDは (S1) とする。以下の各歌詞行では必ずこの (S1) を直前に置き、別の話者IDへ変更しない。"""
    return f"""{str(user_instruction).strip()}

曲名: {title}
曲の情報: {style}
対象区間: 元音源の {plan.start_seconds:.3f} 秒から {plan.duration_seconds:.3f} 秒間。
{casting_instruction}
{identity_instruction}
{visual_style_instruction(visual_style_mode)}
<Audio 1> は元曲から切り出した同期用音声である。歌声とリズムを参照し、歌唱中は口形・表情・呼吸・身体のリズムを音声に同期させる。映像内へ字幕・ロゴ・文字を生成しない（字幕は後段で正確に合成する）。
MVの映像内容、場所、照明、カメラ、色調、カット割りは、曲とキャラクターに似合うよう自由に設計する。歌詞の意味を映像で説明しすぎず、音楽映像として気持ちよい変化を付ける。
以下は音声解析で元曲に整列した歌唱時刻。複数人物では字幕用歌詞をH3へ転記せず、音声だけに同期する。
{timeline}
最終H3プロンプトは英語で、subject_definitions、summary、retention_analysis、detailed_description、overall_soundscape、non_diegetic_music の6セクションをこの順序で一度ずつ出力する。subject_definitionsで上記の各Subjectを別々に定義し、detailed_descriptionの全カットで同じSubjectラベルを使う。<Audio 1> を再利用する権威ある同期音源として明記し、別の歌声・台詞・音楽を生成しない。
""".strip()


def load_bundle_manifest(bundle_folder):
    folder = Path(bundle_folder).expanduser().resolve()
    manifest_path = folder / "mv_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"mv_manifest.json がありません: {folder}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "comfyui.mv_asset_bundle":
        raise ValueError("対応していないMV素材バンドルです")
    version = manifest.get("schema_version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 2:
        raise ValueError(f"対応していないMV素材バンドルschema_versionです: {version}")
    manifest_characters(manifest)
    audio_name = manifest.get("audio", {}).get("file")
    lyrics_name = manifest.get("lyrics", {}).get("display_file")
    if not audio_name or not lyrics_name:
        raise ValueError("manifestにaudio.fileまたはlyrics.display_fileがありません")
    audio_path = (folder / audio_name).resolve()
    lyrics_path = (folder / lyrics_name).resolve()
    if not audio_path.is_file() or not lyrics_path.is_file():
        raise FileNotFoundError("manifestが参照する音源または歌詞ファイルがありません")
    return folder, manifest, audio_path, lyrics_path
