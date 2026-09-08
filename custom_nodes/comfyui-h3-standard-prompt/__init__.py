import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import unicodedata

import folder_paths
from .lm_client import LocalLMHelper
from .standard import timing, timeline, segment_prompt, normalize_lm_fields, reference_format_errors


DEFAULT_EXTRA_RULES = '''話し言葉・会話・台詞・ナレーション・歌詞は、ユーザーが別の言語を明示しない限り日本語にする。H3プロンプトでは日本語の発話を必ず <d>[Japanese] ...</d> として、話者ごとに固定IDを付ける。
引用符内またはユーザーが明示した台詞は一字も変更せず、指定回数がなければ一度だけ発声させる。同じ台詞・同じ意味の相づち・発声を繰り返さず、二重発声させない。台詞が明示されていないが場面上必要なら、短く自然な日本語を一度だけ創作してよい。無言・台詞なし・発声なしの指定では人声を追加しない。
字幕・テロップ・吹き出し・ロゴ・ウォーターマーク・意味不明な画面文字は、ユーザーが明示しない限り追加しない。指定のない新しい登場人物・モブ・画面外の声を追加せず、人物の分身や重複を作らない。話者の声質と人物同一性を維持し、口の動きと発声を一致させる。
BGMの有無・種類はユーザー指示を優先する。BGMを入れる場合は台詞と効果音を邪魔しない音量にし、台詞中は自動的に音量を下げる。BGM指定がない場合は勝手に追加しない。
長尺を複数区間で生成するときは、前区間の台詞・動作・導入を繰り返さず、未実行の続きだけを進める。'''

_DIALOGUE_TAG_RE = re.compile(r'<d>\[([^\]]+)\]\s*(.*?)</d>', re.IGNORECASE | re.DOTALL)
_SPEAKER_ID_RE = re.compile(r'\(S\d+(?:\s*,\s*S\d+)*\)')
_SPEECH_REQUEST_RE = re.compile(r'話す|喋る|しゃべる|言う|台詞|セリフ|会話|ナレーション|歌う|歌詞|発声|掛け声')
_NO_SPEECH_RE = re.compile(
    r'無言|人声なし|喋らない|しゃべらない|話さない|'
    r'(?:台詞|セリフ|会話|ナレーション|発声|掛け声|歌声|歌詞)'
    r'(?:[・、,／/\s]*(?:台詞|セリフ|会話|ナレーション|発声|掛け声|歌声|歌詞))*'
    r'[・、,／/\s]*(?:なし|無し|不要|入れない|追加しない)'
)
_OTHER_LANGUAGE_RE = re.compile(r'英語|中国語|韓国語|フランス語|ドイツ語|スペイン語|イタリア語|ロシア語|ポルトガル語|English|Chinese|Korean|French|German|Spanish|Italian|Russian|Portuguese', re.IGNORECASE)
_MUSIC_REQUEST_RE = re.compile(r'BGM|背景音楽|音楽|劇伴|サウンドトラック|background\s+music|soundtrack', re.IGNORECASE)
_NO_MUSIC_RE = re.compile(
    r'(?:BGM|背景音楽|音楽|劇伴|サウンドトラック|background\s+music|soundtrack)'
    r'(?:は|を|の)?\s*(?:なし|無し|不要|入れない|追加しない|なしにする|without|none|no)',
    re.IGNORECASE,
)
_SPEECH_CLAIM_RE = re.compile(r'\b(?:says?|speaks?|shouts?|whispers?|narrates?|sings?|utters?|dialogue|narration|spoken\s+(?:line|words?|phrase)|human\s+voice)\b', re.IGNORECASE)
_SPOKEN_QUOTE_RE = re.compile(
    r'\b(?:says?|speaks?|shouts?|whispers?|narrates?|sings?|utters?)\b[^\n.!?]{0,160}["“]([^"”]+)["”]',
    re.IGNORECASE,
)


def _supplied_dialogue_lines(brief):
    values = []
    for match in re.finditer(r'「([^」]+)」|『([^』]+)』|“([^”]+)”|"([^"]+)"', brief or ''):
        value = next((item for item in match.groups() if item is not None), '').strip()
        if value and re.search(r'[ぁ-んァ-ヶ一-龯々ー]', value):
            values.append(value)
    return values


