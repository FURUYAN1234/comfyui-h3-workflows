import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import unicodedata
from .lm_device import prompt_gpu_session, ensure_cpu_for_video

import folder_paths
import nodes
from .standard import timing, timeline, segment_prompt, normalize_lm_fields, reference_format_errors, boundary_errors, segment_frame_budget
from .quality_guard import conversion_errors, VOCAL_REQUEST


# Three-mode quality policy. A continuation guide counts toward the raw H3 window.
THREE_MODE_POLICY = "three-mode-continuity-v1"

DEFAULT_EXTRA_RULES = '''話し言葉・会話・台詞・ナレーション・歌詞は、ユーザーが別の言語を明示しない限り日本語にする。H3プロンプトでは日本語の発話を必ず <d>[Japanese] ...</d> として、話者ごとに固定IDを付ける。
引用符内またはユーザーが明示した台詞は一字も変更せず、指定回数がなければ一度だけ発声させる。同じ台詞・同じ意味の相づち・発声を繰り返さず、二重発声させない。台詞が明示されていないが場面上必要なら、短く自然な日本語を一度だけ創作してよい。無言・台詞なし・発声なしの指定では人声を追加しない。
字幕・テロップ・吹き出し・ロゴ・ウォーターマーク・意味不明な画面文字は、ユーザーが明示しない限り追加しない。指定のない新しい登場人物・モブ・画面外の声を追加せず、人物の分身や重複を作らない。話者の声質と人物同一性を維持し、口の動きと発声を一致させる。
BGMの有無・種類はユーザー指示を優先する。BGMを入れる場合は台詞と効果音を邪魔しない音量にし、台詞中は自動的に音量を下げる。BGM指定がない場合は勝手に追加しない。
長尺を複数区間で生成するときは、前区間の台詞・動作・導入を繰り返さず、未実行の続きだけを進める。'''

_DIALOGUE_TAG_RE = re.compile(r'<d>\[([^\]]+)\]\s*(.*?)</d>', re.IGNORECASE | re.DOTALL)
_SPEAKER_ID_RE = re.compile(r'\(S\d+(?:\s*,\s*S\d+)*\)')
_SPEECH_REQUEST_RE = re.compile(
    r'話す|話し(?:て|ま|かけ)|喋る|喋っ|喋り|しゃべる|しゃべっ|しゃべり|'
    r'言う|言っ|言います|台詞|セリフ|会話|ナレーション|歌う|歌って|歌詞|発声|掛け声|挨拶|あいさつ|'
    r'\b(?:speak|speaks|speaking|talk|talks|talking|say|says|dialogue|narration|sing|sings|singing)\b', re.I)
# A mixed list can prohibit voice, music and visible text in any order.
_BAN_ITEM = r'(?:台詞|セリフ|会話|ナレーション|発声|掛け声|歌声|歌詞|歌|人声|声|BGM|背景音楽|音楽|劇伴|サウンドトラック|字幕|テロップ|文字|ロゴ|効果音|環境音)'
_LIST_BAN_RE = re.compile(
    _BAN_ITEM + r'(?:[・、,／/\s]+' + _BAN_ITEM + r')*'
    r'(?:は|を|の)?\s*(?:なし|無し|不要|入れない|追加しない)', re.I)
_NO_SPEECH_RE = re.compile(
    r'無言|喋らない|しゃべらない|話さない|' + _LIST_BAN_RE.pattern, re.I)


def requests_global_silence(brief):
    """Recognize global silence without promoting a local/extra-voice ban.

    Quoted dialogue and visible lettering are content. A ban on narration or
    unscripted additions does not prohibit separately requested character lines.
    """
    directions = unicodedata.normalize('NFKC', brief or '')
    directions = re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*"', ' ', directions)
    for match in _NO_SPEECH_RE.finditer(directions):
        value = match.group()
        if not re.search(r'無言|人声|(?:^|[・、,／/\s])声|喋らない|しゃべらない|話さない|台詞|セリフ|会話|発声', value):
            continue  # Singing/narration alone is a distinct audio category.
        clause = re.split(r'[。.!?！？\n;；]', directions[:match.start()])[-1]
        # A later clause may give a genuine global ban, so never return False
        # early merely because an earlier scoped ban was found.
        if re.search(r'(?:以外|ほか|他|余計|余分|不要な|無用|追加の|指定外|未指定|アドリブ|勝手な|不明|二重|重複)[^、,]{0,24}$', clause):
            continue
        if re.search(r'(?:(?:前半|後半|冒頭|最後|この区間|発話中|台詞中|それ以降|その後|言った後|話した後)(?:は|では|の間)?|\d+(?:\.\d+)?秒(?:間|時点)?(?:は|では|から|まで|以降|の間)?)\s*$', clause):
            continue
        return True
    return False


_OTHER_LANGUAGE_RE = re.compile(r'英語|中国語|韓国語|フランス語|ドイツ語|スペイン語|イタリア語|ロシア語|ポルトガル語|English|Chinese|Korean|French|German|Spanish|Italian|Russian|Portuguese', re.IGNORECASE)
_MUSIC_REQUEST_RE = re.compile(r'BGM|背景音楽|音楽|劇伴|サウンドトラック|background\s+music|soundtrack', re.IGNORECASE)
_NO_MUSIC_RE = re.compile(
    r'(?:BGM|背景音楽|音楽|劇伴|サウンドトラック|background\s+music|soundtrack)'
    r'(?:は|を|の)?\s*(?:なし|無し|不要|入れない|追加しない|なしにする|without|none|no)',
    re.IGNORECASE,
)
_SPEECH_CLAIM_RE = re.compile(r'\b(?:says?|speaks?|shouts?|whispers?|narrates?|sings?|utters?|dialogue|narration|spoken\s+(?:line|words?|phrase)|human\s+voice)\b', re.IGNORECASE)
_SPOKEN_QUOTE_RE = re.compile(
    r'\b(?:says?|speaks?|shouts?|whispers?|narrates?|sings?|utters?)\b[^\n.!?"“”<>]{0,160}?["“]([^"”]+)["”]',
    re.IGNORECASE,
)

