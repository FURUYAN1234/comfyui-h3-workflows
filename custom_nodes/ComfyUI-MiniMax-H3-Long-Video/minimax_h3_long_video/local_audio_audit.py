"""Local scripted-speech guard. Transcription is not a general audio-quality verdict."""
import difflib
import json
import math
import os
from pathlib import Path
import re
import threading
import unicodedata

_LOCK = threading.RLock()
_ASR = None
_ASR_PATH = None


def normalize(text):
    return ''.join(c.lower() for c in unicodedata.normalize('NFKC', text or '') if c.isalnum())


def expected_lines(prompt):
    return [re.sub(r'^\s*\[[^]]+\]\s*', '', value).strip()
            for value in re.findall(r'<d>(.*?)</d>', prompt, re.I | re.S)]


def _script_match(expected, actual):
    matcher = difflib.SequenceMatcher(None, expected, actual, autojunk=False)
    coverage = sum(block.size for block in matcher.get_matching_blocks()) / max(1, len(expected))
    if matcher.ratio() >= 0.75 and coverage >= 0.65:
        return True, matcher.ratio() + coverage
    kana_script = any('\u3041' <= char <= '\u3096' for char in expected)
    kanji_asr = any('\u4e00' <= char <= '\u9fff' for char in actual)
    compact_ratio = len(actual) / max(1, len(expected))
    matched = (kana_script and kanji_asr and 0.45 <= compact_ratio <= 1.05
               and matcher.ratio() >= 0.55 and coverage >= 0.5)
    return matched, matcher.ratio() + coverage


def _script_span(chunks, expected):
    best = None
    for start in range(len(chunks)):
        text = ''
        for end in range(start, len(chunks)):
            text += normalize(chunks[end].get('text', ''))
            if len(text) > len(expected) * 1.5:
                break
            matched, score = _script_match(expected, text)
            if matched and (best is None or score > best[0]):
                best = (score, start, end)
    return None if best is None else best[1:]


