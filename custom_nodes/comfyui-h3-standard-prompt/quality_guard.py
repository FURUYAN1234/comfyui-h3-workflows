"""Checks for unsafe-to-render prompt conversions, not audiovisual quality scoring."""
import re
import unicodedata
from difflib import SequenceMatcher

JP = re.compile(r'[ぁ-んァ-ヶ一-龯]')
TAG = re.compile(r'<d>\s*(?:\[[^\]]+\]\s*)?(.*?)</d>', re.I | re.S)
QUOTED_SPEECH = re.compile(r'\b(?:says?|shouts?|whispers?|sings?|utters?)\b[^\n.!?]{0,100}["“]([^"”]+)["”]', re.I)
VOCAL_REQUEST = re.compile(r'悲鳴|叫び|叫ぶ|叫ん|泣き声|笑い声|息遣い|息づかい|うめき|呻き')

def repetition_requested(text):
    text=re.sub(r'繰り返さ(?:ない|ず)|繰り返し(?:なし|無し)|(?:do not|never|don.t|no)\s+repeat\w*', '', text, flags=re.I)
    return bool(re.search(r'繰り返|繰返|何度|回(?:言|話|叫|発声)|\brepeat\b',text,re.I))

def norm(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC',text))

def conversion_errors(prompt, brief, supplied, duration):
    errors = []
    sound = re.search(r'overall_soundscape\s*:(.*?)(?=non_diegetic_music\s*:|$)',prompt,re.I|re.S)
    if sound and (JP.search(sound.group(1)) or TAG.search(sound.group(1))):
        errors.append('Soundscape contains spoken words or written cries. Move actual dialogue into the visual timeline using <d> tags; describe nonverbal screams once in English without phonetic text.')
    main = re.search(r'(?:detailed_description|integrated_multimodal_description)\s*:(.*?)(?=overall_soundscape\s*:|$)',prompt,re.I|re.S)
    if main:
        clocks = [int(a)*60+float(b) for a,b in re.findall(r'(\d{1,2}):(\d{2}(?:\.\d+)?)',main.group(1))]
        clocks += [float(x) for x in re.findall(r'(?i)\b(?:At|By|From|to)\s+(\d+(?:\.\d+)?)\s*(?:s|seconds?)\b',main.group(1))]
        if clocks and max(clocks) < float(duration)*0.8:
            errors.append('The visual timeline stops too early. Describe all actions through the final seconds and ending of the requested duration, not only the opening. Summary alone does not count.')
    bodies = TAG.findall(prompt) + QUOTED_SPEECH.findall(prompt)
    exact = {norm(x) for x in supplied}
    directions = brief
    for line in supplied:
        directions = directions.replace(line, '')
    directions = norm(directions)
    for body in bodies:
        value = norm(body)
        if value in exact:
            continue  # Explicit user dialogue can intentionally mention filmmaking.
        if re.search(r'camera (?:zooms?|pans?|moves?|angles?)|(?:make|create|generate) (?:a |the )?(?:video|shot)|reference image|カメラワーク|動画の長さ|秒の動画|添付.{0,6}画像', body, re.I):
            errors.append('Dialogue contains production/camera/reference commands instead of character speech. Rewrite these as visual directions, not spoken words.')
        match = SequenceMatcher(None, value, directions, autojunk=False).find_longest_match()
        if match.size >= 16 and match.size >= len(value)*0.45:
            errors.append('Production directions were copied into speech. Only actual character words belong inside dialogue; describe actions and camera in English prose.')
    prose = TAG.sub('', prompt)
    prose = QUOTED_SPEECH.sub('', prose)
    # Original-language visible lettering is legitimate, but only when requested.
    if re.search(r'画面文字|字幕|テロップ|看板|表示|書かれ|書いて|文字',brief):
        prose = re.sub(r'「[^」]*」|『[^』]*』|["“][^"”]*["”]', '', prose)
    if len(JP.findall(prose)) >= 20:
        errors.append('Untranslated Japanese production prose remains outside speech/visible text. Translate visual instructions into English; never forward the original brief.')
    compact = norm(prompt)
    if len(norm(brief)) >= 20 and norm(brief) in compact:
        errors.append('The original production brief was returned instead of an H3 rewrite.')
    invented = [b for b in bodies if norm(b) not in exact]
    # Conservative character heuristic, not a measured speaking-speed guarantee.
    if invented and sum(len(JP.findall(b)) for b in bodies) > max(40, float(duration)*7):
        errors.append('Invented dialogue is too long for the duration. Shorten only invented speech and leave pauses for action; preserve user-supplied lines.')
    if len(bodies) != len(set(map(norm,bodies))) and not repetition_requested(brief):
        errors.append('A spoken line is duplicated. Include each line once unless repetition was requested.')
    return list(dict.fromkeys(errors))
