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

def _range_is_covered(interval, ranges):
    """Whether existing phases already own every moment of an inline cue."""
    start, end = interval
    if end <= start:
        return False
    cursor = start
    for left, right in sorted(ranges):
        if right <= cursor:
            continue
        if left > cursor + 1e-6:
            return False
        cursor = right
        if cursor >= end - 1e-6:
            return True
    return False


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
    protected = re.compile(r'(<d>.*?</d>|「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*")', re.S|re.I)
    bracket_range = re.compile(
        r'(\[Shot\s+\d+\]\s*)?\[(' + CLOCK + r')\s*[-–—~〜]\s*(' + CLOCK + r')\]', re.I)
    natural_range = re.compile(
        r'(\[Shot\s+\d+\]\s*)?\b(?:From|Between)\s+(' + CLOCK +
        r')\s*(?:to|through|and|[-–—~〜])\s*(' + CLOCK + r')\s*[,;:]?', re.I)
    structural = _structural_text(body)
    replacements, bracket_phases = [], []
    prose_ranges = list(natural_range.finditer(structural))
    for match in bracket_range.finditer(structural):
        interval = tuple(map(seconds, (match.group(2), match.group(3))))
        parents = list(bracket_phases)
        # Local models bracket inline speech cues too: At [00:04-00:07].
        # A containing earlier phase owns that cue; a partial overlap remains
        # a real timing conflict. Explicit Shot markers always retain control.
        if re.search(r'\b(?:At|By|Until|Before|After|Between)\s*$', structural[:match.start()], re.I):
            parents.extend(tuple(map(seconds, (m.group(2), m.group(3))))
                           for m in prose_ranges if m.end() <= match.start())
        if not match.group(1) and _range_is_covered(interval, parents):
            continue
        replacements.append((match.start(), match.end(),
            '\n'+match.group(2)+'-'+match.group(3)+'\n'+(match.group(1) or '')))
        bracket_phases.append(interval)
    for left, right, replacement in reversed(replacements):
        body = body[:left]+replacement+body[right:]
    structural = _structural_text(body)
    covered = [tuple(map(seconds, m.groups())) for m in RANGE.finditer(structural)]
    # A prose clock inside an explicit phase is an event cue, not a second
    # phase. This also keeps a natural-language parent range's nested speech
    # cues intact, while accepting uncovered phases in mixed-format output.
    replacements = []
    for match in natural_range.finditer(structural):
        interval = tuple(map(seconds, (match.group(2), match.group(3))))
        if _range_is_covered(interval, covered):
            continue
        replacements.append((match.start(), match.end(),
            '\n'+match.group(2)+'-'+match.group(3)+'\n'+(match.group(1) or '')))
        covered.append(interval)
    # Japanese/seconds-only phases are common in imperfect local-LM rewrites.
    # Promote uncovered ranges in prose order; later recap ranges are already
    # covered and must not reset the timeline. Protected quotes are masked.
    numeric_range = re.compile(r'(?<![\w:.])(?:\[Shot\s+\d+\]\s*)?(\d+(?:\.\d+)?)\s*(?:秒)?\s*[-–—~〜]\s*(\d+(?:\.\d+)?)\s*(?:秒|seconds?\b|s\b)\s*[,、:：]?', re.I)
    for match in numeric_range.finditer(structural):
        interval = tuple(map(float, match.groups()))
        if _range_is_covered(interval, covered):
            continue
        replacements.append((match.start(), match.end(), '\n'+clock(interval[0])+'-'+clock(interval[1])+'\n'))
        covered.append(interval)
    replacements.sort(key=lambda item: item[0])
    for left, right, replacement in reversed(replacements):
        body = body[:left]+replacement+body[right:]
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
    structural = _structural_text(body)
    matches = list(RANGE.finditer(structural))
    is_range = bool(matches)
    if not matches:
        matches = list(SHOT.finditer(structural))
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


_CONTENT = re.compile(r'<d>.*?</d>|「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*"', re.I | re.S)
_DIALOGUE = re.compile(r'<d>.*?</d>', re.I | re.S)


def _structural_text(value):
    return _CONTENT.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m.group()), value)


def _sub_structural(pattern, replacement, value):
    parts, cursor = [], 0
    for match in _CONTENT.finditer(value):
        parts.extend((re.sub(pattern,replacement,value[cursor:match.start()],flags=re.I), match.group()))
        cursor = match.end()
    parts.append(re.sub(pattern,replacement,value[cursor:],flags=re.I))
    return ''.join(parts)


def _without_dialogue(value):
    """Remove a repeated speech event, including its cue; retain later action."""
    if not _DIALOGUE.search(value):
        return value.strip()
    masked = _structural_text(value)
    cuts = [0] + [m.end() for m in re.finditer(r'(?<=[.!?;])\s+|\n+', masked)] + [len(value)]
    kept = []
    for left, right in zip(cuts, cuts[1:]):
        piece = value[left:right]
        tags = list(_DIALOGUE.finditer(piece))
        if tags:
            piece = piece[tags[-1].end():].lstrip(' .,;:')
        if piece.strip():
            kept.append(piece.strip())
    return ' '.join(kept)


