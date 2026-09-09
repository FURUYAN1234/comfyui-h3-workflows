import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import unicodedata

import folder_paths
from .lm_client import LocalLMHelper
from .standard import timing, timeline, segment_prompt, normalize_lm_fields, reference_format_errors, boundary_errors
from .quality_guard import conversion_errors, VOCAL_REQUEST


# Validated per-pass frame budget; independent of story, cast and source images.
SINGLE_PASS_LIMIT_SECONDS = 20.0

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
    # Missing language markup is a repairable form difference, not bad dialogue.
    if not _OTHER_LANGUAGE_RE.search(brief or ''):
        prompt = re.sub(r'<d>(.*?)</d>', lambda m: '<d>'+ (m.group(1).strip() if m.group(1).strip().startswith('[') else '[Japanese] '+m.group(1).strip())+'</d>', prompt, flags=re.I|re.S)
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
    if (_SPEECH_REQUEST_RE.search(brief or '') or VOCAL_REQUEST.search(brief or '')) and not _NO_SPEECH_RE.search(brief or ''):
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
    positive_main = re.sub(r'\b(?:no|without)\b[^.\n]*', '', main, flags=re.I)
    claims_speech = bool(_SPEECH_CLAIM_RE.search(positive_main))

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


def system_prompt(mode, duration, boundaries=()):
    fields = 'subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music' if 'Ref2' in mode else 'integrated_multimodal_description, overall_soundscape, non_diegetic_music'
    boundary_rule = ('Generation boundaries: '+', '.join(f'{b:g} seconds' for b in boundaries)+'. No action time range may cross a boundary. At each boundary keep the current camera framing and subject positions; the next range advances the already-reached state, without restaging the action onset. Describe each side separately. ') if boundaries else ''
    return f'''{boundary_rule}Rewrite the brief as a MiniMax H3 {mode} video lasting {duration:g} seconds. Return only JSON with these string keys: {fields}. The JSON schema enforces field limits. Aim for 350-450 words TOTAL. End immediately after the JSON object.
All visual prose is English. Translate the production instructions into visible actions in their original order; NEVER read them aloud or discuss rendering, software, or the rewrite process. Only actual dialogue/lyrics and requested visible lettering retain their original language. Exact user-supplied words must remain unchanged. Default voice language is Japanese. Screams, breaths and gasps are short NONVERBAL sounds, not narration. Describe them in English as Japanese female screams, without Japanese phonetic spellings or invented dialogue.
The main description MUST contain the complete beginning, middle, and ending, spanning the entire requested duration. Never put later actions only in the summary. The main description uses explicit [MM:SS-MM:SS] ranges for successive phases. Cover 00:00 through the requested final second, putting the requested ending in the final range. Keep the action progressing throughout; do not finish the story early and pad the rest with black screen. These ranges are mandatory even for one continuous shot. They are timing phases, not cuts. Maintain continuous action and camera movement unless the user requests cuts; never repeat an establishing view or reset the actors between phases. Keep all requested actions, characters, camera movements and sound. Preserve causal order: an initiating event must happen before its consequences, never reappear in the final phase. Allocate the final phase solely to completing the requested ending state. At each boundary explicitly state the already-reached position and ongoing movement, not a fresh start. Do not add people, narration, captions or music without a request. Speech uses (S1) <d>[Japanese] actual words</d> once with natural pauses. No speech text in soundscape or music.
For references: subject_definitions contains ONLY stable appearance, never the starting location, pose or action. The detailed description owns all changing locations and poses. Soundscape contains only continuous ambience; put one-off growls, attacks, screams and thunder onsets in their timed action ranges. For references: subject_definitions gives separate <Subject 1>, <Subject 2> etc with appearance and correct <Picture N> source. One image may contain multiple people. Retention states fully_preserved/partially_preserved/attribute_transfer/weak_reference relationships. Use the same Subject labels in the action timeline. Ref2VA images define identity, not compulsory first frames. In I2VA, state <Picture 1> is the first frame at 0.00 seconds.
Soundscape contains environmental and nonverbal sounds. Music is N/A unless requested. Preserve deliberate silence and requested BGM.
'''


def split_conversion_issues(errors, prompt, image_count):
    """Tolerate mixed-language prose and incomplete metadata; never discard content."""
    warnings, blocking = [], []
    used = {int(n) for n in re.findall(r'<Picture\s+(\d+)>', prompt, re.I)}
    for error in errors:
        minor = error.startswith((
            'The user requests speech. Put every spoken line',
            'Split the action phase at ',
            'Use explicit time ranges with generation boundaries:',
            'Soundscape contains spoken words or written cries.',
            'Untranslated Japanese production prose remains',
            'retention_analysis must state reference preservation relationships',
        ))
        if error.startswith('Bind the connected image sources '):
            # Missing labels are metadata omissions; nonexistent image IDs are not.
            minor = used.issubset(set(range(1, image_count + 1)))
        (warnings if minor else blocking).append(error)
    return blocking, warnings

