"""Prompt-owned timing; no story-specific defaults and no model calls."""
import math
import re
import warnings

CLOCK = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?"
RANGE = re.compile(r"(?m)^\s*(" + CLOCK + r")\s*[-–—~〜]\s*(" + CLOCK + r")\s*[:：]?\s*$")
SHOT = re.compile(r"(?<!from )\[Shot\s+(\d+)\](?:\s+(?:At\s+)?(" + CLOCK + r")\s*,?)?", re.I)
FIELD = re.compile(r"(?m)^\s*(subject_definitions|summary|retention_analysis|detailed_description|integrated_multimodal_description|overall_soundscape|non_diegetic_music)\s*:", re.I)
TAIL = re.compile(r"(?m)^\s*(?:STYLE|MOTION|CAMERA|IMPORTANT|AUDIO|SOUND|NEGATIVE|CONSTRAINTS)\s*:")
DECLARATIONS = [
    re.compile(r"(?im)^\s*(?:duration(?:_seconds)?|total duration|video length|尺|動画の長さ|全長|長さ)\s*[:：=]\s*(\d+(?:\.\d+)?)\s*(?:s(?:ec(?:onds?)?)?|秒)?\b?".replace(r"\b?", "")),
    re.compile(r"\b(?:create|generate|make)\s+(?:a\s+)?(\d+(?:\.\d+)?)[ -]second(?:s)?\b", re.I),
    re.compile(r"\b(?:the entire|the full|total(?: duration)?(?: is| of)?)\s+(\d+(?:\.\d+)?)\s*seconds?\b", re.I),
    re.compile(r"(?:全体|合計|全長|尺)(?:は|を|が|で)?\s*(\d+(?:\.\d+)?)\s*秒"),
    re.compile(r"(\d+(?:\.\d+)?)\s*秒(?:間)?(?:の動画|動画|で(?:生成|作成|描|完成))"),
]

def normalize_lm_fields(prompt, expected):
    """Accept inline H3 sections; whitespace is not a semantic error."""
    # Local models may omit header punctuation or add Markdown. Repair only
    # recognized section headings, not story content or quoted dialogue.
    headers = '|'.join(expected)
    prompt = re.sub(r'(?mi)^[ \t]*(?:\#{1,6}[ \t]+)?\*{0,2}('+headers+r')(?=[\s:*]|$)\*{0,2}[ \t]*:?[ \t]*\*{0,2}[ \t]*',
                    lambda m:m.group(1).lower()+': ',prompt)
    pattern = r'(?<!\w)('+'|'.join(expected)+r')\s*:'
    matches = list(re.finditer(pattern, prompt, re.I))
    if [m.group(1).lower() for m in matches] != expected:
        return prompt, False
    chunks = []
    preamble = prompt[:matches[0].start()].strip()
    for i, match in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(prompt)
        value = prompt[match.end():end].strip()
        if not value:
            return prompt, False
        field = expected[i]
        if field in ('integrated_multimodal_description','detailed_description'):
            if not SHOT.search(value):
                value = '[Shot 1] '+value
            value = re.sub(r'\[Shot\s+1\]\s+At\s+0{1,2}:00(?:\.0+)?\s*,?', '[Shot 1]', value, count=1, flags=re.I)
        chunks.append(field+': '+value)
    return (preamble+'\n\n' if preamble else '')+'\n\n'.join(chunks), True

def reference_format_errors(prompt, image_count):
    if not image_count:
        return []
    used = {int(n) for n in re.findall(r'<Picture\s+(\d+)>',prompt,re.I)}
    supplied = set(range(1,image_count+1))
    errors = []
    if used != supplied:
        errors.append('Bind the connected image sources '+', '.join(f'<Picture {n}>' for n in sorted(supplied))+' to their requested reference roles; do not invent additional images.')
    retention = re.search(r'(?is)retention_analysis\s*:(.*?)detailed_description\s*:',prompt)
    if not retention or not re.search(r'\b(?:fully_preserved|partially_preserved|attribute_transfer|weak_reference)\b',retention.group(1)):
        errors.append('retention_analysis must state reference preservation relationships using fully_preserved, partially_preserved, attribute_transfer, or weak_reference; it is not an audience-engagement analysis.')
    return errors

def seconds(value):
    parts = value.split(':')
    result = 0.0
    for part in parts:
        result = result * 60 + float(part)
    return result

def clock(value):
    ms = round(value * 1000)
    return f'{ms // 60000:02d}:{ms % 60000 / 1000:06.3f}'