_STYLE_LOCK_RE = re.compile(
    r'MV_VISUAL_STYLE_LOCK:\s*(SOURCE_MATCH_AUTO|2D_ANIME|3D_CGI|PHOTOREAL_LIVE_ACTION)',
    re.IGNORECASE,
)
_IDENTITY_LOCK_RE = re.compile(r'MV_CHARACTER_IDENTITY_LOCK:\s*([^\n\r]+)', re.IGNORECASE)
_RENDERING_MEDIUM_RE = re.compile(
    r'RENDERING_MEDIUM\s*=\s*(2D_ANIME|3D_CGI|PHOTOREAL_LIVE_ACTION)',
    re.IGNORECASE,
)

_STYLE_CONTRACTS = {
    '2D_ANIME': (
        'Flat 2D cel animation with clean line art, hand-drawn forms, and cel shading is maintained in every shot.'
    ),
    '3D_CGI': (
        'Consistent 3D CGI models, materials, dimensional lighting, and the source render style are maintained in every shot.'
    ),
    'PHOTOREAL_LIVE_ACTION': (
        'Photoreal live-action cinematography, natural skin and fabric texture, physical lenses, and real-world lighting are maintained in every shot.'
    ),
}


def enforce_visual_style_lock(prompt, brief):
    """Make a manual medium lock unambiguous without replacing LM-authored scenes."""
    lock = _STYLE_LOCK_RE.search(brief or '')
    if not lock:
        return prompt
    requested = lock.group(1).upper()
    if requested == 'SOURCE_MATCH_AUTO':
        return prompt
    # A model may repeat the three choices from the instruction while explaining
    # why it chose one. Remove those prose markers, then restore only the selected
    # marker at the two visual ownership sections. Scene content remains intact.
    result = _RENDERING_MEDIUM_RE.sub('', prompt or '')
    marker = f'RENDERING_MEDIUM={requested}. {_STYLE_CONTRACTS[requested]} '
    first_section = (
        r'(subject_definitions\s*:\s*)'
        if re.search(r'subject_definitions\s*:', result, re.IGNORECASE)
        else r'(integrated_multimodal_description\s*:\s*)'
    )
    result = re.sub(
        first_section,
        lambda match: match.group(1) + marker,
        result,
        count=1,
        flags=re.IGNORECASE,
    )
    if re.search(r'detailed_description\s*:', result, re.IGNORECASE):
        result = re.sub(
            r'(detailed_description\s*:\s*)',
            lambda match: match.group(1) + marker,
            result,
            count=1,
            flags=re.IGNORECASE,
        )
    return result


def enforce_character_identity_lock(prompt, brief):
    """Keep explicit stable character traits after the LM conversion.

    English lock text is safe to inject verbatim into H3. Japanese lock text is
    left to the multimodal LM for translation, then checked by category below.
    """
    lock = _IDENTITY_LOCK_RE.search(brief or '')
    if not lock:
        return prompt
    notes = ' '.join(lock.group(1).split())
    if not notes or re.search(r'[ぁ-んァ-ヶ一-龯々]', notes):
        return prompt
    marker = (
        'CHARACTER_IDENTITY_LOCK=STRICT. ' + notes + ' '
        '<Picture 1> is authoritative. Preserve the same facial silhouette and proportions, '
        'eye shape and spacing, eyebrow shape, nose and mouth placement, jawline, bangs, and '
        'hair silhouette; do not replace them with a generic character face. '
        'Never alter these stable traits. '
    )
    section = (
        r'(subject_definitions\s*:\s*)'
        if re.search(r'subject_definitions\s*:', prompt or '', re.IGNORECASE)
        else r'(integrated_multimodal_description\s*:\s*)'
    )
    return re.sub(section, lambda match: match.group(1) + marker, prompt or '', count=1, flags=re.IGNORECASE)


def character_identity_lock_errors(prompt, brief):
    lock = _IDENTITY_LOCK_RE.search(brief or '')
    if not lock:
        return []
    subject_match = re.search(
        r'(?:subject_definitions|integrated_multimodal_description)\s*:\s*(.*?)(?=\n\s*(?:summary|retention_analysis|detailed_description|overall_soundscape|non_diegetic_music)\s*:|\Z)',
        prompt or '', re.IGNORECASE | re.DOTALL,
    )
    subject = subject_match.group(1) if subject_match else ''
    categories = (
        (r'hair|bob|bangs?|fringe', 'hair length and cut'),
        (r'eyes?|iris', 'eye color'),
        (r'outfit|clothing|wears?|bikini|dress|shirt|jacket', 'outfit'),
        (r'ears?|headband|headwear|accessor|tail|horns?', 'head accessories, ears, or tail'),
    )
    missing = [label for pattern, label in categories if not re.search(pattern, subject, re.IGNORECASE)]
    if missing:
        return [
            'Describe the reference character stable identity in subject_definitions, including: '
            + ', '.join(missing) + '. Preserve exact shapes, lengths, and colors from <Picture 1>.'
        ]
    return []