class H3StandardPrompt:
    @classmethod
    def INPUT_TYPES(cls):
        schema = json.loads(Path(__file__).with_name('input_schema.json').read_text(encoding='utf-8'))
        schema['required']['fallback_to_japanese'][1].update(default=False, display_name='旧・原文続行（安全のため無効）', tooltip='互換性のため残していますが、ONでも未変換原文は動画へ渡しません。内容不備は最大2回修正依頼し、未解決なら停止します。')
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
        tl = long_timeline()
        # Up to 20 seconds can be one continuous H3 pass; avoid an artificial seam at 15s.
        max_raw = tl._h3_grid_frames(max(1, round(duration*24))) if 15 < duration <= SINGLE_PASS_LIMIT_SECONDS else 362
        planned = tl.plan_segments(tl._h3_grid_frames(max(1, round(duration*24))), int(context_frames), False, max_raw, exact_output_frames=max(1, round(duration*24)))
        boundaries = [s.output_start/24 for s in planned[1:]]
        prompt = source
        tolerated_warnings = []
        status = '直接入力：LM Studio・外部API呼出しなし'
        if brief:
            helper = LocalLMHelper()
            helper.SYSTEM_PROMPT = system_prompt(mode, duration, boundaries)
            helper.STREAM_RESPONSE = True
            helper.H3_SAMPLING = True
            images = [x for x in (reference_image_1,reference_image_2,reference_image_3,reference_image_4,reference_image_5) if x is not None]
            expected = ['subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music'] if 'Ref2' in mode else ['integrated_multimodal_description','overall_soundscape','non_diegetic_music']
            helper.H3_FIELDS = expected
            extra_rules = extra_rules.replace(DEFAULT_EXTRA_RULES, '').strip()
            instruction = brief
            for attempt in range(3):
                print(f'[H3] LLM conversion attempt {attempt + 1}/3; unconverted fallback disabled.')
                # Always false, including old workflows that still serialize true.
                prompt, status = helper.convert(instruction,model,api_base,temperature,max_tokens,
                    timeout_seconds,False,extra_rules,input_images=images,reasoning='off')
                prompt, valid = normalize_lm_fields(prompt, expected)
                if 'Ref2' in mode:
                    prompt = normalize_reference_labels(prompt)
                prompt = enforce_dialogue_locality(prompt, brief)
                errors = [] if valid else ['Return the complete ordered H3 fields with nonempty English visual descriptions.']
                errors += conversion_errors(prompt, brief, _supplied_dialogue_lines(brief), duration)
                errors += reference_format_errors(prompt,len(images)) if 'Ref2' in mode else []
                errors += dialogue_format_errors(prompt, brief)
                errors += boundary_errors(prompt, duration, boundaries)
                errors, tolerated_warnings = split_conversion_issues(errors, prompt, len(images))
                if not errors:
                    status += f' / blocking checks passed (attempt {attempt + 1}/3)'
                    if tolerated_warnings:
                        print('[H3] Continuing with nonblocking conversion warnings: ' + '; '.join(tolerated_warnings))
                    break
                reasons = '; '.join(dict.fromkeys(errors))
                print(f'[H3] Rejected conversion {attempt + 1}/3: {reasons}')
                if attempt == 2:
                    raise RuntimeError('H3変換が内容検査に3回不合格となりました。原文は動画へ渡しません。理由: ' + reasons)
                instruction = (brief + '\n\nREWRITE_CORRECTION: Generate a fresh conversion. '
                    'Rewrite the ORIGINAL brief above, preserving its actions, references, duration and exact supplied dialogue. '
                    'Fix these errors: ' + reasons)
            prompt = enforce_music_policy(prompt, brief)
            prompt = enforce_no_unscripted_speech(prompt, brief)
        tl = long_timeline()
        count_frames = max(1, round(duration*24))
        length = tl._h3_grid_frames(count_frames)
        # max_raw was selected before conversion so planning and validation agree.
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
                  'warnings':dialogue_format_warnings(prompt) + tolerated_warnings,
                  'windows':[[s.output_start/24,(s.output_start+s.output_frames)/24] for s in segments]}
        return prompt,json.dumps(report,ensure_ascii=False,indent=2),length,max_raw,plan


NODE_CLASS_MAPPINGS = {'H3StandardPrompt':H3StandardPrompt}
NODE_DISPLAY_NAME_MAPPINGS = {'H3StandardPrompt':'通常H3プロンプト：直接入力／日本語LM変換・指定尺'}