def _parse_created_dialogue(response):
    text = (response or '').strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    raw_lines = payload.get('lines') if isinstance(payload, dict) else None
    if not isinstance(raw_lines, list):
        return []
    result = []
    for item in raw_lines:
        if not isinstance(item, str):
            continue
        value = item.strip().strip('「」『』“”"')
        if not value or len(value) > 160:
            continue
        if not re.search(r'[ぁ-んァ-ヶ一-龯々ー]', value):
            continue
        if value not in result:
            result.append(value)
    return result[:4]


def create_japanese_dialogue(brief, model, api_base, timeout_seconds):
    dialogue_helper = LocalLMHelper()
    dialogue_helper.SYSTEM_PROMPT = '''Create the actual Japanese words that will be spoken in the requested video.
The user requested speech but did not supply exact dialogue. Infer short, natural, context-appropriate Japanese lines from the scene and duration.
Return only strict JSON in this form: {"lines":["actual Japanese line"]}
Use one line unless the brief clearly requires a multi-speaker exchange. Use at most four short lines. Do not return descriptions, placeholders, translations, speaker labels, Markdown, or repeated wording.'''
    last_response = ''
    for temperature in (0.2, 0.0):
        last_response, _ = dialogue_helper.convert(
            brief,
            model,
            api_base,
            temperature,
            512,
            timeout_seconds,
            False,
            '',
            input_images=[],
            reasoning='off',
        )
        lines = _parse_created_dialogue(last_response)
        if lines:
            return lines
    print('[H3] 台詞JSONの形式警告。動画プロンプト生成側で自然な台詞を補います。')
    return []


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
    if _SPEECH_REQUEST_RE.search(brief or '') and not _NO_SPEECH_RE.search(brief or ''):
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
    requested = bool(_MUSIC_REQUEST_RE.search(normalized_brief)) and not _NO_MUSIC_RE.search(normalized_brief)
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
    no_speech = bool(_NO_SPEECH_RE.search(brief or ''))
    requests_speech = bool(_SPEECH_REQUEST_RE.search(brief or '')) and not no_speech
    claims_speech = bool(_SPEECH_CLAIM_RE.search(main))

    if no_speech and tags:
        errors.append('The user requested no human speech, so remove every <d> dialogue tag and every vocal line.')
    if (requests_speech or claims_speech) and not tags and not _SPOKEN_QUOTE_RE.search(main) and not any(line in main for line in supplied):
        errors.append('The user requests speech. Put every spoken line in <d>[Japanese] exact words</d> and bind the speaker with a stable (S1) ID.')

    bodies = []
    for tag in tags:
        language = tag.group(1).strip()
        body = tag.group(2).strip()
        bodies.append(body)
        if not explicit_other_language and language.casefold() != 'japanese':
            errors.append('All speech must use the [Japanese] language tag unless the user explicitly requests another language.')

    for line in supplied:
        if line not in main:
            errors.append(f'The supplied dialogue is missing from the scene: {line}')

    return list(dict.fromkeys(errors))