def visual_style_lock_errors(prompt, brief):
    """Require the image-aware LM conversion to keep the requested source medium."""
    lock = _STYLE_LOCK_RE.search(brief or '')
    if not lock:
        return []
    requested = lock.group(1).upper()
    rendered = {match.group(1).upper() for match in _RENDERING_MEDIUM_RE.finditer(prompt or '')}
    if requested == 'SOURCE_MATCH_AUTO':
        if len(rendered) != 1:
            return [
                'Inspect <Picture 1> and state exactly one rendering marker in the final prompt: '
                'RENDERING_MEDIUM=2D_ANIME, RENDERING_MEDIUM=3D_CGI, or '
                'RENDERING_MEDIUM=PHOTOREAL_LIVE_ACTION. Preserve that medium in every shot.'
            ]
        requested = next(iter(rendered))
    if rendered != {requested}:
        return [
            f'The style lock requires exactly RENDERING_MEDIUM={requested}. Remove every conflicting '
            'rendering marker and preserve this medium in subject_definitions and every timed shot.'
        ]
    visual = _main_description(prompt)
    positive_visual = re.sub(
        r'\b(?:no|not|never|without|avoid|forbid(?:den)?|exclude)\b[^.,;\n]{0,100}',
        '',
        visual,
        flags=re.IGNORECASE,
    )
    requirements = {
        '2D_ANIME': (r'\b2D\b|cel[- ]?(?:anime|animation|shading)|hand[- ]drawn|line art',
                     r'\b3D\s+CGI\b|photoreal(?:istic)?|live[- ]action|plastic skin'),
        '3D_CGI': (r'\b3D\b|CGI|computer[- ]generated|rendered model',
                   r'flat 2D|cel[- ]?(?:anime|animation)|hand[- ]drawn|live[- ]action'),
        'PHOTOREAL_LIVE_ACTION': (r'photoreal(?:istic)?|live[- ]action|real[- ]world camera|natural skin texture',
                                  r'flat 2D|cel[- ]?(?:anime|animation)|hand[- ]drawn|\b3D\s+CGI\b'),
    }
    required, forbidden = requirements[requested]
    errors = []
    if not re.search(required, visual, re.IGNORECASE):
        errors.append(f'Describe the entire visual timeline explicitly in the locked {requested} medium.')
    if re.search(forbidden, positive_visual, re.IGNORECASE):
        errors.append(f'Remove rendering language that conflicts with the locked {requested} medium.')
    return errors


def _supplied_dialogue_lines(brief):
    values = []
    for match in _DIALOGUE_TAG_RE.finditer(brief or ''):
        value = match.group(2).strip()
        if value:
            values.append(value)
    for match in re.finditer(r'「([^」]+)」|『([^』]+)』|“([^”]+)”|"([^"]+)"', brief or ''):
        value = next((item for item in match.groups() if item is not None), '').strip()
        if value and re.search(r'[ぁ-んァ-ヶ一-龯々ー]', value):
            values.append(value)
    return list(dict.fromkeys(values))


_TIMED_DIALOGUE_RE = re.compile(
    r'\[\s*(\d+(?:\.\d+)?)\s*[-–—~〜]\s*(\d+(?:\.\d+)?)\s*\]\s*'
    r'(\(S\d+(?:\s*,\s*S\d+)*\))?\s*<d>\[([^\]]+)\]\s*(.*?)</d>',
    re.IGNORECASE | re.DOTALL,
)