def timing(prompt, fallback):
    # Dialogue/visible text is content, never a duration-control command.
    control = re.sub(r'<d>.*?</d>|"[^"\n]*"|「[^」]*」', '', prompt, flags=re.S)
    declared = {float(m.group(1)) for regex in DECLARATIONS for m in regex.finditer(control)}
    if len(declared) > 1:
        warnings.warn('複数の尺指定があります。最長の明示尺を使用します。')
    ranges = list(RANGE.finditer(control))
    last = 0.0
    for match in ranges:
        start, end = map(seconds, match.groups())
        if start < last - 1e-6 or end <= start:
            warnings.warn('時間範囲の揺れを検出しました。文章順で処理します。')
        last = max(last, end)
    duration = max(declared) if declared else (last or float(fallback))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('動画の秒数は正の有限値で指定してください。')
    if last > duration + 1e-6:
        warnings.warn('工程時刻より明示された動画尺を優先します。')
    return duration, ('明示秒数' if declared else '時間範囲の終端' if ranges else '指定なし時の秒数')

def timeline(prompt, duration):
    fields = list(FIELD.finditer(prompt))
    body_start, body_end = 0, len(prompt)
    for i, field in enumerate(fields):
        if field.group(1).lower() in ('detailed_description', 'integrated_multimodal_description'):
            body_start = field.end()
            body_end = fields[i+1].start() if i+1 < len(fields) else len(prompt)
            break
    body = prompt[body_start:body_end]
    # Local LMs also emit inline bracketed ranges. Parse these as real event
    # boundaries rather than treating the entire story as one spanning Shot.
    # Quoted speech is content, not a timeline-control channel.
    protected = re.compile(r'(<d>.*?</d>|「[^」]*」|"[^"\n]*")', re.S)
    bracket_range = re.compile(
        r'(?:\[Shot\s+\d+\]\s*)?\[(' + CLOCK + r')\s*[-–—~〜]\s*(' + CLOCK + r')\]', re.I)
    chunks = protected.split(body)
    for index in range(0, len(chunks), 2):
        chunks[index] = bracket_range.sub(lambda m: '\n'+m.group(1)+'-'+m.group(2)+'\n', chunks[index])
    body = ''.join(chunks)
    # Also accept narrative phase clocks such as 'At 3.5s' / 'By 8s'.
    # Reuse the same shot parser and keep quoted clock text untouched.
    if not RANGE.search(body):
        phase = re.compile(r'(?:\[Shot\s+\d+\]\s*)?(?:(?:At|By|From)\s+)(\d+(?:\.\d+)?)\s*(?:seconds?|s)\b(?:\s+to\s+\d+(?:\.\d+)?\s*(?:seconds?|s))?\s*[:,]?', re.I)
        initial = re.compile(r'\[Shot\s+\d+\]\s*(\d+(?:\.\d+)?)\s*(?:seconds?|s)\s*[:,]?', re.I)
        chunks = protected.split(body)
        for index in range(0,len(chunks),2):
            chunks[index] = initial.sub(lambda m:'[Shot 1] At '+clock(float(m.group(1)))+', ',chunks[index])
            chunks[index] = phase.sub(lambda m:'[Shot 1] At '+clock(float(m.group(1)))+', ',chunks[index])
        body=''.join(chunks)
    matches = list(RANGE.finditer(body))
    is_range = bool(matches)
    if not matches:
        matches = list(SHOT.finditer(body))
    if not matches:
        return None
    common = body[:matches[0].start()].strip()
    tail_match = TAIL.search(body, matches[-1].end()) if is_range else re.search(r'\[Global Instructions\]',body,re.I)
    end = tail_match.start() if tail_match else len(body)
    global_tail = body[end:].strip() if tail_match else ''
    entries = []
    for i, match in enumerate(matches):
        if is_range:
            start, finish = map(seconds, match.groups())
        else:
            # Untimed later shots do not imply any exact transition time.
            if i and match.group(2) is None:
                return _inferred_timeline(prompt, body_start, body_end, common, matches, body, end, global_tail, duration, is_range)
            start = seconds(match.group(2)) if match.group(2) else 0
            next_stamp = matches[i+1].group(2) if i+1<len(matches) else None
            finish = seconds(next_stamp) if next_stamp else duration
        stop = matches[i+1].start() if i+1<len(matches) else end
        if start >= duration or finish <= start or (entries and start < entries[-1][1]-1e-6):
            return _inferred_timeline(prompt, body_start, body_end, common, matches, body, end, global_tail, duration, is_range)
        entries.append((start, finish, body[match.end():stop].strip()))
    return prompt[:body_start], common, entries, global_tail, prompt[body_end:], is_range

def _inferred_timeline(prompt, body_start, body_end, common, matches, body, end, tail, duration, is_range):
    warnings.warn('ショット時刻の揺れを検出。内容と文章順を保持して指定尺内へ均等配置します。')
    entries = []
    for i, match in enumerate(matches):
        stop = matches[i+1].start() if i+1 < len(matches) else end
        entries.append((duration*i/len(matches), duration*(i+1)/len(matches), body[match.end():stop].strip()))
    return prompt[:body_start], common, entries, tail, prompt[body_end:], is_range