def long_timeline():
    name = '_h3_standard_timeline_contract'
    if name not in sys.modules:
        path = Path(folder_paths.base_path)/'custom_nodes/ComfyUI-MiniMax-H3-Long-Video/minimax_h3_long_video/timeline.py'
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def system_prompt(mode, duration):
    ref = 'Ref2' in mode
    fields = ('subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music'
              if ref else 'integrated_multimodal_description, overall_soundscape, non_diegetic_music')
    result = f'''Convert the user's brief into an ordinary MiniMax H3 audiovisual prompt for {mode}.
The complete output video is {duration:g} seconds, not a repeated series of short videos.
Use these exact English field names in this order: {fields}.
Each field must be followed by a colon, then its value. Use one field per paragraph.
Write descriptive prose in English. Preserve supplied dialogue, lyrics and visible text verbatim in their original language.
Preserve requested subjects, actions, order, timing, camera, style and sound. Do not add contradictory instructions.
Preserve requested music, silence, fades and speech. Do not enforce a music ban, mandatory closed mouths, 5-second windows, one utterance per window, or dialogue-count-derived duration.
Actual cuts use [Shot 1] with no timestamp, then [Shot N] At MM:SS.mmm, with increasing times strictly before the end.
Do not invent camera cuts for phases of a continuous shot. Describe timed actions inside that continuous shot.
Use stable (S1) speaker IDs and <d>[Language] exact spoken words</d>. Create dialogue only when the user requests speech but supplies no words.
If any speech, narration, singing, or invented vocal line is present and the user did not explicitly request another language, the spoken words must be natural Japanese and use <d>[Japanese] exact spoken words</d>.
Never write spoken words in ordinary quotation marks. Every utterance must have a stable speaker ID in the same local description, and spoken words must not be repeated in overall_soundscape.
Use <scenetrans> and explicit continuous audio for a spoken line crossing an actual cut; never repeat the line.
overall_soundscape describes physical sounds and ambience. non_diegetic_music describes only audience-only music, or N/A if none.
Return only the complete H3 prompt, without commentary or Markdown fences.
'''
    result += '\nRequired output skeleton (replace every placeholder with the actual content):\n'+ '\n\n'.join(field.strip()+': ...' for field in fields.split(','))+'\n'
    if ref:
        result += '''Reference images provide only the roles requested by the user; they are not automatically starting frames.
Define reusable visible content as <Subject N> tied to <Picture N>; retain those identities across the six fields. (Sx) is a voice ID, not an extra person.
subject_definitions: give each separately tracked subject its own line, describe its source-supported features and cite its source <Picture N>. One image can define multiple subjects, and multiple images can define one subject; do not assume a one-to-one mapping.
summary: summarize the target video and how the reference assets are used.
retention_analysis: for each referenced content label, state its appearances and the appropriate fixed relationship marker fully_preserved, partially_preserved, attribute_transfer, or weak_reference, then explain which requested attributes are retained or changed. This is reference fidelity analysis, NEVER viewer engagement or audience retention.
detailed_description: insert the defined <Subject N> labels at their first appearance and in later shots so the source bindings remain explicit. Begin [Shot 1] without a timestamp.
Address every connected <Picture N> according to the user's requested role. Images used only for identity are cited inside subject definitions, not forced into the target video's first frame.
'''
    elif 'I2' in mode:
        result += 'Picture 1 is the actual first frame, not just an identity reference. Inside integrated_multimodal_description, use: [Shot 1] For the target video, at 0.00 seconds into the target video, <Picture 1> is fully referenced. Put each required field on its own line.\n'
    if duration > 15:
        result += 'Because this is a long video, provide increasing [Shot N] At MM:SS.mmm markers to place events throughout the entire duration. For a continuous shot, explicitly say the same uninterrupted shot continues, not that the camera cuts.\n'
    return result


