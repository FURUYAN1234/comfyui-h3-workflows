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
    if not all(row['valid_timestamps'] for row in evidence):
        return dict(base, status='unavailable', reason='Recognition timing is invalid; no automatic retake.')
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


def audit(audio, prompt, evidence_path=None):
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
        finally:
            torch.set_num_threads(previous_threads)
    verdict['observations'] = observations
    verdict['device'] = str(recognizer.device)
    verdict['recognition_passes'] = len(observations)
    if evidence_path is not None:
        Path(evidence_path).parent.mkdir(parents=True, exist_ok=True)
        Path(evidence_path).write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding='utf-8')
    return verdict