def repeated_speech_cutoff(verdict, prompt):
    """Return the second matching utterance start when two ASR contexts agree.

    Padding-shifted recognitions are already produced by :func:`audit`.  Requiring
    two of those independent timing contexts keeps this recovery from muting a
    single uncertain transcription.  The returned time is relative to delivered
    audio, rather than to the recognizer's padded window.
    """
    expected = normalize(''.join(expected_lines(prompt)))
    if not expected or not isinstance(verdict, dict):
        return None
    issues = verdict.get('issues') or []
    if not issues or any(row.get('kind') not in ('repeated_speech', 'unscripted_speech') for row in issues):
        return None
    cutoffs = []
    for observation in verdict.get('word_observations') or verdict.get('observations') or []:
        if verdict.get('word_observations') and not _valid_timestamps(
                observation.get('result') or {}, observation.get('duration')):
            continue
        padding = observation.get('padding_seconds', 0.0)
        chunks = (observation.get('result') or {}).get('chunks') or []
        matches = []
        start = 0
        while start < len(chunks):
            text, best = '', None
            for end in range(start, len(chunks)):
                text += normalize(chunks[end].get('text', ''))
                if len(text) > len(expected) * 1.5:
                    break
                matcher = difflib.SequenceMatcher(None, expected, text, autojunk=False)
                ratio = matcher.ratio()
                coverage = sum(block.size for block in matcher.get_matching_blocks()) / len(expected)
                if ratio >= 0.75 and coverage >= 0.65 and (best is None or ratio > best[0]):
                    best = (ratio, end)
            stamp = chunks[start].get('timestamp')
            if best is None or not stamp or not isinstance(stamp[0], (int, float)):
                start += 1
                continue
            matches.append(max(0.0, float(stamp[0]) - float(padding)))
            start = best[1] + 1
        if len(matches) >= 2 and matches[1] >= 0.5:
            cutoffs.append(matches[1])
    if len(cutoffs) < 2 or max(cutoffs) - min(cutoffs) > 0.75:
        # ASR may spell the same Japanese line with different kanji/kana in
        # each chunk.  When the verdict already corroborates repetition, use
        # the second near-matching chunk boundary as the repair cutoff.
        fallback = []
        for observation in verdict.get('observations') or []:
            padding = observation.get('padding_seconds', 0.0)
            chunks = [c for c in (observation.get('result') or {}).get('chunks', [])
                      if c.get('text') and c.get('timestamp') and isinstance(c['timestamp'][0], (int, float))]
            for first, second in zip(chunks, chunks[1:]):
                a = normalize(first.get('text', ''))
                b = normalize(second.get('text', ''))
                if a and b and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.55:
                    fallback.append(max(0.0, float(second['timestamp'][0]) - float(padding)))
                    break
        if len(fallback) >= 2 and max(fallback) - min(fallback) <= 0.75:
            fallback.sort()
            return fallback[len(fallback) // 2]
        return None
    cutoffs.sort()
    return cutoffs[len(cutoffs) // 2]


def unprescribed_speech_cutoff(verdict, prompt):
    """Locate stable speech appended after one complete scripted line."""
    lines = expected_lines(prompt)
    if len(lines) != 1 or not isinstance(verdict, dict):
        return None
    expected = normalize(lines[0])
    cutoffs = []
    for observation in verdict.get('word_observations') or []:
        result = observation.get('result') or {}
        if not _valid_timestamps(result, observation.get('duration')):
            continue
        chunks = result.get('chunks') or []
        span = _script_span(chunks, expected)
        if span is None or span[1] + 1 >= len(chunks):
            continue
        extra = normalize(''.join(row.get('text', '') for row in chunks[span[1] + 1:]))
        if len(extra) < 4 or set(extra) <= _NONLEXICAL:
            continue
        stamp = chunks[span[1] + 1].get('timestamp')
        if stamp and isinstance(stamp[0], (int, float)):
            cutoffs.append(max(0.0, float(stamp[0]) - float(observation.get('padding_seconds', 0.0))))
    if len(cutoffs) < 2 or max(cutoffs) - min(cutoffs) > 0.75:
        return None
    cutoffs.sort()
    return cutoffs[len(cutoffs) // 2]


def scripted_speech_end(verdict, prompt):
    """Return the end of the first complete scripted utterance.

    Two independently padded ASR observations must agree.  This timestamp is
    used only to keep a short native reaction after a complete line; an
    uncertain alignment must trigger regeneration instead of clipping speech.
    """
    expected = normalize(''.join(expected_lines(prompt)))
    if not expected or not isinstance(verdict, dict):
        return None
    observations = verdict.get('word_observations') or verdict.get('observations') or []
    ends = []
    for observation in observations:
        result = observation.get('result') or {}
        duration = observation.get('duration')
        if not _valid_timestamps(result, duration):
            continue
        padding = float(observation.get('padding_seconds', 0.0))
        chunks = result.get('chunks') or []
        match_end = None
        span = _script_span(chunks, expected)
        if span is not None:
            stamp = chunks[span[1]].get('timestamp')
            if stamp and isinstance(stamp[1], (int, float)):
                match_end = max(0.0, float(stamp[1]) - padding)
        if match_end is not None:
            ends.append(match_end)
    if len(ends) < 2 or max(ends) - min(ends) > 0.75:
        # Japanese ASR may render a kana script in kanji, which lowers direct
        # character coverage even when the repeated utterances are identical.
        # A corroborated repeated-speech verdict plus two matching adjacent
        # chunks still proves the first utterance end without guessing words.
        ends = []
        issue_kinds = {row.get('kind') for row in verdict.get('issues') or []}
        if 'repeated_speech' not in issue_kinds:
            return None
        for observation in verdict.get('observations') or []:
            result = observation.get('result') or {}
            duration = observation.get('duration')
            if not _valid_timestamps(result, duration):
                continue
            padding = float(observation.get('padding_seconds', 0.0))
            chunks = result.get('chunks') or []
            for first, second in zip(chunks, chunks[1:]):
                a, b = normalize(first.get('text', '')), normalize(second.get('text', ''))
                if a and b and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.55:
                    ends.append(max(0.0, float(first['timestamp'][1]) - padding))
                    break
        if len(ends) < 2 or max(ends) - min(ends) > 0.75:
            return None
    ends.sort()
    return ends[len(ends) // 2]


def natural_tail_trim_seconds(verdict, prompt, repeated_start,
                              min_reaction=0.25, max_reaction=1.0):
    """Choose a safe AV end before a repeated utterance.

    The retained interval includes the complete first line and a bounded slice
    of real generated reaction.  It never fabricates a reaction by freezing a
    frame, and it declines to trim when the ASR timing leaves no safe margin.
    """
    speech_end = scripted_speech_end(verdict, prompt)
    if speech_end is None or repeated_start is None:
        return None
    repeated_start = float(repeated_start)
    latest = repeated_start - 0.04
    target = min(latest, speech_end + float(max_reaction))
    if target < speech_end + float(min_reaction):
        # Segment-level ASR sometimes starts the second repeated chunk at the
        # end of the first line, swallowing the intervening reaction into that
        # chunk.  Estimate the real second onset by aligning the first
        # utterance duration to the end of the repeated chunk.  Require two
        # independently padded observations to agree before using it.
        inferred = []
        for observation in verdict.get('observations') or []:
            padding = float(observation.get('padding_seconds', 0.0))
            chunks = [c for c in (observation.get('result') or {}).get('chunks', [])
                      if c.get('text') and c.get('timestamp')
                      and all(isinstance(v, (int, float)) for v in c['timestamp'])]
            for first, second in zip(chunks, chunks[1:]):
                a, b = normalize(first.get('text', '')), normalize(second.get('text', ''))
                if not a or not b or difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() < 0.55:
                    continue
                first_duration = float(first['timestamp'][1]) - float(first['timestamp'][0])
                estimate = float(second['timestamp'][1]) - first_duration - padding
                if first_duration > 0 and estimate > speech_end + float(min_reaction):
                    inferred.append(estimate)
                break
        if len(inferred) < 2 or max(inferred) - min(inferred) > 0.75:
            return None
        inferred.sort()
        repeated_start = inferred[len(inferred) // 2]
        latest = repeated_start - 0.04
        target = min(latest, speech_end + float(max_reaction))
        if target < speech_end + float(min_reaction):
            return None
    return target


# A stretch of the same vowel is one event. Humming with repeated onsets
# (e.g. uun / fun / n) is different from a brief breath or one long vowel.
_NONLEXICAL = set('あぁいぃうぅえぇおぉんはふっアァイィウゥエェオォンハフッーhmm')
_HUM_UNIT = re.compile(r'(?:[あぁいぃうぅえぇおぉはふ]+ー*ん+ー*|ん+ー*)')


def _extra_runs(text, expected):
    reference = normalize(''.join(expected))
    raw = unicodedata.normalize('NFKC', text or '')
    characters = [(lower, i) for i, char in enumerate(raw) if char.isalnum() for lower in char.lower()]
    actual = ''.join(char for char, _ in characters)
    matcher = difflib.SequenceMatcher(None, reference, actual, autojunk=False)
    coverage = sum(block.size for block in matcher.get_matching_blocks()) / max(1, len(reference))
    spans = [(actual[j:k], raw[characters[j][1]:characters[k-1][1]+1])
             for op, _, _, j, k in matcher.get_opcodes() if op in ('insert', 'replace') and k > j]
    extras = [span for span, _ in spans if len(span) >= 4 and not set(span) <= _NONLEXICAL]
    hum_count = 0
    for span, raw_span in spans:
        if not set(span) <= _NONLEXICAL:
            continue
        # Keep separators: 'n, n, n' is repeated; one 'nnnn' stretch is not.
        hiragana = ''.join(chr(ord(c)-0x60) if 'ァ' <= c <= 'ヶ' else c for c in raw_span)
        hum_count += len(_HUM_UNIT.findall(hiragana))
    return coverage, extras, hum_count


def _changed_kana_ending(text, expected):
    def kana(value):
        return ''.join(chr(ord(c)-0x60) if 'ァ' <= c <= 'ヶ' else c for c in normalize(value))
    reference, actual = kana(''.join(expected)), kana(text)
    operations = difflib.SequenceMatcher(None, reference, actual, autojunk=False).get_opcodes()
    if len(operations) < 2:
        return None
    operation, a, b, c, d = operations[-1]
    before = operations[-2]
    if operation not in ('replace', 'delete') or before[0] != 'equal' or before[2]-before[1] < 2:
        return None
    wanted, heard = reference[a:b], actual[c:d]
    # Only corroborated kana endings; kanji spelling and one-vowel variation stay unknown/allowed.
    if (max(len(wanted), len(heard)) < 2 or not re.fullmatch(r'[ぁ-ゖー]{1,6}', wanted)
            or not re.fullmatch(r'[ぁ-ゖー]{0,6}', heard)):
        return None
    return {'expected_ending': wanted, 'recognized_ending': heard}


def _valid_timestamps(result, duration):
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        return False
    previous = 0.0
    for chunk in result.get('chunks', []):
        ts = chunk.get('timestamp')
        if not ts or len(ts) != 2 or any(x is None for x in ts):
            return False
        a, b = ts
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in ts):
            return False
        if a < previous or a < 0 or b < a or b > duration + 0.1:
            return False
        previous = a
    return bool(result.get('chunks'))


def evaluate(expected, observations):
    """Corroborate defects in at least two of at most three timing contexts."""
    base = {'scope': 'local_scripted_extra_speech', 'quality_pass': None,
            'issues': [], 'automatic_regeneration': False}
    if not expected:
        return dict(base, status='not_applicable', reason='No authoritative tagged speech to compare.')
    if len(observations) not in (2, 3):
        return dict(base, status='unavailable', reason='Two or three observations with different timing contexts are required.')
    evidence = []
    for observation in observations:
        result = observation['result']
        coverage, extras, hum_count = _extra_runs(result.get('text', ''), expected)
        evidence.append({'coverage': coverage, 'extras': extras,
                         'nonlexical_hum_count': hum_count,
                         'changed_kana_ending': _changed_kana_ending(result.get('text', ''), expected),
                         'valid_timestamps': _valid_timestamps(result, observation['duration'])})
    base['evidence'] = evidence
    # A duplicated full line can have low character coverage when ASR changes
    # kanji/kana (e.g. 私/わたし, 機嫌/きげん).  Do not discard that strong
    # repeated-speech signal merely because the spelling-normalized coverage
    # gate is below the ordinary quality threshold.
    expected_norm = normalize(''.join(expected))
    duplicate_contexts = 0
    for observation in observations:
        text_norm = normalize((observation.get('result') or {}).get('text', ''))
        if expected_norm and text_norm.count(expected_norm) >= 2:
            duplicate_contexts += 1
    if duplicate_contexts >= 2:
        base['issues'] = [{
            'kind': 'repeated_speech',
            'evidence': 'The complete scripted line recurs in two independent timing contexts despite ASR spelling variation.',
            'corroborated_extras': [(expected_norm, expected_norm)],
            'extra_characters': len(expected_norm),
        }]
        base['automatic_regeneration'] = True
        return dict(base, status='fail')
    # Japanese ASR commonly emits kanji where the authoritative script uses
    # kana, so a literal expected-text count can miss an otherwise unambiguous
    # duplicate.  Two timing contexts that both split the audio into the same
    # adjacent repeated phrase are sufficient evidence, provided the complete
    # recognition is materially longer than the one expected line.  The length
    # guard preserves intentional repetitions already present in the script.
    chunk_repeats = []
    if len(expected) == 1:
        for observation in observations:
            result = observation.get('result') or {}
            actual_norm = normalize(result.get('text', ''))
            if len(actual_norm) < len(expected_norm) * 1.45:
                continue
            chunks = [normalize(row.get('text', '')) for row in result.get('chunks', [])]
            best = None
            for first, second in zip(chunks, chunks[1:]):
                if min(len(first), len(second)) < 4:
                    continue
                ratio = difflib.SequenceMatcher(None, first, second, autojunk=False).ratio()
                if ratio >= 0.8 and (best is None or ratio > best[0]):
                    best = (ratio, first, second)
            if best is not None:
                chunk_repeats.append(best)
    corroborated_chunk_repeat = any(
        difflib.SequenceMatcher(None, first[1], second[1], autojunk=False).ratio() >= 0.8
        for i, first in enumerate(chunk_repeats)
        for second in chunk_repeats[i + 1:]
    )
    if corroborated_chunk_repeat:
        base['issues'] = [{
            'kind': 'repeated_speech',
            'evidence': 'The same adjacent utterance repeats in two independent timing contexts despite kanji/kana spelling differences.',
            'corroborated_extras': [(chunk_repeats[0][1], chunk_repeats[1][1])],
            'extra_characters': min(len(chunk_repeats[0][1]), len(chunk_repeats[1][1])),
        }]
        base['automatic_regeneration'] = True
        return dict(base, status='fail')
    if not all(row['valid_timestamps'] for row in evidence):
        return dict(base, status='unavailable', reason='Recognition timing is invalid; no automatic retake.')
    # This guard detects extra speech; word correctness is handled by the
    # provider audit.  Whisper often writes a kana script with compact kanji,
    # which lowers literal character coverage even when both shifted contexts
    # contain exactly one stable utterance.  Treat that narrow shape as a
    # clean single utterance, while leaving unrelated low-coverage text
    # unavailable.
    if len(expected) == 1:
        single_chunks = []
        for observation in observations:
            chunks = [row for row in (observation.get('result') or {}).get('chunks', [])
                      if normalize(row.get('text', ''))]
            if len(chunks) != 1:
                single_chunks = []
                break
            single_chunks.append(normalize(chunks[0].get('text', '')))
        stable_single = (len(single_chunks) == len(observations)
                         and all(difflib.SequenceMatcher(
                             None, single_chunks[0], value, autojunk=False).ratio() >= 0.95
                             for value in single_chunks[1:]))
        actual = single_chunks[0] if single_chunks else ''
        kana_script = any('\u3041' <= char <= '\u3096' for char in expected_norm)
        kanji_asr = any('\u4e00' <= char <= '\u9fff' for char in actual)
        compact_ratio = len(actual) / max(1, len(expected_norm))
        if (stable_single and kana_script and kanji_asr
                and 0.45 <= compact_ratio <= 1.05
                and all(row['coverage'] >= 0.5 and not row['extras']
                        and row['nonlexical_hum_count'] == 0
                        and row['changed_kana_ending'] is None for row in evidence)):
            return dict(base, status='pass',
                        reason='Two timing contexts contain one stable kanji-rendered utterance; no extra speech detected.')
    usable = [row for row in evidence if row['coverage'] >= 0.65]
    if len(usable) < 2:
        return dict(base, status='unavailable',
                    needs_context_recheck=len(observations) == 2 and len(usable) == 1,
                    reason='Script coverage disagrees or is insufficient; no automatic retake without corroboration.')
    pairs = []
    pair_sizes = []
    for i, first_row in enumerate(usable):
        for second_row in usable[i+1:]:
            for first in first_row['extras']:
                for second in second_row['extras']:
                    match = difflib.SequenceMatcher(None, first, second, autojunk=False)
                    common = sum(block.size for block in match.get_matching_blocks())
                    if common >= 4 and match.ratio() >= 0.5:
                        pairs.append((first, second))
                        pair_sizes.append(min(sum(map(len, first_row['extras'])),
                                              sum(map(len, second_row['extras']))))
    issues = []
    endings = [row['changed_kana_ending'] for row in usable if row['changed_kana_ending']]
    agreed_ending = next((ending for ending in endings if endings.count(ending) >= 2), None)
    if agreed_ending is not None:
        issues.append({'kind': 'changed_words',
                       'evidence': 'The same multi-character kana ending differs from the script in two timing contexts.',
                       **agreed_ending})
    if pairs:
        repeated = any(difflib.SequenceMatcher(None, a, normalize(line), autojunk=False).ratio() >= 0.8
                       for a, _ in pairs for line in expected)
        issues.append({'kind': 'repeated_speech' if repeated else 'unscripted_speech',
                       'evidence': 'A substantial extra vocal transcription recurs in two timing contexts.',
                       'corroborated_extras': pairs, 'extra_characters': max(pair_sizes)})
    hum_counts = [row['nonlexical_hum_count'] for row in usable]
    repeated_counts = [count for count in hum_counts if count >= 3]
    if len(repeated_counts) >= 2:
        issues.append({'kind': 'repeated_vocalization',
                       'evidence': 'At least three unprescribed humming units recur in two timing contexts.',
                       'repeat_count': min(repeated_counts), 'observed_counts': hum_counts})
    if issues:
        return dict(base, status='fail', issues=issues, automatic_regeneration=True)
    if any(row['extras'] or row['nonlexical_hum_count'] >= 2 or row['changed_kana_ending'] for row in usable):
        return dict(base, status='unavailable', needs_context_recheck=len(observations) == 2,
                    reason='Extra speech, repeated vocalization or a changed kana ending is not corroborated; no automatic retake.')
    return dict(base, status='pass', reason='No corroborated extra speech, repeated vocalization or changed kana ending detected; full voice quality still needs listening.')


def config_contract():
    path = Path(__file__).with_name('local_audio_audit.json')
    value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    return {'policy': 'local-japanese-extra-speech-v2', 'enabled': bool(value.get('enabled', False)),
            'model_path': os.environ.get('H3_LOCAL_WHISPER_MODEL') or value.get('model_path')}


def configuration():
    path = Path(__file__).with_name('local_audio_audit.json')
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if not value.get('enabled', False):
        return None
    model_path = os.environ.get('H3_LOCAL_WHISPER_MODEL') or value.get('model_path')
    if not model_path or not Path(model_path).is_dir():
        raise RuntimeError('Local speech guard model folder is missing. Configure local_audio_audit.json; no models are downloaded automatically.')
    return str(Path(model_path).resolve())


def _recognizer(model_path):
    global _ASR, _ASR_PATH
    if _ASR is None or _ASR_PATH != model_path:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
        processor = AutoProcessor.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
        model = AutoModelForSpeechSeq2Seq.from_pretrained(model_path, local_files_only=True,
                    trust_remote_code=False, dtype=torch.float32, attn_implementation='sdpa')
        _ASR = pipeline('automatic-speech-recognition', model=model,
                        tokenizer=processor.tokenizer, feature_extractor=processor.feature_extractor, device=torch.device('cpu'))
        if _ASR.device.type != 'cpu' or next(_ASR.model.parameters()).device.type != 'cpu':
            raise RuntimeError('Local speech guard must run on CPU.')
        _ASR_PATH = model_path
    return _ASR


def audit(audio, prompt, evidence_path=None, *, locate_repeats=False):
    import torch
    import torchaudio.functional
    lines = expected_lines(prompt)
    languages = {tag.strip().lower() for tag in re.findall(r'<d>\s*\[([^]]+)\]', prompt, re.I)}
    if languages and languages != {'japanese'}:
        return {'status': 'not_applicable', 'scope': 'local_scripted_extra_speech', 'issues': [],
                'automatic_regeneration': False, 'quality_pass': None, 'reason': 'This local guard currently verifies Japanese scripted speech only.'}
    if not lines:
        return evaluate(lines, [])
    model_path = configuration()
    if model_path is None:
        return {'status': 'not_configured', 'scope': 'local_scripted_extra_speech', 'issues': [],
                'automatic_regeneration': False, 'quality_pass': None}
    wave = audio['waveform'].detach().to(device='cpu', dtype=torch.float32)[0].mean(0)
    rate = int(audio['sample_rate'])
    if rate != 16000:
        wave = torchaudio.functional.resample(wave, rate, 16000)
    # The second window retains every sample and shifts the ASR context with
    # quiet padding. Cropping speech edges would create false missing words.
    # Its decoded timestamps are checked against that actual padded duration.
    padding = round(0.4 * 16000)
    windows = [wave, torch.nn.functional.pad(wave, (padding, padding))]
    observations = []
    word_observations = []
    with _LOCK:
        previous_threads = torch.get_num_threads()
        try:
            torch.set_num_threads(min(4, previous_threads))
            recognizer = _recognizer(model_path)
            def recognize(sample, padding_seconds):
                result = recognizer({'array': sample.numpy(), 'sampling_rate': 16000},
                         return_timestamps=True, generate_kwargs={'language': 'japanese', 'task': 'transcribe',
                         'condition_on_prev_tokens': False, 'num_beams': 1, 'max_new_tokens': 256})
                observations.append({'duration': len(sample)/16000, 'padding_seconds': padding_seconds,
                                     'result': result})
            for sample, padding_seconds in zip(windows, (0.0, 0.4)):
                recognize(sample, padding_seconds)
            verdict = evaluate(lines, observations)
            # One ASR timing context may omit the humming entirely. Recheck
            # only a discordant extra-vocal signal, keeping every audio sample.
            # This is bounded to one extra recognition, not another generation.
            if verdict.get('needs_context_recheck'):
                extra_padding = round(0.8 * 16000)
                recognize(torch.nn.functional.pad(wave, (extra_padding, extra_padding)), 0.8)
                verdict = evaluate(lines, observations)
            verdict['observations'] = observations
            if locate_repeats and verdict['status'] != 'pass' and len(lines) == 1:
                # Cause recovery needs word boundaries for both repeated lines
                # and a correct line followed by unrelated generated speech.
                for sample, padding_seconds in zip(windows, (0.0, 0.4)):
                    result = recognizer({'array': sample.numpy(), 'sampling_rate': 16000},
                        return_timestamps='word', generate_kwargs={'language': 'japanese', 'task': 'transcribe',
                        'condition_on_prev_tokens': False, 'num_beams': 1, 'max_new_tokens': 256})
                    word_observations.append({'duration': len(sample)/16000,
                                              'padding_seconds': padding_seconds, 'result': result})
        finally:
            torch.set_num_threads(previous_threads)
    verdict['observations'] = observations
    if word_observations:
        verdict['word_observations'] = word_observations
    verdict['speech_end_seconds'] = scripted_speech_end(verdict, prompt)
    verdict['delivered_duration_seconds'] = round(float(wave.shape[-1]) / 16000, 6)
    verdict['device'] = str(recognizer.device)
    verdict['recognition_passes'] = len(observations)
    if evidence_path is not None:
        Path(evidence_path).parent.mkdir(parents=True, exist_ok=True)
        Path(evidence_path).write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding='utf-8')
    return verdict
