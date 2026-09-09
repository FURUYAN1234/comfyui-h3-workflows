import re
from dataclasses import dataclass


FPS = 24
PROMPT_PLAN_SCHEMA_VERSION = 6


@dataclass(frozen=True)
class Segment:
    index: int
    raw_frames: int
    context_frames: int
    output_start: int
    output_frames: int

    @property
    def prompt_start_seconds(self):
        return self.output_start / FPS

    @property
    def prompt_end_seconds(self):
        return (self.output_start + self.output_frames) / FPS


def _h3_grid_frames(minimum_frames):
    frames = max(5, int(minimum_frames))
    return frames + ((5 - frames) % 17)


def _segment_output_frames(max_raw_frames):
    """Reverse the common whole-second input before its 17k+5 padding."""
    nearest_seconds = round(max_raw_frames / FPS)
    for seconds in (nearest_seconds, nearest_seconds - 1, nearest_seconds + 1):
        whole_seconds_frames = seconds * FPS
        if seconds > 0 and _h3_grid_frames(whole_seconds_frames) == max_raw_frames:
            return whole_seconds_frames
    return max_raw_frames


def dialogue_duration_frames(dialogue_turns, max_raw_frames):
    """Return one complete generation window per tagged dialogue turn."""
    return max(1, int(dialogue_turns)) * _segment_output_frames(max_raw_frames)


def plan_segments(output_frames, context_frames, has_initial_latent, max_raw_frames,
                  exact_output_frames=None):
    if output_frames < 1:
        raise ValueError("output_frames must be positive")
    if context_frames not in (22, 39):
        raise ValueError("context_frames must be 22 or 39")
    if max_raw_frames < 5 or max_raw_frames % 17 != 5:
        raise ValueError("max_raw_frames must use the MiniMax H3 17k+5 frame grid")

    # max_raw_frames selects a human-scale timeline window. For example, 362
    # frames means a 15-second master window, not a 15.083-second prompt cut.
    # The removable AV guide and H3 grid padding are internal generation detail.
    segment_frames = _segment_output_frames(max_raw_frames)

    segments = []
    remaining = (_segment_output_frames(int(output_frames)) if exact_output_frames is None
                 else int(exact_output_frames))
    if remaining < 1 or remaining > output_frames:
        raise ValueError("Exact output frame count must be positive and fit the padded input")
    output_start = 0
    index = 0
    while remaining:
        context = context_frames if has_initial_latent or index else 0
        delivered = min(remaining, segment_frames)
        raw_frames = _h3_grid_frames(context + delivered)
        segments.append(Segment(index, raw_frames, context, output_start, delivered))
        remaining -= delivered
        output_start += delivered
        index += 1
    return segments