class H3StandardPrompt:
    @classmethod
    def INPUT_TYPES(cls):
        schema = json.loads(Path(__file__).with_name('input_schema.json').read_text(encoding='utf-8'))
        schema['required']['mode'] = (['T2VA','I2VA','Ref2VA (R2V)'],)
        schema['required']['duration_seconds'][1].update(default=15.0, min=0.1, max=600.0,
            display_name='秒数指定がない場合の長さ（秒）')
        schema['optional']['extra_rules'][1].update(
            default=DEFAULT_EXTRA_RULES,
            display_name='LM Studioの基本ルール（通常はこのまま）',
        )
        schema['optional']['context_frames'] = (['22','39'],{'default':'39'})
        return {section: {name: tuple(value) for name, value in fields.items()} for section, fields in schema.items()}

    RETURN_TYPES = ('STRING','STRING','INT','INT','MINIMAX_H3_LONG_PROMPT_PLAN')
    RETURN_NAMES = ('H3_PROMPT','実行計画','総フレーム数','内部区間フレーム数','区間プロンプト計画')
    FUNCTION = 'convert'
    CATEGORY = 'MiniMax H3 Video/Local Prompt'

    def convert(self, japanese_instruction, mode, duration_seconds, model, api_base,
                temperature, max_tokens, timeout_seconds, fallback_to_japanese,
                extra_rules='', source_h3_prompt='', external_h3_prompt='',
                reference_image_1=None, reference_image_2=None, reference_image_3=None,
                reference_image_4=None, reference_image_5=None, context_frames='39'):
        brief = (japanese_instruction or '').strip()
        source = external_h3_prompt if (external_h3_prompt or '').strip() else source_h3_prompt
        if not brief and not (source or '').strip():
            raise ValueError('日本語指示またはH3プロンプトを入力してください。')
        # The two fields are alternate input routes. A stale pasted example
        # must not become an implicit draft or override a new Japanese brief.
        duration, origin = timing(brief if brief else source, duration_seconds)
        prompt = source
        status = '直接入力：LM Studio・外部API呼出しなし'
        if brief:
            helper = LocalLMHelper()
            helper.SYSTEM_PROMPT = system_prompt(mode, duration)
            images = [x for x in (reference_image_1,reference_image_2,reference_image_3,reference_image_4,reference_image_5) if x is not None]
            instruction = brief
            validation_brief = brief
            if (
                _SPEECH_REQUEST_RE.search(brief)
                and not _NO_SPEECH_RE.search(brief)
                and not _supplied_dialogue_lines(brief)
                and not _OTHER_LANGUAGE_RE.search(brief)
            ):
                created_lines = create_japanese_dialogue(brief, model, api_base, timeout_seconds)
                exact_lines = '\n'.join(f'「{line}」' for line in created_lines)
                instruction += (
                    '\n\nAUTHORITATIVE_JAPANESE_DIALOGUE:\n'
                    + exact_lines
                    + '\nUse every line above verbatim exactly once in <d>[Japanese] ...</d>. '
                    'Do not repeat the words in overall_soundscape or non_diegetic_music.'
                )
                validation_brief += '\n\n' + exact_lines
            prompt, status = helper.convert(instruction,model,api_base,temperature,max_tokens,
                timeout_seconds,False,extra_rules,input_images=images,reasoning='off')
            expected = ['subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music'] if 'Ref2' in mode else ['integrated_multimodal_description','overall_soundscape','non_diegetic_music']
            for attempt in range(1):
                prompt, valid = normalize_lm_fields(prompt, expected)
                if 'Ref2' in mode:
                    prompt = normalize_reference_labels(prompt)
                ref_errors = reference_format_errors(prompt,len(images)) if 'Ref2' in mode else []
                prompt = enforce_dialogue_locality(prompt, validation_brief)
                dialogue_errors = dialogue_format_errors(prompt, validation_brief)
                errors = ref_errors + dialogue_errors
                if valid and not errors:
                    break
                status += ' / 形式警告（停止せず続行）: ' + '; '.join(errors or ['セクション表記の揺れ'])
                break
            prompt = enforce_music_policy(prompt, validation_brief)
            prompt = enforce_no_unscripted_speech(prompt, validation_brief)
        tl = long_timeline()
        count_frames = max(1, round(duration*24))
        length = tl._h3_grid_frames(count_frames)
        max_raw = 362  # Native 15-second pass; padding/guide frames are internal.
        context = int(context_frames)
        segments = tl.plan_segments(length, context, False, max_raw, exact_output_frames=count_frames)
        plan = {
            'schema':tl.PROMPT_PLAN_SCHEMA_VERSION,'length_input':length,
            'delivered_length':count_frames,'requested_output_frames':count_frames,
            'preserve_generated_audio':True,
            'max_raw_frames':max_raw,'context_frames':context,'has_initial_latent':False,
            'segments':[{**tl._segment_record(s),'prompt':segment_prompt(prompt,duration,s,len(segments))} for s in segments],
        }
        report = {'route':status,'mode':mode,'duration_source':origin,'duration_seconds':count_frames/24,
                  'segment_count':len(segments),'dialogue_count_changes_duration':False,
                  'warnings':dialogue_format_warnings(prompt),
                  'windows':[[s.output_start/24,(s.output_start+s.output_frames)/24] for s in segments]}
        return prompt,json.dumps(report,ensure_ascii=False,indent=2),length,max_raw,plan


NODE_CLASS_MAPPINGS = {'H3StandardPrompt':H3StandardPrompt}
NODE_DISPLAY_NAME_MAPPINGS = {'H3StandardPrompt':'通常H3プロンプト：直接入力／日本語LM変換・指定尺'}
