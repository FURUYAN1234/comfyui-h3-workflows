# SPDX-License-Identifier: GPL-3.0-only
"""Validated, opt-in variable dialogue windows; legacy timelines are unchanged."""
import hashlib
import math
import re
import unicodedata
import json

from .timeline import FPS, Segment, _h3_grid_frames

SCHEMA = "fourpanel-dialogue-timing-v1"
TAG = re.compile(r"<d>\s*(?:\[[^\]]+\]\s*)?(.*?)\s*</d>", re.I | re.S)


def spoken_lines(prompt):
    return [m.group(1).strip() for m in TAG.finditer(prompt)
            if any(unicodedata.category(c)[0] in "LN" for c in m.group(1))]


def plan_confirmed_windows(windows, context_frames, has_initial_latent):
    if not isinstance(windows, list) or not windows:
        raise ValueError("確定台詞の区間設計が空です。")
    if context_frames not in (22, 39):
        raise ValueError("context_frames must be 22 or 39")
    result, start = [], 0
    for index, frames in enumerate(windows):
        if type(frames) is not int or frames % FPS or not 5 * FPS <= frames <= 15 * FPS:
            raise ValueError("確定台詞の区間尺は5〜15秒の整数秒で指定してください。")
        context = context_frames if index or has_initial_latent else 0
        result.append(Segment(index, _h3_grid_frames(frames + context), context, start, frames))
        start += frames
    return result


def require_timing_connection(prompt, timing, prompt_plan=None):
    if "CONFIRMED SPEECH TIMING:" in prompt and timing is None and prompt_plan is None:
        raise ValueError("確定台詞の秒数が未接続です。読み確認のDIALOGUE_TIMINGを生成ノードへ接続してください。")


def validate_timing(timing, prompt):
    if not isinstance(timing, dict) or timing.get("schema") != SCHEMA:
        raise ValueError("台詞の尺設計が不正です。読み確認から再実行してください。")
    if timing.get("prompt_sha256") != hashlib.sha256(prompt.encode("utf-8")).hexdigest():
        raise ValueError("確認後にH3プロンプトが変わっています。台詞の読み・尺を再確認してください。")
    turns = timing.get("turns")
    lines = spoken_lines(prompt)
    if (not isinstance(turns, list) or len(turns) != len(lines)
            or any(not isinstance(row, dict) or type(row.get("index")) is not int or row.get("index") != i
                   or row.get("reading") != lines[i] for i, row in enumerate(turns))):
        raise ValueError("確定した台詞の順序・内容と尺設計が一致しません。")
    windows = [row.get("output_frames") for row in turns]
    if any(type(v) is not int for v in windows) or timing.get("total_frames") != sum(windows):
        raise ValueError("確定台詞の合計尺が一致しません。")
    for row, frames in zip(turns, windows):
        target = row.get("speech_deadline_seconds")
        if target is not None and (type(target) not in (int, float) or not math.isfinite(target)
                                   or not 0.5 < target <= frames / FPS - 1.0):
            raise ValueError("発話終了の目安は区間内に収め、最後の余白を残してください。")
    return windows


def validate_local_dialogues(local_prompts, timing):
    turns = timing["turns"]
    if len(local_prompts) != len(turns):
        raise ValueError("台詞と生成区間の数が一致しません。")
    for i, (prompt, row) in enumerate(zip(local_prompts, turns)):
        if spoken_lines(prompt) != [row["reading"]]:
            raise ValueError(f"区間{i+1}の台詞が確認内容と一致しません。分割・重複した状態では生成しません。")


def recovery_segments(source_segments, extensions):
    """Apply bounded per-turn capacity repairs, retaining source context choices."""
    result, start = [], 0
    for source in source_segments:
        extra = extensions.get(str(source.index), 0)
        if type(extra) is not int or not 0 <= extra <= 2:
            raise ValueError('Invalid saved dialogue duration repair')
        frames = source.output_frames + extra * FPS
        if frames > 15 * FPS:
            raise ValueError('Dialogue duration repair exceeds 15 seconds')
        result.append(Segment(source.index, _h3_grid_frames(frames + source.context_frames),
                              source.context_frames, start, frames))
        start += frames
    return result


def needs_duration_repair(status):
    """Read structured observations, never infer a cutoff from arbitrary prose."""
    if status.startswith('fail: audiovisual:'):
        try:
            status = json.loads(status.split('fail: audiovisual:', 1)[1]).get('audio', '')
        except (ValueError, TypeError):
            return False
    if not status.startswith('fail: dialogue audio:'):
        return False
    try:
        data = json.loads(status.split('fail: dialogue audio:', 1)[1])
    except (ValueError, TypeError):
        return False
    acoustic = data.get('acoustic')
    if not isinstance(acoustic, dict):
        return False
    issue_kinds = {
        issue.get('kind')
        for verdict in (data.get('verdict'), acoustic)
        if isinstance(verdict, dict)
        for issue in verdict.get('issues', [])
        if isinstance(issue, dict) and issue.get('kind')
    }
    if issue_kinds - {'truncated_speech', 'insufficient_speech_margin'}:
        return False
    # Word-timestamp margin alone is not evidence of a clipped audible ending.
    ending = acoustic.get('ending', {})
    return ending.get('status') == 'cut_off' and any(
        isinstance(issue, dict) and issue.get('kind') == 'truncated_speech'
        for issue in acoustic.get('issues', []))