def segment_prompt(prompt, duration, segment, count):
    if count == 1 and not segment.context_frames:
        return prompt  # Native H3 sees the ordinary prompt verbatim.
    parsed = timeline(prompt, duration)
    if parsed is None:
        warnings.warn('時刻指定なし。元の指示と前区間の映像・音声を使って続行します。')
        return (f'Current window: {segment.output_start/24:.3f}-{(segment.output_start+segment.output_frames)/24:.3f} seconds of {duration:.3f} seconds. '
                'Continue from the audiovisual context; do not repeat already completed dialogue or actions.\n\n'+prompt)
    prefix, common, entries, tail, suffix, is_range = parsed
    start, end = segment.output_start/24, (segment.output_start+segment.output_frames)/24
    context = segment.context_frames/24
    selected = [(a,b,text) for a,b,text in entries if a < end and b > start]
    parts = []
    # Intro can contain opening-only commands. It is never replayed on continuation.
    if common and start == 0:
        parts.append(common)
    elif common:
        parts.append('Use the preceding audiovisual context for the established scene and identities.')
    for a,b,text in selected:
        left, right = context+max(a,start)-start, context+min(b,end)-start
        marker = f'{clock(left)}-{clock(right)}' if is_range else (f'[Shot {len(parts)+1}] At {clock(left)},')
        if a < start:
            text = 'Continue the already-started action from the preceding context; do not restart it. Remaining action: ' + text
        parts.append(marker+'\n'+text)
    if not selected:
        parts.append('Continue the established ending state from the preceding audiovisual context.')
    guide = (f'Continuation window of the original video: {clock(start)}-{clock(end)}. '
             f'The first {context:.3f} seconds are preceding context, not new action. '
             'Execute only the following remaining local events; do not replay the opening. '
             'Time ranges mark successive phases, not automatic camera cuts. Preserve continuous '
             'character movement, positions, and scene state between phases. Do not insert '
             'unrequested establishing shots, reverse-angle resets, or replay a pose/action. '
             'Use the described camera motion; cut only where the original description explicitly '
             'requests a cut or a new Shot.\n\n')
    # Ref2V summary/retention can contain the entire story, so retain only identity definitions later.
    if start and prefix:
        definition = re.search(r'(?is)(subject_definitions\s*:.*?)(?=\n\s*(?:summary|retention_analysis|detailed_description)\s*:)',prefix)
        field = 'detailed_description:' if 'detailed_description' in prefix else 'integrated_multimodal_description:'
        prefix = (definition.group(1)+'\n\n' if definition else '')+field
    if start:
        suffix = re.sub(r'(?is)(overall_soundscape\s*:).*?(?=non_diegetic_music\s*:|$)',
            r'\1 Continue the environmental ambience already audible in the preceding context. Add only sounds explicitly occurring in the local timed description; completed attacks, impacts, dialogue and vocal onsets are not new events.\n\n', suffix)
    # Bracket-range normalization consumes the enclosing Shot marker. Preserve
    # its single-take meaning for H3 rather than sending a bare list of phases.
    body_match = re.search(r'(?:detailed_description|integrated_multimodal_description)\s*:(.*?)(?=overall_soundscape\s*:|$)', prompt, re.I|re.S)
    original_shots = list(SHOT.finditer(body_match.group(1))) if body_match else []
    if is_range and len(original_shots) == 1:
        parts.insert(0, '[Shot 1] One continuous, uninterrupted take. The camera follows the ongoing action smoothly through the timed phases below, preserving the same moment and character positions.')
    if end >= duration and re.search(r'(?i)\b(?:disappear|vanish)\w*\b|no (?:characters|subjects|people) remain|(?:leave|leaves|leaving|exit|exits|exiting) (?:the )?(?:frame|view)', ' '.join(text for _,_,text in selected)):
        parts.append('ENDING CONTINUITY: Complete the described disappearance. Once subjects leave or are swallowed out of view, the final composition holds the resulting environment alone through the last frame. Reference identities remain available for appearance consistency; they do not require subjects to return to view. Keep the concluding camera movement moving away from the subjects, never cutting back to a closer view.')
    return guide+prefix+'\n\n'.join(parts)+('\n\n'+tail if tail else '')+suffix


def boundary_errors(prompt, duration, boundaries):
    """A spanning paragraph replays its beginning when copied to both passes."""
    if not boundaries:
        return []
    parsed = timeline(prompt, duration)
    if parsed is None:
        return ['Use explicit time ranges with generation boundaries: '+', '.join(clock(b) for b in boundaries)]
    errors = []
    for boundary in boundaries:
        if any(a < boundary-1e-6 and b > boundary+1e-6 for a,b,_ in parsed[2]):
            errors.append('Split the action phase at '+clock(boundary)+'. No range may straddle this generation boundary. Before it, establish the ongoing action; after it, describe only its further progression from the reached position/scale, never its onset or a fresh camera move.')
    return errors