def _event_clock(seconds):
    value = max(0.0, float(seconds))
    minutes = int(value // 60)
    remainder = value - minutes * 60
    return f'{minutes:02d}:{remainder:06.3f}'


def restore_supplied_dialogue(prompt, brief):
    """Restore exact tagged user dialogue that an LLM accidentally drops.

    Visual direction remains authored by the LLM. Only explicit input tags are
    copied, so this cannot invent a line or turn production prose into speech.
    """
    events = []
    for match in _TIMED_DIALOGUE_RE.finditer(brief or ''):
        start, end, speaker, language, body = match.groups()
        events.append((float(start), float(end), speaker or '(S1)', language.strip(), body.strip()))
    if not events:
        return prompt
    section = re.search(
        r'((?:detailed_description|integrated_multimodal_description)\s*:\s*)(.*?)'
        r'(?=\n\s*overall_soundscape\s*:)',
        prompt or '', re.IGNORECASE | re.DOTALL,
    )
    if not section:
        return prompt
    body = section.group(2).rstrip()
    # LMs sometimes keep the exact words but widen two precise lyric windows
    # into one whole-shot speech range. Remove those model-authored speech
    # sentences and append one authoritative event per original input range.
    supplied_words = {words for _, _, _, _, words in events}
    remove_spans = []
    for tag_match in _DIALOGUE_TAG_RE.finditer(body):
        words = tag_match.group(2).strip()
        if words not in supplied_words and '<Audio 1> は元曲' not in (brief or ''):
            continue
        prefix = body[max(0, tag_match.start() - 320):tag_match.start()]
        from_matches = list(re.finditer(r'\bFrom\s+\d{2}:\d{2}\.\d{3}\s+to\s+\d{2}:\d{2}\.\d{3}\b', prefix))
        if from_matches:
            start = max(0, tag_match.start() - len(prefix) + from_matches[-1].start())
        else:
            boundaries = list(re.finditer(r'(?:(?<=\.)\s+|(?<=\n))', body[:tag_match.start()]))
            start = boundaries[-1].end() if boundaries else 0
        ending = re.search(r'\.(?=\s|$)', body[tag_match.end():])
        end = tag_match.end() + ending.end() if ending else tag_match.end()
        remove_spans.append((start, end))
    for start, end in sorted(remove_spans, reverse=True):
        body = body[:start].rstrip() + ' ' + body[end:].lstrip()
    scheduled = [
        (start, end,
            f'From {_event_clock(start)} to {_event_clock(end)}, the assigned singer performs '
            f'exactly once with mouth movement synchronized to <Audio 1>: {speaker} {exact_tag}.'
        )
        for start, end, speaker, language, words in events
        for exact_tag in [f'<d>[{language}] {words}</d>']
    ]
    phase_re = re.compile(
        r'(?m)^\s*\[?(\d{2}):(\d{2}(?:\.\d+)?)\s*[-–—~〜]\s*'
        r'(\d{2}):(\d{2}(?:\.\d+)?)\]?\s*$'
    )
    phases = list(phase_re.finditer(body))
    insertions = {}
    unslotted = []
    for start, end, sentence in scheduled:
        chosen = None
        for index, phase in enumerate(phases):
            phase_start = int(phase.group(1)) * 60 + float(phase.group(2))
            phase_end = int(phase.group(3)) * 60 + float(phase.group(4))
            if phase_start - 1e-3 <= start < phase_end + 1e-3 and end <= phase_end + 1e-3:
                chosen = index
                break
        if chosen is None:
            unslotted.append(sentence)
        else:
            insertion_point = phases[chosen + 1].start() if chosen + 1 < len(phases) else len(body)
            insertions.setdefault(insertion_point, []).append(sentence)
    for insertion_point, sentences in sorted(insertions.items(), reverse=True):
        body = body[:insertion_point].rstrip() + '\n' + '\n'.join(sentences) + '\n\n' + body[insertion_point:].lstrip()
    if unslotted:
        body += ('\n' if body else '') + '\n'.join(unslotted)
    return prompt[:section.start(2)] + body + prompt[section.end(2):]


def requests_scripted_speech(brief):
    if requests_global_silence(brief):
        return False
    directions = _normalized_for_matching(brief)
    directions = re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*"', ' ', directions)
    directions = _NO_SPEECH_RE.sub(' ', directions)
    return bool(_SPEECH_REQUEST_RE.search(directions))


def _main_description(prompt):
    match = re.search(
        r'(?:integrated_multimodal_description|detailed_description)\s*:\s*(.*?)(?=\n\s*overall_soundscape\s*:)',
        prompt or '',
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ''


def _normalized_for_matching(value):
    return unicodedata.normalize('NFKC', value or '')


def normalize_reference_labels(prompt):
    result = prompt or ''
    for label in ('Subject', 'Picture', 'Video', 'Audio'):
        result = re.sub(
            rf'(?<!<)\b{label}\s+(\d+)\b(?!>)',
            lambda match, label=label: f'<{label} {match.group(1)}>',
            result,
            flags=re.IGNORECASE,
        )
    return result


def enforce_dialogue_locality(prompt, brief):
    """Clean empty markup without moving dialogue or inventing speakers."""
    # Missing language markup is a repairable form difference, not bad dialogue.
    if not _OTHER_LANGUAGE_RE.search(brief or ''):
        prompt = re.sub(r'<d>(.*?)</d>', lambda m: '<d>'+ (m.group(1).strip() if m.group(1).strip().startswith('[') else '[Japanese] '+m.group(1).strip())+'</d>', prompt, flags=re.I|re.S)
    def quoted_speech(match):
        words = match.group(1)
        prefix = prompt[max(0,match.start()-24):match.start()]
        if not re.search(r'[ぁ-んァ-ヶ一-龯]',words) or re.search(r'\b(?:never|not|no|without)\b[^.!?]*$',prefix,re.I):
            return match.group()
        return match.group().replace('"'+words+'"','<d>[Japanese] '+words+'</d>').replace('“'+words+'”','<d>[Japanese] '+words+'</d>')
    prompt = _SPOKEN_QUOTE_RE.sub(quoted_speech,prompt)
    # A Japanese LM may translate the scene imperfectly yet keep exact speech
    # in Japanese quotation marks. Only a following speech verb makes it speech;
    # quoted signs, timers and other visible lettering remain untouched.
    japanese_quote = re.compile(r'「([^」]+)」(?=(?:と|って)[^。！？!\n「」]{0,20}?(?:言(?:う|い|った)|話(?:す|し|した)|告げ|叫(?:ぶ|び)|尋ね|答え|呟(?:く|き)))')
    pieces = re.split(r'(<d>.*?</d>)', prompt, flags=re.I|re.S)
    for i in range(0, len(pieces), 2):
        pieces[i] = japanese_quote.sub(lambda q: '<d>[Japanese] '+q.group(1)+'</d>', pieces[i])
    prompt = ''.join(pieces)
    result = re.sub(r'<d>\s*\[[^\]]+\]\s*</d>', '', prompt, flags=re.I)
    return re.sub(r'(["“])\s*(["”])', '', result)


def dialogue_format_warnings(prompt):
    main = _main_description(prompt)
    warnings = []
    if _DIALOGUE_TAG_RE.search(main) and not _SPEAKER_ID_RE.search(main):
        warnings.append('話者ID省略：文脈による解釈を許容しました。')
    if _SPOKEN_QUOTE_RE.search(main):
        warnings.append('通常の引用符による台詞表記を許容しました。')
    bodies = [m.group(2).strip() for m in _DIALOGUE_TAG_RE.finditer(main)]
    if len(bodies) != len(set(bodies)):
        warnings.append('同じ台詞の複数記載があります。意図した繰返しか確認してください。')
    return warnings


def enforce_no_unscripted_speech(prompt, brief):
    main = _main_description(prompt)
    if _DIALOGUE_TAG_RE.search(main):
        return prompt
    if (requests_scripted_speech(brief) or VOCAL_REQUEST.search(brief or '')) and not requests_global_silence(brief):
        return prompt
    main_clause = (
        ' No character speaks. No dialogue, narration, singing, chanting, intelligible words, '
        'unintelligible speech-like babble, or other human vocalization is audible.'
    )
    result = re.sub(
        r'((?:integrated_multimodal_description|detailed_description)\s*:\s*)(.*?)(\n\s*overall_soundscape\s*:)',
        lambda match: match.group(1) + match.group(2).rstrip() + main_clause + match.group(3),
        prompt,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    sound_clause = (
        ' No dialogue, narration, singing, chanting, intelligible words, or unintelligible '
        'speech-like vocal sounds are present.'
    )
    return re.sub(
        r'(overall_soundscape\s*:\s*)(.*?)(\n\s*non_diegetic_music\s*:)',
        lambda match: match.group(1) + match.group(2).rstrip() + sound_clause + match.group(3),
        result,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )


def enforce_music_policy(prompt, brief):
    normalized_brief = _normalized_for_matching(brief)
    banned_music = any(_MUSIC_REQUEST_RE.search(m.group()) for m in _LIST_BAN_RE.finditer(normalized_brief))
    requested = bool(_MUSIC_REQUEST_RE.search(normalized_brief)) and not (_NO_MUSIC_RE.search(normalized_brief) or banned_music)
    section = re.search(r'(non_diegetic_music\s*:\s*)(.*)\Z', prompt or '', re.IGNORECASE | re.DOTALL)
    if not section:
        return prompt
    if not requested:
        return prompt[:section.start()] + section.group(1) + 'N/A'
    music = section.group(2).strip()
    if not _DIALOGUE_TAG_RE.search(_main_description(prompt)):
        return prompt
    if not music or music.casefold() in {'n/a', 'none', 'no music'}:
        music = 'Background music appropriate to the requested scene is present.'
    ducking = ' The music stays below the dialogue and automatically ducks during every spoken line.'
    return prompt[:section.start()] + section.group(1) + music.rstrip() + ducking


def dialogue_format_errors(prompt, brief):
    errors = []
    main = _main_description(prompt)
    tags = list(_DIALOGUE_TAG_RE.finditer(main))
    supplied = _supplied_dialogue_lines(brief)
    explicit_other_language = bool(_OTHER_LANGUAGE_RE.search(brief or ''))
    no_speech = requests_global_silence(brief)
    requests_speech = requests_scripted_speech(brief)
    positive_main = re.sub(r'\b(?:no|without)\b[^.\n]*', '', main, flags=re.I)
    claims_speech = bool(_SPEECH_CLAIM_RE.search(positive_main))

    if no_speech and tags:
        errors.append('The user requested no human speech, so remove every <d> dialogue tag and every vocal line.')
    if not no_speech and (requests_speech or claims_speech) and not tags:
        errors.append('The user requests speech. Put every spoken line in <d>[Japanese] exact words</d> and bind the speaker with a stable (S1) ID.')

    bodies = []
    for tag in tags:
        language = tag.group(1).strip()
        body = tag.group(2).strip()
        bodies.append(body)
        if not explicit_other_language and language.casefold() != 'japanese':
            errors.append('All speech must use the [Japanese] language tag unless the user explicitly requests another language.')
        if not explicit_other_language and (not re.search(r'[ぁ-んァ-ヶ一-龯々ー]', body)
                or re.search(r'\b(?:TBD|placeholder|insert dialogue|actual words)\b|台詞をここ|セリフをここ', body, re.I)):
            errors.append('Write actual context-appropriate Japanese words inside every dialogue tag; placeholders and untranslated English are not speech.')

    for line in supplied:
        if line not in main:
            errors.append(f'The supplied dialogue is missing from the scene: {line}')

    for match in _TIMED_DIALOGUE_RE.finditer(brief or ''):
        start, end, speaker, language, words = match.groups()
        tag_pattern = (
            r'<d>\[' + re.escape(language.strip()) + r'\]\s*'
            + re.escape(words.strip()) + r'\s*</d>'
        )
        occurrences = list(re.finditer(tag_pattern, main, re.IGNORECASE | re.DOTALL))
        if len(occurrences) != 1:
            errors.append(f'The supplied dialogue must appear exactly once: {words.strip()}')
            continue
        prefix = main[max(0, occurrences[0].start() - 220):occurrences[0].start()]
        exact_range = rf'From\s+{re.escape(_event_clock(float(start)))}\s+to\s+{re.escape(_event_clock(float(end)))}\b'
        if not re.search(exact_range, prefix, re.IGNORECASE):
            errors.append(
                f'Keep the exact supplied speech range {_event_clock(float(start))}-{_event_clock(float(end))} '
                f'for: {words.strip()}'
            )

    return list(dict.fromkeys(errors))


def enforce_segment_dialogue_schedule(local_prompt, brief, segment):
    """Put authoritative MV lyric events directly into the consumed segment prompt.

    This is the final layer after timeline segmentation, so an LM-authored broad
    speech range cannot erase the exact ASR-aligned windows. A line crossing a
    generation boundary is marked as a continuation in the following raw clip.
    """
    if '<Audio 1> は元曲' not in (brief or ''):
        return local_prompt
    events = []
    for match in _TIMED_DIALOGUE_RE.finditer(brief or ''):
        start, end, speaker, language, words = match.groups()
        events.append((float(start), float(end), speaker or '(S1)', language.strip(), words.strip()))
    if not events:
        return local_prompt
    start = segment.output_start / 24
    end = (segment.output_start + segment.output_frames) / 24
    context = segment.context_frames / 24
    scheduled = []
    for event_start, event_end, speaker, language, words in events:
        if event_start >= end or event_end <= start:
            continue
        local_start = context + max(event_start, start) - start
        local_end = context + min(event_end, end) - start
        tag = f'{speaker} <d>[{language}] {words}</d>'
        if event_start < start:
            scheduled.append(
                f'From {_event_clock(local_start)} to {_event_clock(local_end)}, continue only the remaining '
                f'part of the already-started vocal line synchronized to <Audio 1>: {tag}. '
                'Do not restart the line or repeat any earlier word.'
            )
        elif event_end > end:
            scheduled.append(
                f'From {_event_clock(local_start)} to {_event_clock(local_end)}, begin this vocal line exactly once '
                f'with mouth movement synchronized to <Audio 1>: {tag}. The line continues only through the next '
                'audiovisual guide; do not rush, finish, or repeat it inside this clip.'
            )
        else:
            scheduled.append(
                f'From {_event_clock(local_start)} to {_event_clock(local_end)}, perform exactly once with mouth '
                f'movement synchronized to <Audio 1>: {tag}. Start and finish inside this range.'
            )
    # Remove any model-authored dialogue markup first. The exact source events
    # below are the only vocal words H3 may consume for this segment.
    result = re.sub(r'(?:\(S\d+(?:\s*,\s*S\d+)*\)\s*)?<d>.*?</d>', '', local_prompt or '', flags=re.I | re.S)
    if not scheduled:
        return result
    block = (
        '\n\nAUTHORITATIVE VOCAL SCHEDULE — overrides every broader speech range above. '
        'Use only these tagged words; do not add filler, echoes, or a second voice.\n'
        + '\n'.join(scheduled)
    )
    match = re.search(r'\n\s*overall_soundscape\s*:', result, re.IGNORECASE)
    if match:
        return result[:match.start()] + block + result[match.start():]
    return result.rstrip() + block


def long_timeline():
    name = '_h3_standard_timeline_contract'
    if name not in sys.modules:
        path = Path(folder_paths.base_path)/'custom_nodes/ComfyUI-MiniMax-H3-Long-Video/minimax_h3_long_video/timeline.py'
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def system_prompt(mode, duration, boundaries=()):
    fields = 'subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music' if 'Ref2' in mode else 'integrated_multimodal_description, overall_soundscape, non_diegetic_music'
    boundary_rule = ('Generation boundaries: '+', '.join(f'{b:g} seconds' for b in boundaries)+'. No action time range may cross a boundary. At each boundary keep the current camera framing and subject positions; the next range advances the already-reached state, without restaging the action onset. Describe each side separately. ') if boundaries else ''
    return f'''{boundary_rule}Rewrite the brief as a MiniMax H3 {mode} video lasting {duration:g} seconds. Return only JSON with these string keys: {fields}. The JSON schema enforces field limits. Aim for 350-450 words TOTAL. End immediately after the JSON object.
All visual prose is English. Translate the production instructions into visible actions in their original order; NEVER read them aloud or discuss rendering, software, or the rewrite process. Only actual dialogue/lyrics and requested visible lettering retain their original language. Exact user-supplied words must remain unchanged. Default voice language is Japanese. Screams, breaths and gasps are short NONVERBAL sounds, not narration. Describe them in English as short wordless sounds by the assigned subject, preserving the requested voice; do not impose a female voice on every subject or invent phonetic dialogue.
The main description MUST contain the complete beginning, middle, and ending, spanning the entire requested duration. Never put later actions only in the summary. The main description uses explicit [MM:SS-MM:SS] ranges for successive phases. Cover 00:00 through the requested final second, putting the requested ending in the final range. Keep the action progressing throughout; do not finish the story early and pad the rest with black screen. These ranges are mandatory even for one continuous shot. They are timing phases, not cuts. Maintain continuous action and camera movement unless the user requests cuts; never repeat an establishing view or reset the actors between phases. Keep all requested actions, characters, camera movements and sound. Preserve causal order: an initiating event must happen before its consequences, never reappear in the final phase. Allocate the final phase solely to completing the requested ending state. At each boundary explicitly state the already-reached position and ongoing movement, not a fresh start. Every spoken turn belongs to exactly one timed phase and must finish before that phase ends; put a short natural pause at each generation boundary. Never split a sentence or copy its full text into both phases. Do not add people, narration, captions or music without a request. Speech uses (S1) <d>[Japanese] actual words</d> once with natural pauses. If speech is requested without exact words, compose a short natural Japanese line appropriate to the scene INSIDE the dialogue tag; never leave speech as an instruction such as talks, chats, greets or speaks Japanese. Bind each line to the character who says it. Use one brief line unless a multi-speaker exchange is requested. Never use placeholders or pronounce production directions. Silence instructions override creative dialogue. No speech text in soundscape or music.
For references: subject_definitions contains ONLY stable appearance, never the starting location, pose or action. For every visible person, inspect the reference and explicitly state hair length/cut and bangs, eye color, head accessories or ears, facial silhouette and proportions, eye shape and spacing, eyebrow shape, nose and mouth placement, jawline, body build, every outfit layer, footwear, and any tail or mechanical part. Treat the reference pixels as authoritative identity: do not substitute a generic face that merely shares hair, eye, ear, or clothing colors, and never replace a bob with long hair or vice versa. Keep at least one face-readable medium or close view in each generated segment so identity can be verified. The detailed description owns all changing locations and poses. Soundscape contains only continuous ambience; put one-off growls, attacks, screams and thunder onsets in their timed action ranges. For references: subject_definitions gives separate <Subject 1>, <Subject 2> etc with appearance and correct <Picture N> source. One image may contain multiple people. Retention states fully_preserved/partially_preserved/attribute_transfer/weak_reference relationships. Use the same Subject labels in the action timeline. Ref2VA images define identity, not compulsory first frames. In I2VA, state <Picture 1> is the first frame at 0.00 seconds.
Soundscape contains environmental and nonverbal sounds. Music is N/A unless requested. Preserve deliberate silence and requested BGM.
'''


def split_conversion_issues(errors, prompt, image_count):
    """Tolerate minor mixed-language metadata, not an untranslated visual draft."""
    visual_match = re.search(r'(?:integrated_multimodal_description|detailed_description)\s*:\s*(.*?)(?=\n(?:overall_soundscape|non_diegetic_music)\s*:|\Z)', prompt, re.S | re.I)
    visual = visual_match.group(1) if visual_match else prompt
    # Dialogue and visible lettering may legitimately be entirely Japanese.
    visual = re.sub(r'<d>.*?</d>|「[^」]*」|『[^』]*』|["“][^"”]*["”]', '', visual, flags=re.S | re.I)
    japanese_count = len(re.findall(r'[\u3040-\u30ff\u3400-\u9fff]', visual))
    untranslated_visual = japanese_count >= 80 and japanese_count > len(re.findall(r'[A-Za-z]', visual))
    warnings, blocking = [], []
    used = {int(n) for n in re.findall(r'<Picture\s+(\d+)>', prompt, re.I)}
    for error in errors:
        minor = error.startswith((
            'Split the action phase at ',
            'Use explicit time ranges with generation boundaries:',
            'Soundscape contains spoken words or written cries.',
            'Untranslated Japanese production prose remains',
            'retention_analysis must state reference preservation relationships',
        ))
        if error.startswith('Untranslated Japanese production prose remains') and untranslated_visual:
            minor = False
        if error.startswith('Bind the connected image sources '):
            # Missing labels are metadata omissions; nonexistent image IDs are not.
            minor = used.issubset(set(range(1, image_count + 1)))
        (warnings if minor else blocking).append(error)
    return blocking, warnings

class H3StandardPrompt:
    @classmethod
    def INPUT_TYPES(cls):
        schema = copy.deepcopy(nodes.NODE_CLASS_MAPPINGS['H3VideoJapanesePromptLMStudio'].INPUT_TYPES())
        schema['required']['fallback_to_japanese'][1].update(default=False, display_name='旧・原文続行（安全のため無効）', tooltip='互換性のため残していますが、ONでも未変換原文は動画へ渡しません。内容不備は最大2回修正依頼し、未解決なら停止します。')
        schema['required']['mode'] = (['T2VA','I2VA','Ref2VA (R2V)'],)
        schema['required']['duration_seconds'][1].update(default=15.0, min=0.1, max=600.0,
            display_name='秒数指定がない場合の長さ（秒）')
        schema['optional']['extra_rules'][1].update(
            default=DEFAULT_EXTRA_RULES,
            display_name='LM Studioの基本ルール（通常はこのまま）',
        )
        schema['optional']['context_frames'] = (['22','39'],{'default':'39'})
        schema['optional']['auto_gpu_for_prompt'] = ('BOOLEAN', {'default': False, 'display_name': 'LLM変換時にGPUを使用し、動画生成前にCPUへ戻す', 'tooltip': 'このPCのLM Studio用。モデル・量子化・コンテキスト長・並列数を維持して切替。変換失敗時もCPUへ戻します。'})
        return schema

    ensure_cpu_for_video = staticmethod(ensure_cpu_for_video)

    RETURN_TYPES = ('STRING','STRING','INT','INT','MINIMAX_H3_LONG_PROMPT_PLAN')
    RETURN_NAMES = ('H3_PROMPT','実行計画','総フレーム数','内部区間フレーム数','区間プロンプト計画')
    FUNCTION = 'convert'
    CATEGORY = 'MiniMax H3 Video/Local Prompt'

    def convert(self, japanese_instruction, mode, duration_seconds, model, api_base,
                temperature, max_tokens, timeout_seconds, fallback_to_japanese,
                extra_rules='', source_h3_prompt='', external_h3_prompt='',
                reference_image_1=None, reference_image_2=None, reference_image_3=None,
                reference_image_4=None, reference_image_5=None, context_frames='39', auto_gpu_for_prompt=False):
        brief = (japanese_instruction or '').strip()
        source = external_h3_prompt if (external_h3_prompt or '').strip() else source_h3_prompt
        if not brief and not (source or '').strip():
            raise ValueError('日本語指示またはH3プロンプトを入力してください。')
        # The two fields are alternate input routes. A stale pasted example
        # must not become an implicit draft or override a new Japanese brief.
        duration, origin = timing(brief if brief else source, duration_seconds)
        tl = long_timeline()
        max_raw = segment_frame_budget(duration, int(context_frames), tl)
        planned = tl.plan_segments(tl._h3_grid_frames(max(1, round(duration*24))), int(context_frames), False, max_raw, exact_output_frames=max(1, round(duration*24)))
        boundaries = [s.output_start/24 for s in planned[1:]]
        prompt = source
        tolerated_warnings = []
        status = '直接入力：LM Studio・外部API呼出しなし'
        device_report = {'enabled': False}
        if brief:
            helper = nodes.NODE_CLASS_MAPPINGS['QwenJapanesePromptLMStudio']()
            helper.SYSTEM_PROMPT = system_prompt(mode, duration, boundaries)
            helper.STREAM_RESPONSE = True
            helper.H3_SAMPLING = True
            images = [x for x in (reference_image_1,reference_image_2,reference_image_3,reference_image_4,reference_image_5) if x is not None]
            expected = ['subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music'] if 'Ref2' in mode else ['integrated_multimodal_description','overall_soundscape','non_diegetic_music']
            helper.H3_FIELDS = expected
            extra_rules = extra_rules.replace(DEFAULT_EXTRA_RULES, '').strip()
            instruction = brief
            best_candidate = None
            with prompt_gpu_session(helper, model, api_base, auto_gpu_for_prompt) as device_report:
                for attempt in range(3):
                    print(f'[H3] LLM conversion attempt {attempt + 1}/3; unconverted fallback disabled.')
                    # Always false, including old workflows that still serialize true.
                    prompt, status = helper.convert(instruction,model,api_base,temperature,max_tokens,
                        timeout_seconds,False,extra_rules,input_images=images,reasoning='off')
                    prompt, valid = normalize_lm_fields(prompt, expected)
                    if 'Ref2' in mode:
                        prompt = normalize_reference_labels(prompt)
                    prompt = restore_supplied_dialogue(prompt, brief)
                    prompt = enforce_character_identity_lock(prompt, brief)
                    prompt = enforce_visual_style_lock(prompt, brief)
                    prompt = enforce_dialogue_locality(prompt, brief)
                    errors = [] if valid else ['Return the complete ordered H3 fields with nonempty English visual descriptions.']
                    errors += conversion_errors(prompt, brief, _supplied_dialogue_lines(brief), duration)
                    errors += reference_format_errors(prompt,len(images)) if 'Ref2' in mode else []
                    errors += dialogue_format_errors(prompt, brief)
                    errors += character_identity_lock_errors(prompt, brief)
                    errors += visual_style_lock_errors(prompt, brief)
                    errors += boundary_errors(prompt, duration, boundaries)
                    errors, tolerated_warnings = split_conversion_issues(errors, prompt, len(images))
                    boundary_warnings = [w for w in tolerated_warnings if w.startswith(('Split the action phase at ', 'Use explicit time ranges with generation boundaries:', 'Soundscape contains spoken words', 'The user requests speech. Put every spoken line', 'Untranslated Japanese production prose'))]
                    if not errors and (best_candidate is None or len(tolerated_warnings) < len(best_candidate[2])):
                        best_candidate = (prompt, status, list(tolerated_warnings), attempt + 1)
                    if not errors and boundary_warnings and attempt < 2:
                        instruction = (brief + '\n\nREWRITE_CORRECTION: Minimally repair the content-complete draft below. '
                            'Preserve every exact spoken line, its speaker and requested action. Repair the flagged timing/markup and translate any flagged visual prose into English; keep dialogue verbatim. '
                            + '; '.join(boundary_warnings) + '\n\nCONTENT_COMPLETE_DRAFT:\n' + prompt)
                        print('[H3] Repairing continuation timing within the existing 3-attempt limit.')
                        continue
                    if not errors:
                        prompt, status, tolerated_warnings, selected_attempt = best_candidate
                        status += f' / blocking checks passed (selected attempt {selected_attempt}/3)'
                        if tolerated_warnings:
                            print('[H3] Continuing with nonblocking conversion warnings: ' + '; '.join(tolerated_warnings))
                        break
                    reasons = '; '.join(dict.fromkeys(errors))
                    print(f'[H3] Rejected conversion {attempt + 1}/3: {reasons}')
                    if attempt == 2:
                        if best_candidate is not None:
                            prompt, status, tolerated_warnings, selected_attempt = best_candidate
                            status += f' / blocking checks passed (retained attempt {selected_attempt}/3; optional rewrite regressed)'
                            print('[H3] Retaining the content-complete candidate; later optional rewrites lost required content.')
                            break
                        raise RuntimeError('H3変換が内容検査に3回不合格となりました。原文は動画へ渡しません。理由: ' + reasons)
                    instruction = (brief + '\n\nREWRITE_CORRECTION: Generate a fresh conversion. '
                        'Rewrite the ORIGINAL brief above, preserving its actions, references, duration and exact supplied dialogue. '
                        'Fix these errors: ' + reasons)
            prompt = enforce_music_policy(prompt, brief)
            prompt = enforce_no_unscripted_speech(prompt, brief)
        if not brief:
            prompt = enforce_dialogue_locality(prompt, '')
        tl = long_timeline()
        count_frames = max(1, round(duration*24))
        length = tl._h3_grid_frames(count_frames)
        # max_raw was selected before conversion so planning and validation agree.
        context = int(context_frames)
        segments = tl.plan_segments(length, context, False, max_raw, exact_output_frames=count_frames)
        local_prompts = [
            enforce_segment_dialogue_schedule(segment_prompt(prompt, duration, item, len(segments)), brief, item)
            for item in segments
        ]
        plan = {
            'schema':tl.PROMPT_PLAN_SCHEMA_VERSION,'length_input':length,
            'delivered_length':count_frames,'requested_output_frames':count_frames,
            'preserve_generated_audio':True,
            'quality_policy':THREE_MODE_POLICY,
            'max_raw_frames':max_raw,'context_frames':context,'has_initial_latent':False,
            'segments':[{**tl._segment_record(s),'prompt':local_prompt}
                        for s, local_prompt in zip(segments, local_prompts)],
        }
        if device_report.get('enabled'):
            plan['lm_device_guard'] = {'api_base': device_report['api_base'], 'identifier': model}
        report = {'route':status,'mode':mode,'duration_source':origin,'duration_seconds':count_frames/24,
                  'segment_count':len(segments),'dialogue_count_changes_duration':False,
                  'quality_policy':THREE_MODE_POLICY,
                  'llm_device':device_report,
                  'raw_segment_seconds':[s.raw_frames/24 for s in segments],
                  'audio_content_audit':'Local Japanese scripted-speech guard runs when configured in Long Video; full audio quality still requires listening. LM Studio visual inspection cannot verify speech.',
                  'warnings':dialogue_format_warnings(prompt) + tolerated_warnings,
                  'windows':[[s.output_start/24,(s.output_start+s.output_frames)/24] for s in segments]}
        return prompt,json.dumps(report,ensure_ascii=False,indent=2),length,max_raw,plan


NODE_CLASS_MAPPINGS = {'H3StandardPrompt':H3StandardPrompt}
NODE_DISPLAY_NAME_MAPPINGS = {'H3StandardPrompt':'通常H3プロンプト：直接入力／日本語LM変換・指定尺'}