_TIMESTAMP = re.compile(
    r"(?<!\d)(?:(\d{1,2}):)?(\d{2}):(\d{2})(?:\.(\d{1,3}))?(?!\d)"
)
_SHOT = re.compile(
    r"\[Shot\s+(\d+)\]"
    r"(?:\s+(?:At\s+)?((?:(?:\d{1,2}:)?\d{2}:\d{2}(?:\.\d{1,3})?))"
    r"(?:\s*[-\u2013\u2014]\s*((?:(?:\d{1,2}:)?\d{2}:\d{2}(?:\.\d{1,3})?)))?"
    r")?\s*,?",
    re.IGNORECASE,
)
_INTEGRATED = re.compile(
    r"integrated_multimodal_description\s*:\s*(.*?)(?=\n\s*overall_soundscape\s*:|\n\s*non_diegetic_music\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_DETAILED = re.compile(
    r"detailed_description\s*:\s*(.*?)(?=\n\s*overall_soundscape\s*:|\n\s*non_diegetic_music\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_SOUNDSCAPE = re.compile(
    r"overall_soundscape\s*:\s*(.*?)(?=\n\s*non_diegetic_music\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_MUSIC = re.compile(r"non_diegetic_music\s*:\s*(.*)\Z", re.IGNORECASE | re.DOTALL)
_GLOBAL_INSTRUCTIONS = re.compile(
    r"(?mi)^[ \t]*\[Global Instructions\][ \t]*$",
)
_SUMMARY = re.compile(
    r"(?ims)^([ \t]*summary\s*:\s*)(.*?)(?=^[ \t]*retention_analysis\s*:)",
)
_KEYFRAME_ALIGNMENT = re.compile(
    r"(?mi)^(?:For the target video, at 0\.00 seconds into the target video,.*fully referenced\.|"
    r"How the reference pictures align with the target video.*)$\s*",
)
_OUTER_CODE_FENCE = re.compile(
    r"\A\s*```(?:text|txt)?[ \t]*\r?\n(.*?)\r?\n```\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
_BARE_S_DEFINITION = re.compile(r"(?mi)^[ \t]*(S([1-9]\d*))[ \t]*(?:=|:)")
_SUBJECT_DEFINITIONS_BLOCK = re.compile(
    r"(?ims)^([ \t]*subject_definitions\s*:\s*\n)(.*?)(?=^[ \t]*summary\s*:)",
)
_BARE_S_SPEECH = re.compile(
    r"(?i)(?<![\w(<])S([1-9]\d*)([ \t]*(?:\([^\n)]*\))?[ \t]*:[ \t]*<d>)",
)
_BARE_S_TOKEN = re.compile(r"(?<![\w(<])S([1-9]\d*)(?![\w>])")
_DIALOGUE_TAG = re.compile(
    r"<d>\s*(?:\[[^\]]+\]\s*)?.+?\s*</d>",
    re.IGNORECASE | re.DOTALL,
)
_RAW_DIALOGUE_TAG = re.compile(
    r"<d>\s*(.*?)\s*</d>",
    re.IGNORECASE | re.DOTALL,
)
_SPEECH_CUE = re.compile(
    r"\b(?:S\d+|Subject\s+\d+|<Subject\s+\d+>)\b[^.!?\n]{0,240}"
    r"\b(?:says?|asks?|replies?|answers?|exclaims?|whispers?|shouts?|"
    r"speaks?|talks?|chats?|converses?)\b",
    re.IGNORECASE,
)
_MOUTH_MOVEMENT_CUE = re.compile(
    r"(?i)(?:S\d+|Subject\s+\d+|<Subject\s+\d+>|\b(?:her|his|their))[^.!?\n]{0,320}"
    r"(?:\b(?:moves?|moving|opens?|open|opening)\b[^.!?\n]{0,40}\b(?:mouth|lips?)\b"
    r"|\b(?:mouth|lips?)\b[^.!?\n]{0,40}\b(?:moves?|moving|opens?|open|opening)\b)"
)
_REPLAY_PRONE_CAMERA_CUE = re.compile(
    r"(?i)\b(?:camera|cut\s+to)\b[^.!?\n]{0,320}"
    r"\b(?:tracks?|arcs?|doll(?:y|ies)|pans?|cranes?|zooms?|orbits?|"
    r"moves?|travels?|starts?|starting|begins?|reveals?|settles?)\b"
)
_LEGACY_PRESENTATION_LOCK = re.compile(
    r"\s*REFERENCE ASSET PRESENTATION LOCK:.*?"
    r"A picture assigned to a later shot must not appear at the opening\.",
    re.IGNORECASE | re.DOTALL,
)
_SUBJECT_DEFINITION = re.compile(
    r"<Subject\s+([1-9]\d*)>(?:\s*\(S\d+\))?\s+is\b",
    re.IGNORECASE,
)


def _normalize_visual_conditioning_text(value):
    """Remove legacy negated visual concepts from H3 positive conditioning."""
    normalized = _LEGACY_PRESENTATION_LOCK.sub("", value or "")
    # Older LM output occasionally emitted a malformed pseudo-label before the
    # environment sentence. It is prose, not a reference label.
    normalized = re.sub(
        r"(?i)<\s+The\s+environment\b",
        "The environment",
        normalized,
    )
    return re.sub(r"[ \t]+\n", "\n", normalized).strip()


def normalize_dialogue_language_tags(value, default_language="Japanese"):
    """Canonicalize provider dialogue tags so H3 always receives a language.

    Some prompt providers emit ``<d>台詞</d>`` even though MiniMax H3 expects
    ``<d>[Japanese] 台詞</d>``.  Older code also failed to count the untyped
    form, causing automatic duration to build silent segments.  Preserve an
    explicit language when present and add Japanese only when it is absent.
    """
    def replace(match):
        body = match.group(1).strip()
        if not body or re.match(r"^\[[^\]]+\]", body):
            return match.group(0)
        return "<d>[{}] {}</d>".format(default_language, body)

    return _RAW_DIALOGUE_TAG.sub(replace, value or "")


def _canonicalize_bare_s_subjects(value):
    """Repair a complete, sequential ``S1: ...`` Ref2VA definition block."""
    prompt = value or ""
    block = _SUBJECT_DEFINITIONS_BLOCK.search(prompt)
    if block is None:
        return prompt

    body = block.group(2)
    matches = list(_BARE_S_DEFINITION.finditer(body))
    if not matches or _SUBJECT_DEFINITION.search(body):
        return prompt

    ids = [int(match.group(2)) for match in matches]
    if ids != list(range(1, len(ids) + 1)):
        return prompt

    def replace_definition(match):
        subject_id = int(match.group(2))
        return "<Subject {0}> (S{0}) is".format(subject_id)

    repaired_body = _BARE_S_DEFINITION.sub(replace_definition, body)
    prompt = prompt[:block.start(2)] + repaired_body + prompt[block.end(2):]

    def replace_speech(match):
        subject_id = int(match.group(1))
        return "<Subject {0}> (S{0}){1}".format(subject_id, match.group(2))

    prompt = _BARE_S_SPEECH.sub(replace_speech, prompt)
    return _BARE_S_TOKEN.sub(
        lambda match: "<Subject {}>".format(int(match.group(1))),
        prompt,
    )


def _defined_subject_count(value):
    prompt = value or ""
    # Mixed legacy Sx definitions are ambiguous: they may be speaker IDs rather
    # than visible people. Do not turn that ambiguity into a fixed body count.
    if _BARE_S_DEFINITION.search(prompt):
        return None
    block = _SUBJECT_DEFINITIONS_BLOCK.search(prompt)
    if block is not None:
        definition_scope = block.group(2)
    else:
        first_shot = _SHOT.search(prompt)
        definition_scope = (
            prompt[:first_shot.start()] if first_shot is not None else prompt
        )

    ids = [int(item) for item in _SUBJECT_DEFINITION.findall(definition_scope)]
    if not ids:
        referenced = sorted({
            int(item)
            for item in re.findall(
                r"<Subject\s+([1-9]\d*)>", prompt, flags=re.I
            )
        })
        if not referenced:
            return None
        if referenced != list(range(1, referenced[-1] + 1)):
            raise ValueError(
                "H3 visual subject labels must be sequential from <Subject 1>"
            )
        return referenced[-1]
    unique = sorted(set(ids))
    if unique != list(range(1, unique[-1] + 1)):
        raise ValueError(
            "H3 visual subject definitions must be sequential from <Subject 1>"
        )
    return unique[-1]


def _local_visual_continuity_guard(subject_count):
    # Subject labels can denote people, animals, robots, props, environments,
    # and styles. Their cardinality is NEVER a human-body or on-screen count.
    # A close-up may also show fewer identities than the complete inventory.
    return (
        "REFERENCE IDENTITY CONTINUITY: Preserve each referenced entity's kind and "
        "identity. Subject labels may describe humans, animals, robots, objects or "
        "environments; neither their number nor speaker IDs specify a human count. "
        "Use the local shot's source-supported visible set, not the full inventory "
        "in every frame. A camera reframe, occlusion or re-entry reveals the same "
        "existing identity, never a second copy. Preserve each identity's own hair "
        "length, silhouette, clothing and anatomical attachment points. Keep hair, "
        "tails, ears, ribbons, garments and props attached to their original owners "
        "and body regions; do not transfer or merge these features. Background "
        "crowds and intentional look-alikes follow the source, not main-cast clones. "
        "The original reference remains authoritative when the preceding generated "
        "guide drifts. Every visible part of the frame belongs to one coherent scene."
    )


def _strip_nonlocal_dialogue(value):
    """Remove dialogue events from prose copied into every generation segment.

    Dialogue belongs only to the local Shot that schedules it. Small local models
    sometimes recap every spoken line before ``[Shot 1]`` or inside a global block;
    carrying that recap into every long-video pass makes H3 repeat the most salient
    line. Sentence-level removal also drops cues such as ``S1 speaks about...`` that
    could otherwise trigger an unscripted paraphrase after the tag itself is removed.
    """
    cleaned_lines = []
    for line in (value or "").splitlines():
        sentences = re.split(r"(?<=[.!?])(?=\s|$)", line)
        kept = []
        for sentence in sentences:
            tagged = list(_DIALOGUE_TAG.finditer(sentence))
            if tagged:
                # The speech event itself has already happened in the preceding AV
                # guide. Retain only a following visual-action clause, if present.
                sentence = sentence[tagged[-1].end():].strip()
            if (
                sentence
                and not _SPEECH_CUE.search(sentence)
                and not _MOUTH_MOVEMENT_CUE.search(sentence)
            ):
                kept.append(sentence)
        cleaned_lines.append("".join(kept).strip())
    return "\n".join(cleaned_lines).strip()


def _silent_continuation_context(value):
    """Keep identity and setting, but never replay completed speech or camera beats."""
    # Prompt writers wrap long sentences at arbitrary newlines.  Rejoin prose
    # before filtering so a cue such as ``Only <Subject 1>\n moves lips`` cannot
    # evade the silent-window guard.
    cleaned = re.sub(r"\s+", " ", _strip_nonlocal_dialogue(value)).strip()
    sentences = re.split(r"(?<=[.!?])(?=\s|$)", cleaned)
    stable = [
        sentence for sentence in sentences
        if sentence
        and not _REPLAY_PRONE_CAMERA_CUE.search(sentence)
        and not _MOUTH_MOVEMENT_CUE.search(sentence)
    ]
    context = "".join(stable).strip()
    return " ".join(filter(None, (
        context,
        "The prior action and camera move are already complete. Hold the established "
        "composition and continue only with new subtle eye, head, breathing, and hand "
        "reactions. Do not restart or repeat any earlier action. Every visible mouth "
        "and pair of lips remains fully closed; nobody speaks or vocalizes.",
    )))


def _nearest_dialogue_identity(prefix):
    """Use the last explicit identity token, never prefer a distant subject.

    A visual description can list other Subjects before the actual (Sx)
    speaking tag. Subject IDs and speaker IDs are separate namespaces.
    Preserve the chosen namespace for the model's subject definitions.
    """
    tokens = list(re.finditer(
        r"(?i)<Subject\s+([1-9]\d*)>|(?<![\w])S([1-9]\d*)\b", prefix))
    if not tokens:
        return None, None
    token = tokens[-1]
    if token.group(1):
        return int(token.group(1)), None
    return None, int(token.group(2))


def _spread_one_dialogue_per_segment(parsed, segment_count,
                                     timeline_duration_seconds):
    """Expand a dialogue-dense shot into one complete turn per long segment.

    Cloud prompt writers sometimes put several speakers into one five-second
    shot even when later long-video segments are empty. H3 then overlaps or
    truncates those turns. When the prompt contains no more turns than the
    available segments, preserve their order and deterministically assign at
    most one complete turn to each segment.  Spread those turns across the
    *whole* movie instead of packing N turns into the first N segments.  A
    four-turn, six-segment comic therefore keeps its final line for the last
    segment rather than exhausting all dialogue at 20 seconds and leaving H3
    ten seconds in which to invent speech.
    """
    if not segment_count or not timeline_duration_seconds:
        return parsed

    events = []
    dialogue_counts = []
    for _, source_start, text in parsed:
        matches = list(_DIALOGUE_TAG.finditer(text))
        dialogue_counts.append(len(matches))
        for match in matches:
            lookback = text[max(0, match.start() - 320):match.start()]
            subject_id, speaker_id = _nearest_dialogue_identity(lookback)
            events.append({
                "source_text": text,
                "dialogue": match.group(0).strip(),
                "subject_id": subject_id,
                "speaker_id": speaker_id,
                "source_start": source_start,
            })

    if not events or len(events) > int(segment_count):
        return parsed

    segment_duration = float(timeline_duration_seconds) / int(segment_count)
    dense_shot = max(dialogue_counts or [0]) > 1
    one_turn_per_shot = (
        len(events) == len(parsed)
        and all(count == 1 for count in dialogue_counts)
    )
    explicit_turn_starts = [
        event["source_start"] for event in events
        if event["source_start"] is not None
    ]
    early_packed = (
        one_turn_per_shot
        and explicit_turn_starts
        and max(explicit_turn_starts)
        <= float(timeline_duration_seconds) - 2 * segment_duration
    )
    fills_every_segment = one_turn_per_shot and len(events) == int(segment_count)
    if not dense_shot and not early_packed and not fills_every_segment:
        return parsed

    redistributed = []
    for index, event in enumerate(events):
        visual_context = _strip_nonlocal_dialogue(event["source_text"])
        visual_context = re.sub(r"(?im)^\s*Pause,?\s+then\s*:\s*$", "", visual_context).strip()
        if event["subject_id"] is not None:
            speech = (
                "<Subject {subject}> is the only moving mouth and says, {dialogue} "
                "All other visible subjects keep their lips closed."
            ).format(subject=event["subject_id"], dialogue=event["dialogue"])
        elif event["speaker_id"] is not None:
            speech = (
                "The already established visible speaker associated with (S{speaker}) "
                "is the only moving mouth and says, {dialogue} All other visible "
                "subjects keep their lips closed."
            ).format(
                speaker=event["speaker_id"],
                dialogue=event["dialogue"],
            )
        else:
            speech = (
                "The established speaking subject is the only moving mouth and says, "
                "{} All other visible subjects keep their lips closed."
            ).format(event["dialogue"])
        ending = (
            "The speaker finishes the complete sentence, closes their mouth immediately "
            "after the final punctuation, and holds a silent natural reaction."
        )
        text = " ".join(
            item for item in (visual_context, speech, ending) if item
        )
        if len(events) == 1:
            segment_index = 0
        else:
            segment_index = round(
                index * (int(segment_count) - 1) / (len(events) - 1))
        redistributed.append((
            index + 1,
            segment_index * segment_duration,
            text,
        ))
    return redistributed


def _local_dialogue_guard():
    return (
        "Only the explicitly tagged spoken line in this segment's local Shot timeline may be "
        "spoken. Do not repeat, paraphrase, or continue dialogue from the master "
        "summary, common description, global instructions, or preceding AV guide. "
        "No other speaker may interrupt, reply, overlap, whisper, or speak off-screen. "
        "Render one dry, clean, mono-centered voice without echo, reverb, doubling, "
        "chorus, phase smearing, or a mid-utterance change in timbre or fidelity. "
        "If this segment has no tagged spoken line, no human speech is audible."
    )


def has_tagged_dialogue(prompt):
    """Return whether a local H3 prompt contains an explicit dialogue event."""
    return bool(_DIALOGUE_TAG.search(prompt or ""))


def count_timeline_dialogue_turns(prompt):
    """Count canonical dialogue tags only inside the Shot timeline body.

    Prompt writers sometimes echo dialogue in summary or global sections. Those
    copies must not lengthen an automatically sized movie.
    """
    prompt = normalize_dialogue_language_tags(prompt)
    prompt = _normalize_visual_conditioning_text(_strip_outer_code_fence(prompt))
    _, field = _timeline_field(prompt)
    content = field.group(1).strip() if field is not None else prompt
    shots = list(_SHOT.finditer(content))
    if not shots:
        return len(_DIALOGUE_TAG.findall(content))
    globals_ = list(_GLOBAL_INSTRUCTIONS.finditer(content))
    body_end = globals_[0].start() if globals_ else len(content)
    body = content[shots[0].start():body_end]
    return len(_DIALOGUE_TAG.findall(body))


def _prioritize_local_dialogue(timeline, dialogue_deadline_seconds,
                               clock_offset_seconds=0.0):
    """Move one local speech event to the start of its shot with a hard deadline.

    H3 tends to perform prose in written order. If a five-second camera move is
    described before the spoken line, it can delay speech until the end and cut
    the final syllable. Place the sole real dialogue tag immediately after the
    opening shot marker; all visual motion then happens underneath the speech.
    """
    matches = list(_DIALOGUE_TAG.finditer(timeline or ""))
    if len(matches) != 1:
        return timeline
    match = matches[0]
    lookback = timeline[max(0, match.start() - 360):match.start()]
    subject_id, speaker_id = _nearest_dialogue_identity(lookback)
    if subject_id is not None:
        speaker = "<Subject {}>".format(subject_id)
    elif speaker_id is not None:
        speaker = "(S{})".format(speaker_id)
    else:
        speaker = "the established visible speaker"
    clock_offset = max(0.0, float(clock_offset_seconds))
    start = clock_offset + 0.15
    deadline = clock_offset + max(0.5, float(dialogue_deadline_seconds))
    speech_lock = (
        "SPEECH-FIRST TIMING LOCK: At packed-pass time {start}, {speaker} begins the "
        "audible first syllable immediately and completes this entire utterance by "
        "packed-pass time {deadline}: {dialogue} {speaker} is the only moving mouth; all "
        "other visible subjects keep their lips closed. The visual action and camera "
        "motion continue underneath the already-started speech and must never delay it. "
    ).format(
        speaker=speaker,
        start=format_timestamp(start),
        deadline=format_timestamp(deadline),
        dialogue=match.group(0).strip(),
    )
    without_dialogue = (timeline[:match.start()] + timeline[match.end():]).strip()
    first_marker = _SHOT.search(without_dialogue)
    if first_marker is None:
        return speech_lock + without_dialogue
    marker = "[Shot {}]".format(first_marker.group(1))
    if clock_offset:
        marker += " At {},".format(format_timestamp(clock_offset))
    return (
        without_dialogue[:first_marker.start()]
        + marker
        + " "
        + speech_lock
        + without_dialogue[first_marker.end():].lstrip()
    ).strip()


def _dialogue_timing_guard(start_seconds, end_seconds, context_seconds,
                           timeline_duration_seconds, has_local_dialogue):
    local_duration = max(0.0, end_seconds - start_seconds)
    is_final = (
        timeline_duration_seconds is not None
        and _timestamp_millis(end_seconds) >= _timestamp_millis(
            timeline_duration_seconds)
    )
    # A short reaction tail avoids a hard boundary while leaving H3 enough time
    # for a natural complete utterance. The backend also recovers the grid tail.
    tail_seconds = min(local_duration, 0.5)
    deadline = max(0.0, local_duration - tail_seconds)
    if has_local_dialogue:
        speech = (
            "The single tagged spoken line must be completed by packed-pass time {}. "
            "Use a brisk but natural Japanese delivery of about 8-10 mora per second "
            "while preserving the speaker's pitch, timbre, emotion, and intelligibility. "
            "Never split, truncate, or cut off a syllable, word, or sentence at the "
            "segment boundary. After the final spoken punctuation, close the mouth and "
            "hold a natural silent reaction."
        ).format(format_timestamp(context_seconds + deadline))
    else:
        speech = (
            "This local segment contains no tagged dialogue event, so it must remain free of human "
            "speech. Every visible mouth and pair of lips remains fully closed for the entire "
            "new output window; nobody speaks, whispers, laughs, or vocalizes."
        )
    if is_final:
        return (
            "FINAL DIALOGUE AND ENDING DEADLINE: {} The final {:.3f} seconds must be a "
            "silent settled reaction or closing image with steady low ambience and no fade; do not "
            "begin or continue speech there."
        ).format(speech, tail_seconds)
    return (
        "SEGMENT DIALOGUE DEADLINE: {} Reserve the final {:.3f} seconds as a "
        "silent reaction buffer."
    ).format(speech, tail_seconds)


def parse_timestamp(value):
    match = _TIMESTAMP.fullmatch(value.strip())
    if match is None:
        raise ValueError("invalid H3 timestamp: {}".format(value))
    hours, minutes, seconds, millis = match.groups()
    milliseconds = int((millis or "0").ljust(3, "0"))
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + milliseconds / 1000.0


def format_timestamp(seconds):
    millis = max(0, int(round(seconds * 1000.0)))
    minutes, remainder = divmod(millis, 60_000)
    secs, ms = divmod(remainder, 1000)
    return "{:02d}:{:02d}.{:03d}".format(minutes, secs, ms)


def _timestamp_millis(seconds):
    return int(round(seconds * 1000.0))


def _guide_instruction(context_seconds, active_text=""):
    instruction = (
        "PACKED-PASS CLOCK: the supplied preceding AV guide occupies packed-pass time "
        "00:00.000 through {context}. The new deliverable output begins exactly at "
        "packed-pass time {context}. Do not execute any new Shot, tagged dialogue, or "
        "sound event during the guide interval. Continue forward without repeating the "
        "guided action."
    ).format(context=format_timestamp(context_seconds))
    if active_text:
        instruction += (
            " Continue the master-timeline Shot already in progress from its current state; "
            "do not replay its beginning: {}"
        ).format(active_text)
    return instruction


def _unguided_continuation(active_text, context_seconds=0.0):
    if not context_seconds:
        return (
            "[Shot 1] Continue the master-timeline Shot already in progress from its current "
            "state; do not restart or replay its beginning: {}"
        ).format(active_text)
    return (
        "[Shot 1] At {start}, continue the master-timeline Shot already in progress from its current "
        "state; do not restart or replay its beginning: {}"
    ).format(active_text, start=format_timestamp(context_seconds))


def _segment_scope(start_seconds, master_end_seconds, context_seconds,
                   generated_end_seconds=None):
    if generated_end_seconds is None:
        generated_end_seconds = master_end_seconds
    local_duration = generated_end_seconds - start_seconds
    return (
        "Long-video segment scope: the master timeline range is {}-{}. "
        "This H3 generation pass is {:.3f} seconds long"
    ).format(
        format_timestamp(start_seconds),
        format_timestamp(master_end_seconds),
        local_duration,
    ) + (
        ". Its packed-pass clock includes the preceding AV guide before the new output window."
        if context_seconds else "."
    )


def _scope_reference_prefix(prefix, start_seconds, master_end_seconds,
                            context_seconds, generated_end_seconds):
    match = _SUMMARY.search(prefix)
    if match is None:
        return prefix
    scope = _segment_scope(
        start_seconds, master_end_seconds, context_seconds,
        generated_end_seconds)
    if start_seconds:
        scoped_summary = (
            "This is a continuation pass from the complete long-video master. {} "
            "Continue from the supplied preceding AV guide and the local Shot timeline. "
            "Do not restart the opening, recap completed Shots, or execute master-timeline "
            "beats outside this segment."
        ).format(scope)
    else:
        scoped_summary = "{}\n{}".format(match.group(2).rstrip(), scope)
    suffix = prefix[match.end(2):].lstrip()
    return prefix[:match.start(2)] + scoped_summary + "\n\n" + suffix


def _strip_outer_code_fence(prompt):
    match = _OUTER_CODE_FENCE.fullmatch(prompt)
    return match.group(1) if match is not None else prompt


def _validate_reference_labels(prompt, field_name):
    if field_name != "detailed_description":
        return
    invalid = sorted({match.group(1) for match in _BARE_S_DEFINITION.finditer(prompt)})
    # When at least one canonical visual subject exists, remaining bare Sx entries
    # are treated as speaker metadata. They must not be promoted to visible people.
    if invalid and not _SUBJECT_DEFINITION.search(prompt):
        __import__('warnings').warn(
            "Invalid Ref2VA subject label(s): {}. Define visual references as "
            "<Subject N> in subject_definitions and use (Sx) only for speakers."
            .format(", ".join(invalid))
        )


def _localize_audio(value, kind, start_seconds):
    value = value.strip()
    if re.fullmatch(
            r"(?i)(?:N/?A|none|no music)(?:\.\s*No music is audible)?\.?",
            value):
        return "N/A. No music is audible." if kind == "music" else "N/A."
    if not start_seconds:
        return value
    if kind == "soundscape":
        continuity = (
            "Continue the soundscape established by the supplied AV guide without "
            "restarting it. Follow only sound events in the local Shot timeline."
        )
    else:
        continuity = (
            "Continue the already-playing master score seamlessly from the supplied AV "
            "guide. Do not restart its intro, drop, vocals, or earlier musical phases. "
            "Follow only music cues in the local Shot timeline."
        )
    # Absolute master times become local times after segmentation and would replay the
    # opening/drop/finale in every continuation pass. Keep timeless style constraints,
    # but replace a time-bearing master audio timeline with an explicit continuation.
    if _TIMESTAMP.search(value):
        return continuity
    return "{} {}".format(continuity, value) if value else continuity


def _timeline_field(prompt):
    matches = []
    for field_name, pattern in (
            ("integrated_multimodal_description", _INTEGRATED),
            ("detailed_description", _DETAILED)):
        match = pattern.search(prompt)
        if match is not None:
            matches.append((match.start(), field_name, match))
    if not matches:
        return None, None
    _, field_name, match = min(matches, key=lambda item: item[0])
    return field_name, match


def _fallback_prompt(prompt, start_seconds, context_seconds):
    def rebase(match):
        absolute = parse_timestamp(match.group(0))
        return format_timestamp(context_seconds + absolute - start_seconds)

    rebased = _TIMESTAMP.sub(rebase, prompt)
    if context_seconds:
        return _guide_instruction(context_seconds) + "\n\n" + rebased
    return rebased


def slice_prompt(prompt, start_seconds, end_seconds, context_seconds=0.0,
                 segment_index=None, segment_count=None,
                 timeline_duration_seconds=None, preserve_input_prompt=False):
    if preserve_input_prompt and segment_count == 1 and not context_seconds:
        return prompt
    if not preserve_input_prompt:
        prompt = normalize_dialogue_language_tags(prompt)
        prompt = _normalize_visual_conditioning_text(_strip_outer_code_fence(prompt))
        prompt = _canonicalize_bare_s_subjects(prompt)
    has_dialogue = bool(_DIALOGUE_TAG.search(prompt))
    subject_count = _defined_subject_count(prompt)
    master_end_seconds = end_seconds
    if timeline_duration_seconds is not None:
        master_end_seconds = min(master_end_seconds, timeline_duration_seconds)
    field_name, field = _timeline_field(prompt)
    _validate_reference_labels(prompt, field_name)
    if field is not None:
        content = field.group(1).strip()
        prefix = prompt[:field.start()].rstrip()
        wrapped = True
    else:
        content = prompt
        prefix = ""
        wrapped = False

    all_matches = list(_SHOT.finditer(content))
    if not all_matches:
        return _fallback_prompt(prompt, start_seconds, context_seconds)
    first_shot = all_matches[0]
    global_markers = list(_GLOBAL_INSTRUCTIONS.finditer(content))
    if len(global_markers) > 1:
        __import__('warnings').warn('Multiple global instruction markers; preserving text.')
    tail = global_markers[0] if global_markers else None
    if tail is not None and tail.start() < all_matches[-1].end():
        tail = None  # Keep inline instructions in the shot text, not a fatal format error.
    body_end = tail.start() if tail is not None else len(content)
    common_intro = _strip_nonlocal_dialogue(content[:first_shot.start()].strip())
    global_tail = (
        _strip_nonlocal_dialogue(content[tail.end():].strip())
        if tail is not None else ""
    )
    body = content[first_shot.start():body_end].strip()

    matches = list(_SHOT.finditer(body))
    if not matches:
        return _fallback_prompt(prompt, start_seconds, context_seconds)

    parsed = []
    for index, match in enumerate(matches):
        timestamp = match.group(2)
        start = parse_timestamp(timestamp) if timestamp is not None else None
        text_end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        parsed.append((int(match.group(1)), start, body[match.end():text_end].strip()))

    parsed = _spread_one_dialogue_per_segment(
        parsed, segment_count, timeline_duration_seconds)

    shot_numbers = [number for number, _, _ in parsed]
    if shot_numbers != list(range(1, len(parsed) + 1)):
        parsed = [(i+1, start, text) for i, (_, start, text) in enumerate(parsed)]

    explicit = [
        (index, start)
        for index, (_, start, _) in enumerate(parsed)
        if start is not None
    ]
    if (any(current[1] <= previous[1] for previous, current in zip(explicit, explicit[1:]))
            or any(start >= (timeline_duration_seconds or master_end_seconds) for _, start in explicit)):
        __import__('warnings').warn('Ambiguous shot timing; assigning shots in written order.')
        parsed = [(number, None, text) for number, _, text in parsed]
        explicit = []

    if not explicit:
        if segment_index is None or segment_count is None:
            return _fallback_prompt(prompt, start_seconds, context_seconds)
        if len(parsed) >= segment_count:
            per_segment, extra = divmod(len(parsed), segment_count)
            first = segment_index * per_segment + min(segment_index, extra)
            count = per_segment + (segment_index < extra)
            assigned = parsed[first:first + count]
        elif len(parsed) == 1:
            assigned = parsed if segment_index == 0 else []
        else:
            assigned = [
                shot for index, shot in enumerate(parsed)
                if round(index * (segment_count - 1) / (len(parsed) - 1)) == segment_index
            ]
        duration = master_end_seconds - start_seconds
        shots = [
            (number, start_seconds + offset * duration / len(assigned), text)
            for offset, (number, _, text) in enumerate(assigned)
        ] if assigned else []
    else:
        starts = [start for _, start, _ in parsed]
        if starts[0] is None:
            starts[0] = 0.0
        anchors = [index for index, start in enumerate(starts) if start is not None]
        for left, right in zip(anchors, anchors[1:]):
            gap = right - left - 1
            if gap:
                step = (starts[right] - starts[left]) / (gap + 1)
                for offset in range(1, gap + 1):
                    starts[left + offset] = starts[left] + step * offset
        last = anchors[-1]
        if last < len(starts) - 1:
            timeline_end = timeline_duration_seconds
            if timeline_end is None:
                timeline_end = master_end_seconds
            if timeline_end <= starts[last]:
                raise ValueError("H3 timeline must end after its final timestamped Shot")
            gap = len(starts) - last - 1
            step = (timeline_end - starts[last]) / (gap + 1)
            for offset in range(1, gap + 1):
                starts[last + offset] = starts[last] + step * offset
        shots = [
            (number, start, text)
            for start, (number, _, text) in zip(starts, parsed)
        ]

    window_start_millis = _timestamp_millis(start_seconds)
    window_end_millis = _timestamp_millis(master_end_seconds)
    selected = [
        (number, shot_start, text)
        for number, shot_start, text in shots
        if window_start_millis <= _timestamp_millis(shot_start) < window_end_millis
    ]
    starts_with_new_shot = bool(
        selected and _timestamp_millis(selected[0][1]) == window_start_millis)
    active = None if starts_with_new_shot else next(
        (
            (number, shot_start, text)
            for number, shot_start, text in reversed(shots)
            if _timestamp_millis(shot_start) < window_start_millis
        ),
        None,
    )

    active_text = (
        _silent_continuation_context(active[2]) if active is not None else ""
    )
    guide_note = _guide_instruction(context_seconds) if context_seconds else ""
    if start_seconds and active_text:
        rendered = [_unguided_continuation(active_text, context_seconds)]
    else:
        rendered = []
    shot_number_offset = len(rendered)
    for index, (_, shot_start, text) in enumerate(selected):
        shot_number = index + 1 + shot_number_offset
        local_start = context_seconds + shot_start - start_seconds
        if shot_number == 1 and local_start == 0:
            marker = "[Shot 1]"
        else:
            marker = "[Shot {}] At {},".format(
                shot_number, format_timestamp(local_start))
        rendered.append("{} {}".format(marker, text).strip())
    if not rendered:
        rendered.append(
            "[Shot 1] Continue forward from the supplied preceding AV context. "
            "Do not restart or repeat any action already shown."
        )

    if start_seconds and prefix:
        prefix = _KEYFRAME_ALIGNMENT.sub("", prefix).rstrip()
    if field_name == "detailed_description" and prefix:
        prefix = _scope_reference_prefix(
            prefix, start_seconds, master_end_seconds, context_seconds,
            end_seconds)

    parts = []
    if prefix:
        parts.append(prefix)
    timeline = " ".join(rendered)
    local_duration = max(0.0, end_seconds - start_seconds)
    dialogue_deadline = max(0.0, local_duration - 0.5)
    if not preserve_input_prompt:
        timeline = _prioritize_local_dialogue(
            timeline, dialogue_deadline, context_seconds)
    if wrapped:
        if field_name == "integrated_multimodal_description":
            common_intro = "{}\n{}".format(
                _segment_scope(
                    start_seconds, master_end_seconds, context_seconds,
                    end_seconds),
                common_intro,
            ).strip()
        has_local_dialogue = bool(_DIALOGUE_TAG.search(timeline))
        dialogue_guard = _local_dialogue_guard() if has_dialogue else ""
        timing_guard = _dialogue_timing_guard(
            start_seconds,
            end_seconds,
            context_seconds,
            timeline_duration_seconds,
            has_local_dialogue,
        )
        visual_guard = _local_visual_continuity_guard(subject_count)
        if preserve_input_prompt:
            dialogue_guard = timing_guard = visual_guard = ""
        timeline_parts = [
            value for value in (
                common_intro,
                global_tail,
                visual_guard,
                guide_note,
                dialogue_guard,
                timing_guard,
                timeline,
            ) if value
        ]
        parts.append("{}: {}".format(field_name, "\n\n".join(timeline_parts)))
        soundscape = _SOUNDSCAPE.search(prompt)
        if soundscape is not None:
            parts.append(
                "overall_soundscape: "
                + (soundscape.group(1).strip() if preserve_input_prompt else
                   _localize_audio(soundscape.group(1), "soundscape", start_seconds))
            )
        music = _MUSIC.search(prompt)
        if music is not None:
            parts.append(
                "non_diegetic_music: "
                + (music.group(1).strip() if preserve_input_prompt else
                   _localize_audio(music.group(1), "music", start_seconds))
            )
    else:
        if common_intro:
            parts.append(common_intro)
        if guide_note:
            parts.append(guide_note)
        parts.append(timeline)
        if global_tail:
            parts.append(global_tail)
    return "\n\n".join(parts)


def _segment_record(segment):
    return {
        "index": segment.index,
        "raw_frames": segment.raw_frames,
        "context_frames": segment.context_frames,
        "output_start": segment.output_start,
        "output_frames": segment.output_frames,
    }


def build_prompt_plan(master_prompt, length, max_raw_frames, context_frames,
                      has_initial_latent=False, overrides=None):
    """Build the exact local prompts consumed by a long-video sampler run."""
    context_frames = int(context_frames)
    segments = plan_segments(
        length, context_frames, bool(has_initial_latent), max_raw_frames)
    delivered_length = sum(item.output_frames for item in segments)
    prompts = [
        slice_prompt(
            master_prompt,
            item.prompt_start_seconds,
            item.prompt_end_seconds,
            item.context_frames / FPS,
            item.index,
            len(segments),
            delivered_length / FPS,
        )
        for item in segments
    ]
    for name, value in (overrides or {}).items():
        if value is None or not str(value).strip():
            continue
        try:
            index = int(name.rsplit("_", 1)[-1])
        except (AttributeError, TypeError, ValueError):
            raise ValueError(
                "segment prompt override has an invalid index: {}".format(name))
        if index < 0 or index >= len(prompts):
            raise ValueError(
                "segment prompt override {} is outside the {}-segment plan"
                .format(index, len(prompts)))
        prompts[index] = str(value).strip()
    return {
        "schema": PROMPT_PLAN_SCHEMA_VERSION,
        "length_input": int(length),
        "delivered_length": delivered_length,
        "max_raw_frames": int(max_raw_frames),
        "context_frames": context_frames,
        "has_initial_latent": bool(has_initial_latent),
        "segments": [
            {**_segment_record(item), "prompt": local_prompt}
            for item, local_prompt in zip(segments, prompts)
        ],
    }


def prompt_plan_prompts(prompt_plan, segments, length, max_raw_frames,
                        context_frames, has_initial_latent):
    """Validate a prompt plan against sampler settings and return its prompts."""
    if not isinstance(prompt_plan, dict):
        raise ValueError("prompt_plan is not a MiniMax H3 Long prompt plan")
    expected = {
        "schema": PROMPT_PLAN_SCHEMA_VERSION,
        "length_input": int(length),
        "max_raw_frames": int(max_raw_frames),
        "context_frames": int(context_frames),
        "has_initial_latent": bool(has_initial_latent),
    }
    if any(prompt_plan.get(key) != value for key, value in expected.items()):
        raise ValueError(
            "prompt_plan settings do not match length, max_raw_frames, "
            "context_frames, or initial_latent")
    entries = prompt_plan.get("segments")
    if not isinstance(entries, list) or len(entries) != len(segments):
        raise ValueError("prompt_plan segment count does not match the sampler")
    prompts = []
    for segment, entry in zip(segments, entries):
        if not isinstance(entry, dict):
            raise ValueError("prompt_plan contains an invalid segment")
        expected_segment = _segment_record(segment)
        if any(entry.get(key) != value for key, value in expected_segment.items()):
            raise ValueError("prompt_plan segment layout does not match the sampler")
        local_prompt = entry.get("prompt")
        if not isinstance(local_prompt, str) or not local_prompt.strip():
            raise ValueError("prompt_plan contains an empty segment prompt")
        prompts.append(local_prompt.strip())
    return prompts