def segment_frame_budget(duration, context_frames, timeline_module):
    """Keep the *raw* continuation, including guide/padding, on the 15s grid."""
    if duration <= 15:
        return 362
    # 39 context frames allow 13s of new output; 22 allow 14s. Planning
    # still preserves the requested master duration, including fractional tails.
    seconds = int((362 - int(context_frames)) // 24)
    while timeline_module._h3_grid_frames(seconds * 24 + int(context_frames)) > 362:
        seconds -= 1
    return timeline_module._h3_grid_frames(seconds * 24)


def _localize_event_times(text, start, context, duration):
    """Rebase inline event cues as well as phase headings, protecting quotes.

    A 17-second utterance in the 13-second continuation belongs at 5.625s
    in its raw clip (including the 1.625s guide), not outside that clip.
    """
    cue = (r'\b(At|By|From|Until|Before|After|Between|Through|To)\s+('
           + CLOCK + r')(?:\s*(to|through|and|[-–—~〜])\s*(' + CLOCK + r'))?')
    def stamp(value):
        t = seconds(value)
        return clock(context + max(0.0, t-start)) if 0 <= t <= duration else value
    def replace(match):
        result = match.group(1)+' '+stamp(match.group(2))
        if match.group(4):
            separator = match.group(3)
            result += (' '+separator+' ' if separator.isalpha() else separator)+stamp(match.group(4))
        return result
    # Nested bracket cues that timeline() kept inside a parent phase may
    # have no "At" prefix. Normalize both forms before applying one rebase.
    # Quoted speech and visible clock strings remain protected by _sub_structural.
    bracket_cue = (r'(?:\b(At|By|From|Until|Before|After|Between|Through|To)\s+)?\[('
                   + CLOCK + r')(?:\s*[-–—~〜]\s*(' + CLOCK + r'))?\]')
    text = _sub_structural(bracket_cue,
                           lambda m: (m.group(1) or 'At')+' '+m.group(2)
                           + ('-'+m.group(3) if m.group(3) else ''), text)
    text = _sub_structural(cue, replace, text)
    numeric = r'(?<![\w:.])(\d+(?:\.\d+)?)\s*(?:秒)?\s*[-–—~〜]\s*(\d+(?:\.\d+)?)\s*(?:秒|seconds?\b|s\b)'
    def numeric_replace(match):
        return 'From '+stamp(match.group(1))+' to '+stamp(match.group(2))
    return _sub_structural(numeric, numeric_replace, text)


def segment_prompt(prompt, duration, segment, count):
    parsed = timeline(prompt, duration)
    start = segment.output_start / 24
    end = (segment.output_start + segment.output_frames) / 24
    context = segment.context_frames / 24
    if parsed is None:
        warnings.warn('時刻指定なし。文章全体を一つの工程として扱い、台詞は先頭区間だけに配置します。')
        prompt = re.sub(r'((?:integrated_multimodal_description|detailed_description)\s*:)',
                        r'\1 [Shot 1] ', prompt, count=1, flags=re.I)
        parsed = timeline(prompt, duration)
        if parsed is None:
            text = _without_dialogue(prompt) if start else prompt
            return ('Continue the reached scene from the preceding visual guide; do not restart '
                    'the opening or any completed utterance.\n\n' if start else '') + text
    prefix, common, entries, tail, suffix, is_range = parsed
    parts = []
    # Untimed prologues may contain dialogue; give them one owner too.
    if common and not start:
        parts.append(common)
    selected = [(a, b, text) for a, b, text in entries if a < end and b > start]
    for a, b, text in selected:
        left, right = context + max(a, start) - start, context + min(b, end) - start
        if a < start:
            text = re.sub(r'^\s*\[Shot\s+\d+\]\s*', '', text, count=1, flags=re.I)
            text = _without_dialogue(text)
            # A camera cut at the beginning of a spanning phase already occurred.
            # Retain the reached view, without commanding another cut at the seam.
            text = _sub_structural(r'\b(?:the camera|the shot|camera|shot)\s+(?:cuts?|switches?|transitions?)\s+to\b',
                                   'The established view remains on', text)
            text = ('Continue the action already in progress, from its current position, '
                    'scale and movement in the preceding guide; the onset is in the past. ' + text)
        marker = f'{clock(left)}-{clock(right)}' if is_range else (
            '[Shot 1]' if not parts and not start else f'[Shot {len(parts)+1}] At {clock(left)},')
        text = _localize_event_times(text, start, context, duration)
        if _DIALOGUE.search(text):
            finish = max(left, right - min(0.35, (right-left)/5))
            text += (' Respect each explicit speech time range above; start and finish that line inside its own range. '
                     'Do not fill the surrounding speech-free time with vocal filler. '
                     f'Only utterances without an explicit time cue may use {clock(left)}-{clock(finish)} '
                     'in their written order, leaving a short natural pause before the phase ends. '
                     'Preserve each described subject-to-speaker mapping and stable voice, including explicit off-screen voiceover. '
                     'Preserve every written repetition and sustained vowel; add no unwritten repeat, '
                     'echo, simultaneous second voice, paraphrase or speech-like babble. '
                     'Do not read scene directions aloud.')
        parts.append(marker+'\n'+text)
    if not selected:
        parts.append('Continue the reached scene and motion from the preceding guide.')
    body_match = re.search(r'(?:detailed_description|integrated_multimodal_description)\s*:(.*?)(?=overall_soundscape\s*:|$)', prompt, re.I|re.S)
    original_shots = list(SHOT.finditer(_structural_text(body_match.group(1)))) if body_match else []
    if is_range:
        # Ranges schedule phases. Only explicit Shot/cut text creates a cut.
        if len(original_shots) <= 1:
            parts = [_sub_structural(r'\[Shot\s+\d+\]\s*', '', part) for part in parts]
            parts.insert(0, '[Shot 1] One continuous, uninterrupted take. Preserve the ongoing camera movement and character positions through the following phases.')
        else:
            parts = [_sub_structural(r'\[Shot\s+(\d+)\]\s*', lambda m: '' if m.group(1) == '1' else 'The camera cuts here to the next requested shot. ', part) for part in parts]
            parts.insert(0, '[Shot 1] Follow only the camera cuts explicitly described in the local phases.')
    guide = ''
    if context:
        guide = (f'At {clock(context)} the new action continues immediately after the '
                 f'{context:.3f}-second preceding audiovisual guide. The guide is past context '
                 'and is removed from the delivered video. Do not speak, restart a vocal onset, '
                 'or execute new events in that guide interval. Match the last delivered frame: '
                 'same location, visible subjects, framing, screen direction and ongoing motion. '
                 'A generation boundary is not a camera cut.\n')
    guide += ('Use only the vocal events scheduled below. Do not repeat words from the preceding '
              'audio guide or invent speech-like babble. Explicitly requested wordless cries, '
              'natural breaths and sustained vowels are allowed.\n')
    # Keep the documented Ref2VA six-field structure without replaying its synopsis.
    if 'subject_definitions' in prefix.lower():
        definition = re.search(r'(?is)subject_definitions\s*:(.*?)(?=summary\s*:|retention_analysis\s*:|detailed_description\s*:)', prefix)
        retention = re.search(r'(?is)retention_analysis\s*:(.*?)(?=detailed_description\s*:)', prefix)
        definitions = _without_dialogue(definition.group(1)) if definition else ''
        retained = _without_dialogue(retention.group(1)) if retention else 'Use the supplied references for their specified identity and appearance roles.'
        if start:
            retained += (' Reference appearances persist, while the preceding video guide owns current '
                         'positions, scale, pose and camera view. Do not recreate a reference starting pose.')
        prefix = ('subject_definitions: '+definitions+'\n\nsummary: '+
                  ('Continue the reached scene. ' if start else '')+
                  'The local timed description below is the complete event and speech schedule for this segment.'+
                  '\n\nretention_analysis: '+retained+'\n\ndetailed_description: ')
    else:
        # First-frame alignment is opening-only; continuation uses the AV guide.
        prefix = prefix if not start else 'integrated_multimodal_description: '
        prefix = _without_dialogue(prefix)
    # Strip speech recaps from *all* global fields, including the first segment.
    fields = list(FIELD.finditer(suffix))
    if fields:
        cleaned_fields = []
        for i, field in enumerate(fields):
            stop = fields[i+1].start() if i+1 < len(fields) else len(suffix)
            value = _without_dialogue(suffix[field.end():stop])
            if not value:
                value = 'N/A' if field.group(1).lower() == 'non_diegetic_music' else 'Only the non-speech sounds scheduled in the local phases.'
            cleaned_fields.append(field.group(1).lower()+': '+value)
        suffix = '\n\n'.join(cleaned_fields)
    if start:
        sound = re.search(r'(?is)overall_soundscape\s*:(.*?)(?=non_diegetic_music\s*:|$)', suffix)
        silent = sound and sound.group(1).strip().casefold() in ('n/a', 'none', 'silence', 'silent')
        local_sound = ('N/A' if silent else
                       'Continue the established environmental ambience. Add only sounds scheduled in '
                       'the local phases; do not replay completed impacts, attacks, cries or speech.')
        suffix = re.sub(r'(?is)(overall_soundscape\s*:).*?(?=non_diegetic_music\s*:|$)',
                        lambda m: m.group(1)+' '+local_sound+'\n\n', suffix)
    # Remove dialogue in global tails as it is already owned by timed events.
    tail = _without_dialogue(tail)
    if end >= duration and re.search(r'(?i)\b(?:disappear|vanish)\w*\b|no (?:characters|subjects|people) remain|(?:leave|leaves|leaving|exit|exits|exiting) (?:the )?(?:frame|view)', ' '.join(text for _,_,text in selected)):
        parts.append('ENDING CONTINUITY: Complete the requested disappearance, then hold the resulting environment. The availability of reference identities never requires an absent subject to return or a cut back to a closer view.')
    return prefix+guide+'\n\n'.join(parts)+('\n\n'+tail if tail else '')+'\n\n'+suffix.strip()


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
