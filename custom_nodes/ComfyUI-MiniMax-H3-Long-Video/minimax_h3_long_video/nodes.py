# SPDX-License-Identifier: GPL-3.0-only
# Portions of the MiniMax H3 reference conditioning were adapted and modified
# from ComfyUI's built-in MiniMax H3 implementation in 2026.

import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import av
import torch
from PIL import Image, ImageDraw
from safetensors import safe_open

import comfy.nested_tensor
import comfy.model_management
import comfy.utils
import folder_paths
import node_helpers
import nodes as comfy_nodes
from comfy.cli_args import args
from comfy_api.latest import ComfyExtension, InputImpl, Types, io, ui
from comfy_extras import nodes_custom_sampler as custom_sampler
from comfy_extras import nodes_minimax_h3 as h3
from comfy_extras.nodes_audio import vae_decode_audio
from typing_extensions import override

from . import local_audio_audit
from .cloud_prompt import MiniMaxH3CloudPrompt
from .dialogue_timing import (plan_confirmed_windows, validate_timing, validate_local_dialogues,
                             require_timing_connection, recovery_segments, needs_duration_repair)
from .continuity_audit import POLICY as THREE_MODE_POLICY, upstream_graph, standard_audit_settings, audit_instruction, appearance_instruction, parse_verdict

from .timeline import (
    FPS,
    PROMPT_PLAN_SCHEMA_VERSION,
    Segment,
    build_prompt_plan,
    count_timeline_dialogue_turns,
    dialogue_duration_frames,
    has_tagged_dialogue,
    plan_segments,
    apply_explicit_camera_cuts,
    prompt_plan_prompts,
    slice_prompt,
)


SCHEMA_VERSION = 28
SEGMENT_SEED_STRATEGY = "splitmix64-per-segment-v1"
AUDIO_CONTINUITY_STRATEGY = "dialogue-video-guide-silent-audio-v11"
AUDIO_SEAM_CROSSFADE_SECONDS = 0.125
CONTINUATION_GUIDE_SILENCE_SECONDS = 1.0
CONTINUATION_GUIDE_FADE_SECONDS = 0.125
AUDIO_REFINE_STRATEGY = "frozen-video-partial-audio-denoise-v1"
CAST_AUDIT_MAX_RETRIES = 5
CAST_AUDIT_MAX_TOKENS = 256
_QWEN_AUDIT_MODULE = None
UPSCALE_SCHEMA_VERSION = 1
LOOP_UPSCALE_SCHEMA_VERSION = 2

LONG_H3_PROMPT_PLAN = io.Custom("MINIMAX_H3_LONG_PROMPT_PLAN")
H3_DIALOGUE_TIMING = io.Custom("H3_DIALOGUE_TIMING")
LONG_H3_UPSCALE_JOB = io.Custom("MINIMAX_H3_LONG_UPSCALE_JOB")
LONG_H3_SEGMENT = io.Custom("MINIMAX_H3_LONG_SEGMENT")
LONG_H3_UPSCALE_PROGRESS = io.Custom("MINIMAX_H3_LONG_UPSCALE_PROGRESS")


_PATTERN = re.compile(r"%([^%]+)%")
_DATE_FIELD = re.compile(r"dd?|MM?|hh?|HH?|mm?|ss?|yyy?y?")


def _expand_date_pattern(value):
    now = time.localtime()
    fields = {
        "d": now.tm_mday,
        "M": now.tm_mon,
        "h": now.tm_hour,
        "H": now.tm_hour,
        "m": now.tm_min,
        "s": now.tm_sec,
    }

    def replace_field(match):
        token = match.group(0)
        if token == "yy":
            return str(now.tm_year)[-2:]
        if token == "yyyy":
            return str(now.tm_year).zfill(4)
        if token[0] in fields:
            return str(fields[token[0]]).zfill(len(token))
        return token

    return _DATE_FIELD.sub(replace_field, value)


def _workflow_nodes(extra_pnginfo):
    if not isinstance(extra_pnginfo, dict):
        return []
    workflow = extra_pnginfo.get("workflow")
    if not isinstance(workflow, dict):
        return []
    nodes = workflow.get("nodes")
    return nodes if isinstance(nodes, list) else []


def _node_names(node):
    names = [str(node.get("id", "")), node.get("title"), node.get("type")]
    properties = node.get("properties")
    if isinstance(properties, dict):
        names.append(properties.get("Node name for S&R"))
    return [name for name in names if isinstance(name, str) and name]


def _node_input_value(node_name, input_name, prompt, extra_pnginfo):
    nodes = _workflow_nodes(extra_pnginfo)
    matches = [node for node in nodes if node_name in _node_names(node)]
    if not matches:
        lowered = node_name.casefold()
        matches = [node for node in nodes if lowered in [name.casefold() for name in _node_names(node)]]
    if not matches:
        raise ValueError("cache_name pattern refers to unknown node {!r}".format(node_name))
    if len(matches) > 1:
        raise ValueError("cache_name pattern node {!r} is ambiguous; give the node a unique title".format(node_name))

    node_id = str(matches[0].get("id"))
    prompt_node = prompt.get(node_id) if isinstance(prompt, dict) else None
    inputs = prompt_node.get("inputs") if isinstance(prompt_node, dict) else None
    if isinstance(inputs, dict) and input_name in inputs:
        value = inputs[input_name]
    elif input_name == "value":
        widget_values = matches[0].get("widgets_values")
        value = widget_values[0] if isinstance(widget_values, list) and widget_values else None
    else:
        value = None
    if value is None:
        raise ValueError(
            "cache_name pattern %{0}.{1}% could not read that node input".format(
                node_name, input_name))
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError(
            "cache_name pattern %{0}.{1}% does not resolve to a text or number value".format(
                node_name, input_name))
    return str(value).lower() if isinstance(value, bool) else str(value)


def _expand_cache_name(cache_name, prompt, extra_pnginfo):
    def replace(match):
        pattern = match.group(1)
        if pattern.startswith("date:"):
            return _expand_date_pattern(pattern[5:])
        if "." in pattern:
            node_name, input_name = pattern.rsplit(".", 1)
            return _node_input_value(node_name, input_name, prompt, extra_pnginfo)
        return match.group(0)

    return _PATTERN.sub(replace, cache_name)


def _streams(latent):
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if samples is None or not getattr(samples, "is_nested", False):
        raise ValueError("initial_latent must be a sampled MiniMax H3 AV latent")
    parts = samples.unbind()
    if len(parts) != 2:
        raise ValueError("initial_latent must contain H3 video and audio streams")
    video, audio = parts
    if video.ndim != 5 or video.shape[1] != 24:
        raise ValueError("initial_latent has an invalid H3 video stream")
    if audio.ndim != 4 or audio.shape[1] != 32 or audio.shape[2] != 2:
        raise ValueError("initial_latent has an invalid H3 audio stream")
    if video.shape[0] != 1 or audio.shape[0] != 1:
        raise ValueError("MiniMax H3 Long Video currently supports batch size 1")
    return video, audio


def _output_paths(cache_name, resume, width, height):
    output_root = Path(folder_paths.get_output_directory()).resolve()
    resolved_name = cache_name.rstrip("/\\")
    if not resolved_name or resolved_name in (".", ".."):
        raise ValueError("cache_name must be an output-relative folder")
    unresolved_project = (output_root / resolved_name).resolve()
    unresolved_inside_output = os.path.commonpath(
        (str(output_root), str(unresolved_project))) == str(output_root)
    existed_before_resolution = (
        "%" not in resolved_name and unresolved_inside_output and unresolved_project.exists())
    full_output_folder, filename, _, subfolder, _ = folder_paths.get_save_image_path(
        resolved_name + "/master", str(output_root), width, height)
    if filename != "master":
        raise ValueError("cache_name must resolve to an output-relative folder")

    project = Path(full_output_folder).resolve()
    if "%" in os.path.relpath(project, output_root):
        raise ValueError("cache_name contains an unexpanded %...% pattern")
    existed = existed_before_resolution if project == unresolved_project else any(project.iterdir())
    if not resume and existed:
        base = project
        suffix = 2
        while project.exists():
            project = base.with_name("{}_{}".format(base.name, suffix))
            suffix += 1

    master_path = project / "master.mp4"
    relative_folder = os.path.relpath(project, output_root)

    if os.path.commonpath((str(output_root), str(project))) != str(output_root):
        raise ValueError("output path must stay inside the ComfyUI output folder")
    if os.path.commonpath((str(output_root), str(master_path))) != str(output_root):
        raise ValueError("output path must stay inside the ComfyUI output folder")
    project.mkdir(parents=True, exist_ok=True)
    (project / "latents").mkdir(exist_ok=True)
    return project, master_path, relative_folder


def _source_bundle(source_path):
    output_root = Path(folder_paths.get_output_directory()).resolve()
    candidate = Path(source_path.rstrip("/\\"))
    if not candidate.is_absolute():
        candidate = output_root / candidate
    candidate = candidate.resolve()
    if not folder_paths.is_within_directory(str(output_root), str(candidate)):
        raise ValueError("source_path must stay inside the ComfyUI output folder")
    if candidate.is_file():
        if candidate.name not in ("manifest.json", "master.mp4"):
            raise ValueError("source_path must point to a Long H3 bundle, manifest.json, or master.mp4")
        candidate = candidate.parent
    manifest_path = candidate / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("source_path does not contain a Long H3 manifest.json")
    return candidate, manifest_path


def _loop_upscale_source(project, manifest):
    output_root = Path(folder_paths.get_output_directory()).resolve()
    if not folder_paths.is_within_directory(str(output_root), str(project)):
        raise ValueError("processed Long H3 bundle path is invalid")
    source_name = manifest.get("source")
    if not isinstance(source_name, str):
        raise ValueError("processed Long H3 manifest has no source bundle path")
    source = (output_root / source_name).resolve()
    if (not folder_paths.is_within_directory(str(output_root), str(source)) or
            not (source / "manifest.json").is_file()):
        raise ValueError("processed Long H3 source bundle path is invalid")
    if (project.parent != source or
            re.fullmatch(r"upscale(?:_(?:[2-9]|\d{2,}))?", project.name) is None):
        raise ValueError("processed Long H3 bundle is not an upscale child of its source")
    return output_root, source


def _incomplete_loop_upscale_bundle(source):
    candidates = []
    for project in source.iterdir():
        match = re.fullmatch(r"upscale(?:_(?:([2-9])|(\d{2,})))?", project.name)
        if project.is_dir() and match is not None:
            index = int(match.group(1) or match.group(2) or 1)
            candidates.append((index, project.resolve()))
    for _, project in sorted(candidates, reverse=True):
        manifest_path = project / "manifest.json"
        if not manifest_path.is_file():
            continue
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        if (manifest.get("schema") == LOOP_UPSCALE_SCHEMA_VERSION and
                manifest.get("kind") == "minimax_h3_long_ultimate_upscale" and
                manifest.get("status") in ("processing", "decoding")):
            _loop_upscale_source(project, manifest)
            return project, manifest
    raise ValueError("no incomplete Long H3 upscale bundle was found under the source bundle")


def _manifest_segments(source, manifest):
    if manifest.get("status") != "complete":
        raise ValueError("source Long H3 bundle is not complete")
    entries = manifest.get("segments")
    if not isinstance(entries, list) or not entries:
        raise ValueError("source Long H3 manifest has no segments")

    segments = []
    checkpoints = []
    expected_index = 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("index") != expected_index:
            raise ValueError("source Long H3 manifest has invalid segment ordering")
        filename = entry.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("source Long H3 manifest has an invalid segment filename")
        checkpoint = (source / "latents" / filename).resolve()
        if checkpoint.parent != (source / "latents").resolve() or not checkpoint.is_file():
            raise ValueError("source Long H3 checkpoint is missing: {}".format(filename))
        raw_frames = entry.get("raw_frames")
        context_frames = entry.get("context_frames")
        output_start = entry.get("output_start")
        output_frames = entry.get("output_frames")
        if output_start is None or output_frames is None:
            timeline_start = entry.get("timeline_start")
            timeline_end = entry.get("timeline_end")
            if not all(isinstance(value, (int, float)) for value in (
                    timeline_start, timeline_end)):
                raise ValueError("source Long H3 manifest has invalid segment timing")
            output_start = round(timeline_start * FPS)
            output_frames = round((timeline_end - timeline_start) * FPS)
        if not all(isinstance(value, (int, float)) for value in (
                output_start, output_frames, raw_frames, context_frames)):
            raise ValueError("source Long H3 manifest has invalid segment timing")
        if (raw_frames < 1 or raw_frames % 17 != 5 or
                context_frames not in (0, 22, 39) or output_frames < 1 or
                output_frames > raw_frames - context_frames or
                output_start != (segments[-1].output_start + segments[-1].output_frames if segments else 0)):
            raise ValueError("source Long H3 manifest has invalid segment lengths")
        segments.append(Segment(
            expected_index, int(raw_frames), int(context_frames),
            int(output_start), int(output_frames)))
        checkpoints.append(checkpoint)
        expected_index += 1
    return segments, checkpoints


def _upscaler_models():
    model_folder = "latent_upscale_models"
    if model_folder not in folder_paths.folder_names_and_paths:
        folder_paths.add_model_folder_path(
            model_folder, os.path.join(folder_paths.models_dir, model_folder))
    models = [
        name for name in folder_paths.get_filename_list(model_folder)
        if Path(name).suffix.lower() in (".pth", ".safetensors")
    ]
    return models or ["(no H3 latent upscaler models found)"]


def _upscaler_model_path(model_name):
    if Path(model_name).suffix.lower() not in (".pth", ".safetensors"):
        raise ValueError("select a .pth or .safetensors H3 latent upscaler model")
    path = folder_paths.get_full_path("latent_upscale_models", model_name)
    if path is None:
        raise ValueError("H3 latent upscaler model was not found: {}".format(model_name))
    return Path(path)


def _file_fingerprint(path):
    stat = path.stat()
    return str(stat.st_size), str(stat.st_mtime_ns)


def _upscale_metadata(source_checkpoint, model_path, model_name, target_width,
                      target_height, align, device, precision, segment):
    source_size, source_mtime = _file_fingerprint(source_checkpoint)
    model_size, model_mtime = _file_fingerprint(model_path)
    return {
        "upscale_schema": UPSCALE_SCHEMA_VERSION,
        "source_file": source_checkpoint.name,
        "source_size": source_size,
        "source_mtime_ns": source_mtime,
        "model_name": model_name,
        "model_size": model_size,
        "model_mtime_ns": model_mtime,
        "target_width": target_width,
        "target_height": target_height,
        "align": align,
        "device": device,
        "precision": precision,
        "index": segment.index,
        "raw_frames": segment.raw_frames,
        "context_frames": segment.context_frames,
        "output_start": segment.output_start,
        "output_frames": segment.output_frames,
    }


def _upscale_metadata_matches(metadata, expected):
    return all(metadata.get(key) == str(value) for key, value in expected.items())


def _atomic_json(path, data):
    temporary = path.with_name("{}.tmp-{}".format(path.name, os.getpid()))
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def _atomic_text(path, value):
    temporary = path.with_name("{}.tmp-{}".format(path.name, os.getpid()))
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(value)
    os.replace(temporary, path)


def _cpu_latent(latent):
    video, audio = _streams(latent)
    return {
        "samples": comfy.nested_tensor.NestedTensor((
            video.detach().to(device="cpu", copy=True).contiguous(),
            audio.detach().to(device="cpu", copy=True).contiguous(),
        ))
    }


def _save_segment(path, latent, metadata):
    video, audio = _streams(latent)
    state = {
        "video": video.detach().to(device="cpu", copy=True).contiguous(),
        "audio": audio.detach().to(device="cpu", copy=True).contiguous(),
    }
    temporary = path.with_name("{}.tmp-{}.safetensors".format(path.stem, os.getpid()))
    comfy.utils.save_torch_file(state, str(temporary), metadata={key: str(value) for key, value in metadata.items()})
    os.replace(temporary, path)


def _load_segment(path):
    state, metadata = comfy.utils.load_torch_file(str(path), safe_load=True, return_metadata=True)
    if "video" not in state or "audio" not in state:
        raise ValueError("{} is not an H3 AV checkpoint".format(path.name))
    latent = {"samples": comfy.nested_tensor.NestedTensor((state["video"], state["audio"]))}
    _streams(latent)
    return latent, metadata or {}


def _prepare_references(vae, audio_vae, width, height, frame_count, ref_image_size,
                        ref_images, ref_videos, ref_video_audios, ref_audios):
    ref_items = []
    ref_blocks = []

    # Encoding and auditing must use the same numeric socket order and batch expansion.
    # Previously only the first item of each batch reached the sampler, while
    # the audit counted every item and could bind <Picture N> to different pixels.
    for image in _flatten_image_tensors(ref_images):
        image_height, image_width = image.shape[1], image.shape[2]
        if ref_image_size == "match":
            scale = min(1.0, math.sqrt((width * height) / (image_width * image_height)))
        else:
            scale = min(1.0, h3.REF_IMAGE_SHORT_EDGE / min(image_width, image_height))
        target_width = max(h3.CANVAS_MULTIPLE, round(image_width * scale / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)
        target_height = max(h3.CANVAS_MULTIPLE, round(image_height * scale / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)
        resized = h3._resize(image[:1], target_width, target_height, "disabled")
        ref_items.append({"type": "image", "data": resized})
        ref_blocks.append({
            "kind": "image",
            "latent_h": target_height // 16,
            "latent_w": target_width // 16,
            "latent": vae.encode(resized),
        })

    ref_video_audios = ref_video_audios or {}
    for name, video_frames in (ref_videos or {}).items():
        if video_frames is None:
            continue
        soundtrack = ref_video_audios.get("ref_video_audio_" + name.rsplit("_", 1)[-1])
        video_height, video_width = video_frames.shape[1], video_frames.shape[2]
        canvas_width, canvas_height = h3.adapt_canvas(video_width, video_height)
        if video_width * video_height < canvas_width * canvas_height:
            canvas_width = max(h3.CANVAS_MULTIPLE, round(video_width / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)
            canvas_height = max(h3.CANVAS_MULTIPLE, round(video_height / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)
        frames = h3._resize(video_frames, canvas_width, canvas_height, "disabled")[:frame_count]
        count = frames.shape[0]
        if count < 5:
            raise ValueError("MiniMax H3 reference videos need at least 5 frames")
        while count % 17 != 5:
            count -= 1
        frames = frames[:count]
        video_latent = vae.encode(frames)
        audio_latent = None
        audio_length = 0
        if soundtrack is not None:
            audio_latent, audio_length = h3._encode_ref_audio(audio_vae, soundtrack)
            ref_items.append({"type": "audio"})
        sample_indices = list(range(0, frames.shape[0], FPS // 2))
        ref_items.append({
            "type": "video",
            "data": frames[sample_indices],
            "timestamps": [index / 2.0 for index in range(len(sample_indices))],
        })
        ref_blocks.append({
            "kind": "video_audio" if audio_length else "video",
            "latent_t": video_latent.shape[2],
            "latent_h": canvas_height // 16,
            "latent_w": canvas_width // 16,
            "ref_audio_t": audio_length,
            "latent": video_latent,
            "audio_latent": audio_latent,
        })

    for audio in (ref_audios or {}).values():
        if audio is None:
            continue
        audio_latent, audio_length = h3._encode_ref_audio(audio_vae, audio)
        ref_items.append({"type": "audio"})
        ref_blocks.append({"kind": "audio", "ref_audio_t": audio_length, "audio_latent": audio_latent})
    return ref_items, ref_blocks


def _conditioning(clip, prompt, ref_items, ref_blocks):
    tokens = clip.tokenize(prompt, minimax_ref_items=ref_items)
    conditioning = clip.encode_from_tokens_scheduled(tokens)
    if ref_blocks:
        conditioning = node_helpers.conditioning_set_values(conditioning, {"minimax_refs": ref_blocks})
    return conditioning


def _delivered_audio(audio, segment, output_frames=None):
    rate = int(audio["sample_rate"])
    start = round(segment.context_frames / FPS * rate)
    frames = segment.output_frames if output_frames is None else int(output_frames)
    count = round(frames / FPS * rate)
    return dict(audio, waveform=audio["waveform"][..., start:start + count])


def _timeline_audio_segment(audio, segment):
    """Return the source-audio window matching one delivered video segment.

    The timeline audio starts at target-video time zero.  It is sliced before
    H3 reference encoding so long songs do not condition every generation pass
    on the whole track and each pass receives the correct local singing cue.
    """
    if not isinstance(audio, dict) or not torch.is_tensor(audio.get("waveform")):
        raise ValueError("timeline_audio must be a ComfyUI AUDIO value")
    rate = int(audio.get("sample_rate") or 0)
    waveform = audio["waveform"]
    if rate <= 0 or waveform.ndim != 3 or waveform.shape[-1] <= 0:
        raise ValueError("timeline_audio has an invalid sample rate or waveform")
    start = round(segment.output_start / FPS * rate)
    count = round(segment.output_frames / FPS * rate)
    end = min(waveform.shape[-1], start + count)
    if start >= waveform.shape[-1] or end <= start:
        raise ValueError(
            "timeline_audio is shorter than segment {} starting at {:.3f}s".format(
                segment.index, segment.output_start / FPS
            )
        )
    sliced = waveform[..., start:end]
    missing = count - sliced.shape[-1]
    if missing > round(rate / FPS):
        raise ValueError(
            "timeline_audio is too short for segment {} by {:.3f}s".format(
                segment.index, missing / rate
            )
        )
    if missing > 0:
        sliced = torch.nn.functional.pad(sliced, (0, missing))
    return dict(audio, waveform=sliced)


def _silence_continuation_audio_tail(audio, silence_seconds=CONTINUATION_GUIDE_SILENCE_SECONDS,
                                    fade_seconds=CONTINUATION_GUIDE_FADE_SECONDS):
    """End a conditioning-only audio guide in silence without changing delivered audio."""
    rate = int(audio["sample_rate"])
    waveform = audio["waveform"].clone()
    total = waveform.shape[-1]
    silence = min(total, max(0, round(float(silence_seconds) * rate)))
    fade = min(total - silence, max(0, round(float(fade_seconds) * rate)))
    if fade:
        ramp = torch.linspace(1.0, 0.0, fade, device=waveform.device, dtype=waveform.dtype)
        waveform[..., total - silence - fade:total - silence] *= ramp
    if silence:
        waveform[..., total - silence:] = 0
    return dict(audio, waveform=waveform)


def _delivered_continuation_guide(previous, source_segment, context_frames, vae, audio_vae,
                                  silence_audio=False, output_frames=None):
    """Anchor the actual delivered tail, excluding unsaved grid padding."""
    video, _ = _streams(previous)
    images = vae.decode(video)
    if images.ndim == 5:
        images = images.reshape(-1, *images.shape[-3:])
    delivered_frames = source_segment.output_frames if output_frames is None else int(output_frames)
    if delivered_frames < context_frames or delivered_frames > source_segment.output_frames:
        raise ValueError("delivered predecessor cannot supply the requested continuation guide")
    end = source_segment.context_frames + delivered_frames
    start = end - context_frames
    if start < 0 or images.shape[0] < end:
        raise ValueError("delivered predecessor is shorter than its continuation guide")
    video_guide = vae.encode(images[start:end])
    audio = vae_decode_audio(audio_vae, previous)
    rate = int(audio["sample_rate"])
    audio = dict(audio, waveform=audio["waveform"][..., round(start / FPS * rate):round(end / FPS * rate)])
    # The guide is conditioning only.  A guaranteed silent boundary prevents
    # late speech from the preceding segment being replayed, garbled or doubled
    # at the beginning of the next segment.  The saved/delivered predecessor is
    # untouched, so complete final syllables remain audible in the master.
    silence_seconds = context_frames / FPS if silence_audio else CONTINUATION_GUIDE_SILENCE_SECONDS
    audio = _silence_continuation_audio_tail(audio, silence_seconds=silence_seconds)
    audio_guide, _ = h3._encode_ref_audio(audio_vae, audio)
    return {"resolved_frame_index": 0, "latent": video_guide,
            "audio_latent": audio_guide}


def _add_continuation_guide(conditioning, guide):
    keyframes = list(conditioning[0][1].get("minimax_keyframes", []))
    keyframes.append(guide)
    return node_helpers.conditioning_set_values(conditioning, {"minimax_keyframes": keyframes})


def _prompt_hash(prompt):
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


_GENERATION_GRAPH_INPUTS = frozenset((
    "model", "audio_refine_model", "clip", "vae", "audio_vae", "sampler",
    "sigmas", "initial_latent",
))
_REFERENCE_INPUT_PREFIXES = (
    "ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios.",
    "ref_image_", "ref_video_", "ref_video_audio_", "ref_audio_",
)


def _generation_input(name):
    return name in _GENERATION_GRAPH_INPUTS or name.startswith(_REFERENCE_INPUT_PREFIXES)


def _prompt_graph_signature(prompt, unique_id):
    if not isinstance(prompt, dict) or unique_id is None:
        return None

    def visit(node_id, root, visiting):
        node_id = str(node_id)
        node = prompt.get(node_id)
        if not isinstance(node, dict):
            return None
        if node_id in visiting:
            raise ValueError("generation input graph contains a cycle")
        visiting.add(node_id)
        inputs = node.get("inputs")
        normalized = {}
        if isinstance(inputs, dict):
            for name in sorted(inputs):
                if root and not _generation_input(name):
                    continue
                value = inputs[name]
                if (isinstance(value, (list, tuple)) and len(value) == 2 and
                        str(value[0]) in prompt and isinstance(value[1], int)):
                    normalized[name] = {
                        "node": visit(value[0], False, visiting),
                        "output": value[1],
                    }
                else:
                    normalized[name] = value
        visiting.remove(node_id)
        return {"class_type": node.get("class_type"), "inputs": normalized}

    return visit(unique_id, True, set())


def _stable_descriptor(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _stable_descriptor(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_stable_descriptor(item) for item in value]
    if callable(value):
        return "{}.{}".format(
            getattr(value, "__module__", type(value).__module__),
            getattr(value, "__qualname__", type(value).__qualname__),
        )
    return "{}.{}".format(type(value).__module__, type(value).__qualname__)


def _sampler_descriptor(sampler):
    result = {"type": _stable_descriptor(sampler)}
    for name in ("sampler_function", "extra_options", "inpaint_options"):
        if hasattr(sampler, name):
            result[name] = _stable_descriptor(getattr(sampler, name))
    return result


def _update_runtime_hash(digest, value):
    if torch.is_tensor(value):
        digest.update(json.dumps({
            "shape": list(value.shape),
            "dtype": str(value.dtype),
        }, sort_keys=True).encode("utf-8"))
        if value.numel():
            flat = value.detach().reshape(-1)
            step = max(1, math.ceil(flat.numel() / 65536))
            sample = flat[::step][:65536].to(device="cpu", copy=True).contiguous()
            digest.update(sample.view(torch.uint8).numpy().tobytes())
        return
    if getattr(value, "is_nested", False):
        for item in value.unbind():
            _update_runtime_hash(digest, item)
        return
    if isinstance(value, dict):
        for key in sorted(value, key=str):
            digest.update(str(key).encode("utf-8"))
            _update_runtime_hash(digest, value[key])
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _update_runtime_hash(digest, item)
        return
    digest.update(json.dumps(_stable_descriptor(value), sort_keys=True).encode("utf-8"))


def _generation_fingerprint(graph_prompt, unique_id, model, audio_refine_model,
                            audio_refine_steps, audio_refine_denoise,
                            clip, sampler, sigmas,
                            ref_image_size, initial_latent, ref_images, ref_videos,
                            ref_video_audios, ref_audios, timeline_audio=None):
    payload = {
        "schema": SCHEMA_VERSION,
        "graph": _prompt_graph_signature(graph_prompt, unique_id),
        "model_type": _stable_descriptor(model),
        "audio_refine_model_type": _stable_descriptor(audio_refine_model),
        "audio_refine_steps": int(audio_refine_steps),
        "audio_refine_denoise": float(audio_refine_denoise),
        "clip_type": _stable_descriptor(clip),
        "sampler": _sampler_descriptor(sampler),
        "ref_image_size": ref_image_size,
        "segment_seed_strategy": SEGMENT_SEED_STRATEGY,
    }
    # Preserve compatible ordinary single-image resumes, but never reuse a
    # checkpoint conditioned with the previous insertion order / batch[:1].
    if ref_images:
        active_keys = [key for key, image in ref_images.items() if image is not None]
        ordered_keys = sorted(active_keys, key=lambda key: [
            (1, int(part)) if part.isdigit() else (0, part)
            for part in re.split(r"(\d+)", str(key))])
        if active_keys != ordered_keys or any(
                ref_images[key].ndim != 4 or ref_images[key].shape[0] != 1 for key in active_keys):
            payload["reference_image_order_strategy"] = "numeric-sockets-all-batch-items-v1"
    digest = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for name, value in (
            ("sigmas", sigmas),
            ("initial_latent", initial_latent),
            ("ref_images", ref_images),
            ("ref_videos", ref_videos),
            ("ref_video_audios", ref_video_audios),
            ("ref_audios", ref_audios),
            ("timeline_audio", timeline_audio)):
        digest.update(name.encode("utf-8"))
        _update_runtime_hash(digest, value)
    return digest.hexdigest()


def _refine_segment_audio(model, conditioning, latent, seed, steps, denoise):
    """Clean one sampled H3 soundtrack while holding its video bit-identical."""
    if model is None or int(steps) <= 0:
        return latent
    refiner_class = getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {}).get(
        "H3AudioRefineSampler")
    if refiner_class is None:
        raise RuntimeError(
            "H3 audio refinement is enabled, but ComfyUI-H3-AudioRefine is not loaded")
    refined = refiner_class().refine(
        model,
        conditioning,
        conditioning,
        latent,
        int(seed),
        int(steps),
        1.0,
        "euler",
        "simple",
        float(denoise),
        0.0,
    )[0]
    before_video, _ = _streams(latent)
    after_video, after_audio = _streams(refined)
    if not torch.equal(before_video, after_video):
        # Some ComfyUI sampling backends reconstruct even a zero-masked stream
        # with small dtype differences.  The refinement contract is audio-only:
        # keep the refined audio and restore the exact pass-1 video defensively.
        refined = refined.copy()
        refined["samples"] = comfy.nested_tensor.NestedTensor(
            (before_video, after_audio))
    return refined


def _segment_noise_seed(base_seed, segment_index, retry_index=0):
    """Derive stable, independent uint64 noise for each segment and audit retry.

    Reusing one noise field at every continuation boundary can repeatedly recreate
    the same unconditioned figure or layout. SplitMix64 gives every segment and
    retry a deterministic stream while preserving the user's base seed.
    """
    mask = 0xFFFFFFFFFFFFFFFF
    stream_index = int(segment_index) + int(retry_index) * 0x100000000
    if stream_index == 0:
        return int(base_seed) & mask
    value = (
        (int(base_seed) & mask)
        + 0x9E3779B97F4A7C15 * stream_index
    ) & mask
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & mask
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
    return (value ^ (value >> 31)) & mask


def _visual_audit_settings(graph_prompt):
    """Read the already-configured upstream LM Studio connection."""
    if not isinstance(graph_prompt, dict):
        return None
    for node in graph_prompt.values():
        if isinstance(node, dict) and node.get("class_type") == "NanoBananaH3Transform":
            provider = (node.get("inputs") or {}).get("provider")
            if isinstance(provider, str):
                return {"backend": "nanobanana", "provider": provider}
    # Generic workflows have no image-transform node. Their explicit review
    # provider owns the same visual AND waveform audit used by manga workflows.
    for node in graph_prompt.values():
        if isinstance(node, dict) and node.get("class_type") == "JapaneseDialoguePronunciationReview":
            provider = (node.get("inputs") or {}).get("provider")
            if provider in ("OpenAI API", "Google Gemini API"):
                return {"backend": "nanobanana", "provider": provider}
    for node in graph_prompt.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != "H3VideoJapanesePromptLMStudio":
            continue
        inputs = node.get("inputs") or {}
        model = inputs.get("model")
        api_base = inputs.get("api_base")
        timeout = inputs.get("timeout_seconds", 360)
        if isinstance(model, str) and isinstance(api_base, str):
            return model, api_base, int(timeout)
    return None


def _load_qwen_audit_module():
    global _QWEN_AUDIT_MODULE
    if _QWEN_AUDIT_MODULE is not None:
        return _QWEN_AUDIT_MODULE
    module_path = Path(__file__).resolve().parents[2] / "qwen_rapid_jp" / "nodes.py"
    if not module_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "minimax_h3_long_video_qwen_audit", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _QWEN_AUDIT_MODULE = module
    return module


def _flatten_image_tensors(value):
    images = []
    if value is None:
        return images
    if torch.is_tensor(value):
        if value.ndim == 4:
            images.extend(value[index:index + 1] for index in range(value.shape[0]))
        elif value.ndim == 3:
            images.append(value.unsqueeze(0))
        return images
    if isinstance(value, (list, tuple)):
        for item in value:
            images.extend(_flatten_image_tensors(item))
    elif isinstance(value, dict):
        for key in sorted(value, key=lambda key: [
                (1, int(part)) if part.isdigit() else (0, part)
                for part in re.split(r"(\d+)", str(key))]):
            images.extend(_flatten_image_tensors(value[key]))
    return images


def _require_reference_images(prompt, ref_images, first_frame=None):
    """Reject a reference-labelled prompt when no matching pixels arrived."""
    available_picture_count = len(_flatten_image_tensors(ref_images))
    if first_frame is not None:
        available_picture_count += len(_flatten_image_tensors(first_frame))
    referenced_picture_ids = {
        int(value) for value in re.findall(r"<Picture\s+([1-9]\d*)>", prompt or "", re.I)
    }
    required_picture_count = max(
        referenced_picture_ids
        or ({1} if "CHARACTER_IDENTITY_LOCK=STRICT" in (prompt or "") else {0})
    )
    if required_picture_count > available_picture_count:
        raise ValueError(
            f"参照画像が不足しています。プロンプトはPicture {required_picture_count}まで要求していますが、"
            f"サンプラーに届いた画像は{available_picture_count}枚です。"
            "参照番号とRef2Vの画像接続（ref_images.ref_image_0 以降）を確認してください。"
        )


def _subject_inventory(prompt):
    before_shots = re.split(r"\[Shot\s+1\]", prompt or "", maxsplit=1, flags=re.I)[0]
    entries = []
    for subject_id in sorted({
            int(value) for value in re.findall(
                r"<Subject\s+([1-9]\d*)>", before_shots, flags=re.I)}):
        match = re.search(
            r"<Subject\s+{}>\s*(.*?)(?=<Subject\s+[1-9]\d*>|$)".format(subject_id),
            before_shots,
            flags=re.I | re.S,
        )
        if match:
            description = re.sub(r"\s+", " ", match.group(1)).strip(" .\n")
            entries.append("R{}: {}".format(subject_id, description[:700]))
    return "\n".join(entries)


def _video_segment_contact_sheets(master_path, segments):
    """Return one 2x2 contact-sheet tensor for each delivered segment."""
    wanted = {}
    for segment in segments:
        start = segment.output_start / FPS
        duration = segment.output_frames / FPS
        offsets = (0.5, duration / 2.0, max(0.5, duration - 0.5))
        wanted[segment.index] = [start + min(duration - 1 / FPS, offset) for offset in offsets]

    captured = {segment.index: [] for segment in segments}
    pending = [
        (timestamp, segment.index)
        for segment in segments for timestamp in wanted[segment.index]
    ]
    pending.sort()
    with av.open(str(master_path)) as container:
        stream = container.streams.video[0]
        cursor = 0
        for frame in container.decode(stream):
            if cursor >= len(pending):
                break
            seconds = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
            while cursor < len(pending) and seconds + (0.5 / FPS) >= pending[cursor][0]:
                captured[pending[cursor][1]].append(frame.to_image().convert("RGB"))
                cursor += 1

    sheets = {}
    for segment in segments:
        frames = captured[segment.index]
        if not frames:
            continue
        cell_width = min(768, frames[0].width)
        cell_height = round(frames[0].height * cell_width / frames[0].width)
        resized = [frame.resize((cell_width, cell_height), Image.Resampling.LANCZOS) for frame in frames]
        sheet = Image.new("RGB", (cell_width * 2, cell_height * 2), "black")
        for index, frame in enumerate(resized[:3]):
            sheet.paste(frame, ((index % 2) * cell_width, (index // 2) * cell_height))
        array = torch.from_numpy(__import__("numpy").asarray(sheet).copy()).float().div(255.0)
        sheets[segment.index] = array.unsqueeze(0)
    return sheets


def _cast_audit_result_passes(result):
    matches = re.findall(r"CAST_AUDIT\s*:\s*(PASS|FAIL)", result or "", flags=re.I)
    if not matches or matches[-1].upper() != "PASS":
        return False
    for line in (result or "").splitlines():
        if not re.search(
                r"unregistered|unmatched|not\s+in\s+(?:the\s+)?R\s*list",
                line, flags=re.I):
            continue
        if re.search(
                r"\b(?:no|none|zero|without)\b[^\n]{0,80}"
                r"(?:unregistered|unmatched)", line, flags=re.I):
            continue
        if re.search(
                r"(?:"
                r"(?:girl|boy|person|human|figure)[^\n]{0,120}"
                r"(?:unregistered|unmatched|not\s+in\s+(?:the\s+)?R\s*list)"
                r"|(?:one|two|three|four|five|six|\d+)\s+"
                r"(?:visible\s+)?(?:unregistered|unmatched)"
                r"|\|\s*(?:UNMATCHED|UNREGISTERED)\s*\|"
                r")",
                line,
                flags=re.I,
        ):
            return False
    return True


def _next_audit_reroll_floor(failed_segments, recovery_floor):
    """Keep the earliest already-guarded segment in every recursive reroll.

    A resumed audit can start from an explicit reroll floor before the first VLM
    attempt.  Dropping that floor after the audit changes the preserved segments'
    prompt hashes because the recovery guard disappears, so the sampler rejects
    checkpoints it created itself.
    """
    candidates = list(failed_segments)
    if recovery_floor is not None:
        candidates.append(recovery_floor)
    if not candidates:
        raise ValueError("an audit reroll requires at least one failed segment")
    return min(candidates)


_CAST_RECOVERY_BLOCK = re.compile(
    r"\n{2}CAST IDENTITY RECOVERY PASS \d+:.*?"
    r"(?=\n{2}CAST IDENTITY RECOVERY PASS \d+:|\Z)",
    flags=re.I | re.S,
)


def _apply_cast_recovery(local_prompt, recovery_guard):
    """Canonicalize recovery text and remove camera instructions that fight it."""
    cleaned = _CAST_RECOVERY_BLOCK.sub("", local_prompt).rstrip()
    cleaned = re.sub(
        r"\b(?:continuous\s+)?360(?:-degree|\s*°)?\s+(?:camera\s+)?orbit\b",
        "controlled medium-tight camera arc of at most 120 degrees",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(
        r"\b(?:continuous\s+)?(?:full|complete)\s+(?:camera\s+)?orbit\b",
        "controlled medium-tight camera arc of at most 120 degrees",
        cleaned,
        flags=re.I,
    )
    return cleaned + recovery_guard


def _audit_closed_cast_video(master_path, segments, master_prompt, ref_images, settings, local_prompts=None, supplied_sheets=None):
    """Return failed segment indices; unavailable audit infrastructure is non-failing."""
    if not settings:
        return [], "unavailable: visual audit settings are missing"
    references = _flatten_image_tensors(ref_images)
    if isinstance(settings, dict) and settings.get("backend") == "nanobanana":
        node_class = comfy_nodes.NODE_CLASS_MAPPINGS.get("NanoBananaH3Transform")
        if not references or not hasattr(node_class, "audit_video_segment"):
            return [s.index for s in segments], "unavailable: API identity auditor or reference missing"
        sheets = supplied_sheets if supplied_sheets is not None else _video_segment_contact_sheets(master_path, segments)
        failed, details = [], []
        unavailable = False
        for segment in segments:
            sheet = sheets.get(segment.index)
            if sheet is None:
                failed.append(segment.index)
                details.append("{}:missing video frames".format(segment.index))
                unavailable = True
                continue
            for audit_attempt in range(2):
                try:
                    passed, detail = node_class.audit_video_segment(
                        settings["provider"], references, sheet,
                        local_prompts[segment.index] if local_prompts is not None else master_prompt)
                    if not passed:
                        failed.append(segment.index)
                        details.append("{}:{}".format(segment.index, detail))
                    break
                except (RuntimeError, ValueError, TypeError, KeyError):
                    if audit_attempt == 1:
                        failed.append(segment.index)
                        details.append("{}:audit unavailable or invalid response".format(segment.index))
                        unavailable = True
            if unavailable:
                break
        if unavailable:
            return failed, "unavailable: " + " | ".join(details)
        return failed, "pass" if not failed else "fail " + " | ".join(details)
    inventory = _subject_inventory(master_prompt)
    if not references or not inventory:
        return [], "unavailable: missing reference tensors or subject inventory"
    module = _load_qwen_audit_module()
    if module is None:
        return [], "unavailable: qwen_rapid_jp is not installed"
    model, api_base, timeout = settings
    sheets = supplied_sheets if supplied_sheets is not None else _video_segment_contact_sheets(master_path, segments)
    failed = []
    details = []
    system_prompt = (
        "You are a strict generated-video cast identity auditor. Picture 1 and any "
        "following reference pictures are authoritative identity references; the final "
        "picture is a chronological contact sheet from one generated segment. Reference "
        "people may be off screen, and pose, expression, camera angle, and occlusion may "
        "change. 'Missing is allowed' means only that an inventory identity can be absent "
        "from a generated frame. It never permits a visible unregistered identity. Match "
        "every visible generated human "
        "one-to-one using stable face, hair, glasses, clothing, and accessories. A visible "
        "human whose combination does not map to one inventory identity is UNMATCHED, "
        "including a badly transformed identity or a duplicate body. Finding even one "
        "visible UNMATCHED or UNREGISTERED human always requires FAIL. Explicitly "
        "scheduled transformations in the supplied inventory are allowed. End with exactly "
        "CAST_AUDIT: PASS or CAST_AUDIT: FAIL."
    )
    for segment in segments:
        sheet = sheets.get(segment.index)
        if sheet is None:
            failed.append(segment.index)
            details.append("{}:missing_frames".format(segment.index))
            continue
        helper = module.QwenJapanesePromptLMStudio()
        helper.SYSTEM_PROMPT = system_prompt
        instruction = (
            "REFERENCE INVENTORY:\n{}\nAudit every human in all three contact-sheet "
            "frames. Missing reference identities are permitted; unregistered or duplicated "
            "visible identities are not. Be concise: report the visible R mappings or the "
            "first unmatched human, then the required final marker, in at most 120 words."
        ).format(inventory)
        try:
            result, _ = helper.convert(
                instruction, model, api_base, 0.0, CAST_AUDIT_MAX_TOKENS, timeout, False,
                input_images=references + [sheet], reasoning="off")
            if not _cast_audit_result_passes(result):
                failed.append(segment.index)
                details.append("{}:{}".format(segment.index, result[-500:]))
        except Exception as exc:
            return [], "unavailable: {}".format(exc)
    return failed, "pass" if not failed else "fail " + " | ".join(details)


def _audit_delivered_audio(candidate, segment, audio_vae, prompt, settings):
    try:
        auditor = comfy_nodes.NODE_CLASS_MAPPINGS["NanoBananaH3Transform"]
        audio = _delivered_audio(vae_decode_audio(audio_vae, candidate), segment)
        passed, detail = auditor.audit_dialogue_audio(settings["provider"], audio, prompt)
        return passed, detail
    except comfy.model_management.InterruptProcessingException:
        raise
    except Exception as exc:
        return None, "unavailable: dialogue audio inspection " + type(exc).__name__


def _audio_result_status(result):
    passed, detail = result
    return "pass" if passed else (detail if passed is None else "fail: dialogue audio: " + detail)


def _attach_local_audio_evidence(local_result, provider_result):
    """Keep deterministic timing evidence when both inspectors pass."""
    if not local_result or local_result[0] is not True or not provider_result:
        return provider_result
    if provider_result[0] is not True:
        return provider_result
    try:
        local = json.loads(local_result[1])
    except (ValueError, TypeError):
        local = {'detail': local_result[1]}
    try:
        provider = json.loads(provider_result[1])
    except (ValueError, TypeError):
        provider = {'detail': provider_result[1]}
    return True, json.dumps({'policy': 'local-timing-plus-provider-v1',
                             'local': local, 'provider': provider}, ensure_ascii=False)


def _audio_result_speech_end(result):
    if not result or not isinstance(result[1], str):
        return None
    try:
        data = json.loads(result[1])
    except ValueError:
        return None
    stack = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stamp = value.get('speech_end_seconds')
            if isinstance(stamp, (int, float)) and math.isfinite(stamp):
                return float(stamp)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return None


def _reconcile_repaired_audio_audits(local_result, provider_result):
    """Resolve a narrow false positive after deterministic repeat removal.

    A repaired candidate is accepted only when two shifted local ASR passes find
    exactly the scripted speech and the provider's transcript agrees.  The
    provider's acoustic reviewer may still hallucinate words in the encoded
    ambience; its objection is overridden only when the reported ending,
    quality and speech margin independently say the utterance is complete and
    usable.  Any transcript disagreement, cut-off, quality problem or other
    issue remains a failure.
    """
    if not local_result or local_result[0] is not True or not provider_result:
        return provider_result
    passed, detail = provider_result
    if passed is True:
        return _attach_local_audio_evidence(local_result, provider_result)
    if passed is not False:
        return provider_result
    try:
        data = json.loads(detail)
        transcript = data.get('verdict') or {}
        acoustic = data.get('acoustic') or {}
        ending = acoustic.get('ending') or {}
        quality = acoustic.get('quality') or {}
        margin = acoustic.get('speech_margin') or {}
        issues = acoustic.get('issues') or []
        kinds = {row.get('kind') for row in issues if isinstance(row, dict)}
        allowed = {'repeated_speech', 'added_speech', 'unscripted_speech'}
        enough_margin = float(margin.get('available_seconds', -1)) >= float(
            margin.get('minimum_seconds', 0.25))
        if not (transcript.get('pass') is True and issues and kinds <= allowed
                and ending.get('status') == 'complete'
                and quality.get('status') == 'usable' and enough_margin):
            return provider_result
        receipt = {
            'pass': True,
            'policy': 'corroborated_repaired_audio_v1',
            'local': json.loads(local_result[1]),
            'provider_transcript': transcript,
            'provider_ending': ending,
            'provider_quality': quality,
            'provider_speech_margin': margin,
            'overridden_provider_acoustic_issues': issues,
        }
        return True, json.dumps(receipt, ensure_ascii=False)
    except (ValueError, TypeError, AttributeError):
        return provider_result


def _local_audio_verdict_result(verdict):
    status = verdict['status']
    if status in ('unavailable', 'not_configured'):
        return None, 'unavailable: local speech recognition ' + verdict.get('reason', status)
    return status != 'fail', json.dumps({'verdict': {'pass': status != 'fail',
                     'issues': verdict.get('issues', [])}, 'scope': verdict['scope'],
                     'device': verdict.get('device'), 'quality_pass': None,
                     'speech_end_seconds': verdict.get('speech_end_seconds'),
                     'delivered_duration_seconds': verdict.get('delivered_duration_seconds')},
                    ensure_ascii=False)


def _segment_with_delivery_frames(segment, output_frames):
    """Return the same generated segment with a shorter delivered AV window."""
    if output_frames in (None, ''):
        return segment
    frames = int(output_frames)
    if not 1 <= frames <= segment.output_frames:
        raise ValueError('delivered trim frames are outside the generated segment')
    return Segment(segment.index, segment.raw_frames, segment.context_frames,
                   segment.output_start, frames)


def _metadata_delivery_frames(metadata, segment):
    value = metadata.get('delivered_trim_frames') if isinstance(metadata, dict) else None
    return _segment_with_delivery_frames(segment, value).output_frames


def _tail_freeze_seconds(images, threshold=0.0015):
    """Measure the final near-identical frame run without judging quiet motion."""
    if images.shape[0] < 2:
        return 0.0
    reduced = images[..., :3].detach().to(device='cpu', dtype=torch.float32)
    changes = (reduced[1:] - reduced[:-1]).abs().mean(dim=tuple(range(1, reduced.ndim)))
    frozen = 0
    for value in reversed(changes.tolist()):
        if value > float(threshold):
            break
        frozen += 1
    return frozen / FPS


def _recover_saved_repeated_speech_candidate(project, segment, prompt_hash,
                                              audio_vae, prompt, settings, audit_visual,
                                              allow_tail_trim=False,
                                              min_output_frames=1,
                                              legacy_tail_freeze_seconds=None,
                                              legacy_attempt=None):
    """Recheck saved candidates and safely shorten an invalid spoken tail."""
    attempt_dir = project / 'audit_attempts'
    paths = sorted(
        attempt_dir.glob(f'segment_{segment.index:04d}_attempt_*.safetensors'),
        key=lambda path: int(re.search(r'_attempt_(\d+)\.safetensors$', path.name).group(1)),
        reverse=True)
    receipts = []
    for path in paths:
        candidate, metadata = _load_segment(path)
        if (metadata.get('prompt_sha256') != prompt_hash
                or int(metadata.get('raw_frames', -1)) != segment.raw_frames
                or int(metadata.get('context_frames', -1)) != segment.context_frames
                or int(metadata.get('output_frames', -1)) != segment.output_frames):
            continue
        attempt = int(metadata.get('attempt', -1))
        if attempt not in range(9):
            continue
        stem = attempt_dir / f'segment_{segment.index:04d}_attempt_{attempt + 1}.resume-cause-audio'
        prior_visual_pass = False
        verdict_path = attempt_dir / f'segment_{segment.index:04d}_attempt_{attempt + 1}.verdict.json'
        try:
            prior_verdict = json.loads(verdict_path.read_text(encoding='utf-8'))
            prior_status = prior_verdict.get('status', '')
            # A combined pass is proof that the exact saved candidate passed
            # its visual check.  Older compact receipts did not embed the
            # audiovisual JSON payload in ``status``.
            prior_visual_pass = (prior_status == 'pass'
                                 and prior_verdict.get('failed') is False)
            payload_start = prior_status.find('{')
            if payload_start >= 0:
                prior_visual_pass = json.loads(prior_status[payload_start:]).get('visual') == 'pass'
        except (OSError, ValueError, TypeError):
            prior_visual_pass = False
        if not prior_visual_pass:
            # A restart recheck may have replaced the small per-attempt receipt
            # with an inspection-unavailable result.  The append-only manifest
            # still contains the earlier verdict for this exact candidate.
            try:
                saved_manifest = json.loads((project / 'manifest.json').read_text(encoding='utf-8'))
                for row in reversed(saved_manifest.get('segment_audits') or []):
                    if (int(row.get('index', -1)) != segment.index
                            or int(row.get('attempt', -1)) != attempt):
                        continue
                    saved_status = row.get('status', '')
                    if saved_status == 'pass' and row.get('failed') is False:
                        prior_visual_pass = True
                        break
                    payload_start = saved_status.find('{') if isinstance(saved_status, str) else -1
                    if payload_start >= 0:
                        try:
                            if json.loads(saved_status[payload_start:]).get('visual') == 'pass':
                                prior_visual_pass = True
                                break
                        except (ValueError, TypeError):
                            pass
            except (OSError, ValueError, TypeError):
                pass
        cutoff, trim_seconds, trim_frames, local_result = None, None, None, None
        cutoff_source = None
        if local_audio_audit.config_contract().get('enabled'):
            delivered = _delivered_audio(vae_decode_audio(audio_vae, candidate), segment)
            verdict = local_audio_audit.audit(delivered, prompt, Path(str(stem) + '.json'), locate_repeats=True)
            local_result = _local_audio_verdict_result(verdict)
            cutoff = local_audio_audit.repeated_speech_cutoff(verdict, prompt)
            cutoff_kind = 'repeated_speech'
            if cutoff is None:
                cutoff = local_audio_audit.unprescribed_speech_cutoff(verdict, prompt)
                cutoff_kind = 'unprescribed_speech'
            if cutoff is not None:
                cutoff_source = 'local_asr'
                # Old repair checkpoints recorded how much frozen/muted tail
                # they appended after the real repeat boundary.  For the same
                # saved attempt and prompt this is more precise than a coarse
                # ASR chunk that can span both the native reaction and repeat.
                try:
                    legacy_freeze = float(legacy_tail_freeze_seconds)
                    hinted_cutoff = (segment.output_frames / FPS) - legacy_freeze
                    if (attempt == int(legacy_attempt) and legacy_freeze > 0
                            and float(cutoff) < hinted_cutoff < segment.output_frames / FPS):
                        cutoff = hinted_cutoff
                        cutoff_source = 'legacy_repeat_boundary'
                except (TypeError, ValueError):
                    pass
                trim_seconds = None
                if allow_tail_trim:
                    trim_seconds = (local_audio_audit.natural_tail_trim_seconds(
                        verdict, prompt, cutoff, min_reaction=0.125)
                        if cutoff_kind == 'unprescribed_speech'
                        else local_audio_audit.natural_tail_trim_seconds(verdict, prompt, cutoff))
                if trim_seconds is None:
                    receipts.append({'attempt': attempt + 1,
                                     'status': 'fail: invalid speech tail has no safe AV trim',
                                     'cutoff_kind': cutoff_kind,
                                     'cutoff_seconds': round(float(cutoff), 3)})
                    continue
                trim_frames = max(1, min(segment.output_frames, round(trim_seconds * FPS)))
                if trim_frames < int(min_output_frames):
                    receipts.append({'attempt': attempt + 1,
                                     'status': 'fail: safe AV trim is shorter than the next continuation guide',
                                     'cutoff_kind': cutoff_kind,
                                     'cutoff_seconds': round(float(cutoff), 3),
                                     'trim_frames': trim_frames,
                                     'minimum_frames': int(min_output_frames)})
                    continue
                repaired_delivered = _delivered_audio(
                    vae_decode_audio(audio_vae, candidate), segment, trim_frames)
                repaired_verdict = local_audio_audit.audit(
                    repaired_delivered, prompt, Path(str(stem) + '.repaired.json'))
                local_result = _local_audio_verdict_result(repaired_verdict)
        # Removing a suffix cannot introduce a new person, camera or identity
        # defect into frames that already passed.  Reuse that visual proof and
        # let the deterministic frozen-tail gate inspect the new delivered end.
        # If the old visual proof is absent, inspect the trimmed candidate.
        if trim_frames is not None and prior_visual_pass:
            visual_failed, visual_status = [], 'pass'
        elif trim_frames is not None:
            visual_failed, visual_status = audit_visual(candidate, attempt, trim_frames)
        elif prior_visual_pass:
            visual_failed, visual_status = [], 'pass'
        else:
            visual_failed, visual_status = audit_visual(candidate, attempt, None)
        if visual_failed or visual_status != 'pass':
            receipts.append({'attempt': attempt + 1, 'status': visual_status,
                             'trim_frames': trim_frames})
            continue
        audit_segment = _segment_with_delivery_frames(segment, trim_frames)
        locally_verified_trim = (trim_frames is not None and prior_visual_pass
                                 and local_result and local_result[0] is True)
        if locally_verified_trim:
            # ``local_audio_audit`` requires agreement from two independently
            # padded ASR windows and supplies a complete scripted-speech end.
            # Together with the saved visual pass this is sufficient to resume
            # after a server restart without re-entering a cloud credential.
            combined = local_result
        else:
            provider_result = (_audit_delivered_audio(candidate, audit_segment, audio_vae, prompt, settings)
                               if isinstance(settings, dict) and settings.get('backend') == 'nanobanana'
                               else local_result or (None, 'unavailable: audio inspection not configured'))
            combined = (_reconcile_repaired_audio_audits(local_result, provider_result)
                        if trim_frames is not None else provider_result)
        receipts.append({'attempt': attempt + 1, 'status': _audio_result_status(combined),
                         'cutoff_seconds': round(float(cutoff), 3) if cutoff is not None else None,
                         'cutoff_kind': cutoff_kind if cutoff is not None else None,
                         'cutoff_source': cutoff_source,
                         'trim_seconds': round(float(trim_seconds), 3) if trim_seconds is not None else None,
                         'trim_frames': trim_frames,
                         'saved_visual_pass_reused': prior_visual_pass,
                         'local_restart_recovery': bool(locally_verified_trim)})
        # Persist each recovery candidate immediately.  A long ASR recheck must
        # not look frozen to the external monitor while it walks saved attempts.
        try:
            manifest_path = project / 'manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else {}
            manifest['status'] = 'segment_recovering'
            manifest['current_segment'] = segment.index
            manifest['current_attempt'] = attempt + 1
            manifest['recovery_candidate'] = receipts[-1]
            writer = globals().get('_atomic_json')
            if callable(writer):
                writer(manifest_path, manifest)
        except (OSError, ValueError, TypeError):
            pass
        if combined[0] is True:
            recovered_metadata = dict(metadata)
            recovered_metadata.update({
                'segment_audit_attempt': str(attempt),
                'segment_audit_failed': 'False',
                'segment_audit_status': 'pass',
                'segment_audit_policy': 'cause-recovery-v2',
                'selected_audio': 'saved_tail_trimmed' if trim_frames is not None else 'saved_unchanged',
                'delivered_trim_frames': str(trim_frames) if trim_frames is not None else '',
                'repeat_tail_freeze_seconds': '',
            })
            return candidate, recovered_metadata, combined, {
                'index': segment.index, 'attempt': attempt + 1,
                'strategy': 'recover_saved_invalid_av_tail_trim_v3',
                'cutoff_seconds': round(float(cutoff), 3) if cutoff is not None else None,
                'trim_frames': trim_frames, 'candidates': receipts}
    return None, None, None, {'index': segment.index,
                              'strategy': 'recover_saved_invalid_av_tail_trim_v3',
                              'candidates': receipts, 'status': 'no_verified_repair'}


def _speaker_probe_offsets(duration):
    """Three chronological adjacent-frame pairs within the speaking window."""
    end = max(0.0, float(duration) - 0.5)
    delta = min(0.25, end / 5) if end else 0.0
    middle = max(0.0, (end - delta) / 2)
    starts = (min(0.5, max(0.0, middle - delta)), middle, max(0.0, end - delta))
    return tuple(t for start in starts for t in (start, min(end, start + delta)))


def _audit_segment_latent(candidate, segment, vae, audio_vae, prompt, ref_images, settings, evidence_path=None, audio_result=None,
                          previous=None, previous_segment=None, visual_prompt=None):
    audio_status = "pass"
    if isinstance(settings, dict) and settings.get("backend") == "nanobanana":
        result = audio_result if audio_result is not None else _audit_delivered_audio(candidate, segment, audio_vae, prompt, settings)
        audio_status = _audio_result_status(result)
        print("[H3 segment audio audit] " + str(segment.index) + ": " + result[1])

    prompt = visual_prompt or prompt
    video, _ = _streams(candidate)
    images = vae.decode(video)
    if images.ndim == 5:
        images = images.reshape(-1, *images.shape[-3:])
    if images.shape[0] < segment.raw_frames:
        raise ValueError("segment audit: VAE returned too few frames")
    duration = segment.output_frames / FPS
    delivered_images = images[
        segment.context_frames:segment.context_frames + segment.output_frames]
    speech_end = _audio_result_speech_end(audio_result)
    frozen_tail = _tail_freeze_seconds(delivered_images)
    del delivered_images
    post_speech_tail = (max(0.0, duration - speech_end)
                        if speech_end is not None else None)
    unnatural_tail = (post_speech_tail is not None and post_speech_tail > 1.5
                      and frozen_tail > 0.75)
    speaker_probe=isinstance(settings,dict) and settings.get('backend')=='nanobanana'
    offsets = _speaker_probe_offsets(duration) if speaker_probe else (0.5, duration / 2, max(0.5, duration - 0.5))
    frames = []
    for offset in offsets:
        index = segment.context_frames + min(segment.output_frames - 1, round(offset * FPS))
        array = (images[index, ..., :3] * 255).clamp(0, 255).to(device="cpu", dtype=torch.uint8).numpy()
        frames.append(Image.fromarray(array))
    camera_result=None
    if (speaker_probe and previous is not None and previous_segment is not None
            and segment.context_frames == 0 and 'Opening camera setup:' in prompt):
        current_camera=[images[segment.context_frames+i:segment.context_frames+i+1].detach().cpu()
                        for i in (0,min(6,segment.output_frames-1))]
        prior_video,_=_streams(previous)
        prior_frames=vae.decode(prior_video)
        if prior_frames.ndim==5:prior_frames=prior_frames.reshape(-1,*prior_frames.shape[-3:])
        end=previous_segment.context_frames+previous_segment.output_frames
        prior_camera=[prior_frames[i:i+1].detach().cpu() for i in (max(0,end-7),end-1)]
        auditor=comfy_nodes.NODE_CLASS_MAPPINGS.get('NanoBananaH3Transform')
        try:
            camera_result=auditor.audit_camera_view(settings['provider'],current_camera,prompt,prior_camera)
        except (AttributeError,RuntimeError,ValueError,TypeError,KeyError):
            camera_result=(False,{'status':'unavailable','issues':[{'kind':'camera_comparison_unavailable',
                            'detail':'Adjacent delivered-frame camera comparison could not be completed.'}]})
        if evidence_path is not None:
            _atomic_json(evidence_path.with_suffix('.camera.json'),camera_result[1])
            comparison=Image.new('RGB',(int(images.shape[2])*2,
                                        (int(images.shape[1])+28)*2),'black')
            for i,frame in enumerate(prior_camera+current_camera):
                labeled=_labeled_audit_image(frame,('PREVIOUS ' if i<2 else 'CURRENT ')+str(i%2+1))
                array=(labeled[0]*255).clamp(0,255).to(torch.uint8).numpy()
                tile=Image.fromarray(array)
                comparison.paste(tile,((i%2)*tile.width,(i//2)*tile.height))
            comparison.save(evidence_path.with_suffix('.camera.png'))
        del prior_frames,prior_video,current_camera,prior_camera
    del images, video
    width = min(768, frames[0].width)
    height = round(frames[0].height * width / frames[0].width)
    columns=3 if speaker_probe else 2
    sheet = Image.new("RGB", (width * columns, height * 2), "black")
    resized=[frame.resize((width,height),Image.Resampling.LANCZOS) for frame in frames]
    for i, frame in enumerate(resized):
        sheet.paste(frame, ((i % columns) * width, (i // columns) * height))
    if evidence_path is not None:
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(evidence_path)
    tensor = (torch.stack([torch.from_numpy(__import__("numpy").asarray(frame).copy()) for frame in resized]).float().div(255)
              if speaker_probe else torch.from_numpy(__import__("numpy").asarray(sheet).copy()).float().div(255).unsqueeze(0))
    failed, visual_status = _audit_closed_cast_video(None, [segment], prompt, ref_images, settings,
                                   {segment.index: prompt}, {segment.index: tensor})
    if camera_result is not None and not camera_result[0]:
        failed=[segment.index]
        visual_status='fail: camera comparison: '+json.dumps(
            dict(camera_result[1],identity_status=visual_status),ensure_ascii=False)
    if unnatural_tail:
        failed = [segment.index]
        visual_status = 'fail: unnatural post-speech frozen tail: ' + json.dumps({
            'speech_end_seconds': round(speech_end, 3),
            'delivered_duration_seconds': round(duration, 3),
            'post_speech_tail_seconds': round(post_speech_tail, 3),
            'frozen_tail_seconds': round(frozen_tail, 3),
        }, ensure_ascii=False)
    if audio_status == "pass" and visual_status == "pass":
        return [], "pass"
    details = {"audio": audio_status, "visual": visual_status}
    prefix = "unavailable:" if all(value == "pass" or value.startswith("unavailable:")
                                   for value in details.values()) else "fail: audiovisual:"
    return [segment.index], prefix + " " + json.dumps(details, ensure_ascii=False)



def _audit_local_script_audio(candidate, segment, audio_vae, prompt, evidence_path):
    try:
        audio = _delivered_audio(vae_decode_audio(audio_vae, candidate), segment)
        verdict = local_audio_audit.audit(audio, prompt, evidence_path)
        return _local_audio_verdict_result(verdict)
    except comfy.model_management.InterruptProcessingException:
        raise
    except Exception as exc:
        return None, 'unavailable: local speech recognition ' + type(exc).__name__


def _combine_three_mode_audits(visual_result, audio_result):
    if audio_result is None:
        return visual_result
    failed, visual_status = visual_result
    audio_status = _audio_result_status(audio_result)
    if visual_status == audio_status == 'pass':
        return [], 'pass'
    details = {'audio': audio_status, 'visual': visual_status}
    unavailable = all(value == 'pass' or value.startswith('unavailable:') for value in details.values())
    return True, ('unavailable:' if unavailable else 'fail: audiovisual:') + ' ' + json.dumps(details, ensure_ascii=False)


def _labeled_audit_image(frame, label):
    array = (frame[0, ..., :3].detach().cpu() * 255).clamp(0, 255).to(torch.uint8).numpy()
    picture = Image.fromarray(array)
    labeled = Image.new("RGB", (picture.width, picture.height + 28), "white")
    labeled.paste(picture, (0, 28))
    ImageDraw.Draw(labeled).text((8, 8), label, fill="black")
    return torch.frombuffer(bytearray(labeled.tobytes()), dtype=torch.uint8).reshape(
        1, labeled.height, labeled.width, 3).float() / 255


def _opening_audit_images(candidate, segment, vae):
    video, _ = _streams(candidate)
    images = vae.decode(video)
    if images.ndim == 5:
        images = images.reshape(-1, *images.shape[-3:])
    indices = (segment.context_frames, segment.context_frames + (segment.output_frames - 1) // 2)
    return {f"selected_opening_{i}": images[index:index+1].detach().cpu().clone()
            for i, index in enumerate(indices)}


def _audit_three_mode_segment(candidate, previous, source_segment, segment, vae,
                              prompt, ref_images, settings, evidence_path):
    """Compare the actual cut boundary through the existing local visual LM."""
    if not settings:
        return [segment.index], 'unavailable: three-mode local visual inspector settings missing'
    video, _ = _streams(candidate)
    images = vae.decode(video)
    if images.ndim == 5:images = images.reshape(-1, *images.shape[-3:])
    if images.shape[0] < segment.context_frames + segment.output_frames:
        raise ValueError('visual audit received too few delivered frames')
    last = (segment.output_frames - 1) / FPS
    offsets = (0.0, min(0.25,last), last/2, min(last,last/2+0.25), max(0,last-0.25), last)
    current = [images[segment.context_frames + round(t*FPS):segment.context_frames + round(t*FPS)+1].detach().cpu() for t in offsets]
    prior = []
    if previous is not None and source_segment is not None:
        prior_video, _ = _streams(previous)
        prior_images = vae.decode(prior_video)
        if prior_images.ndim == 5:prior_images = prior_images.reshape(-1,*prior_images.shape[-3:])
        end = source_segment.context_frames + source_segment.output_frames
        for index in (max(source_segment.context_frames,end-7),end-1):
            prior.append(prior_images[index:index+1].detach().cpu())
    references = _flatten_image_tensors(ref_images)
    evidence_path.parent.mkdir(parents=True,exist_ok=True)
    inspection_frames = ([_labeled_audit_image(frame, f"APPEARANCE REFERENCE {i}") for i, frame in enumerate(references)]
                         + [_labeled_audit_image(frame, f"PREVIOUS END {i}") for i, frame in enumerate(prior)]
                         + [_labeled_audit_image(frame, f"CURRENT FRAME {i} at {offsets[i]:.3f} s") for i, frame in enumerate(current)])
    evidence_frames = inspection_frames
    width = min(384,int(current[0].shape[2]))
    height = round(current[0].shape[1]*width/current[0].shape[2])
    sheet = Image.new('RGB',(width*4,height*math.ceil(len(evidence_frames)/4)), 'black')
    for i,frame in enumerate(evidence_frames):
        array=(frame[0,...,:3]*255).clamp(0,255).to(torch.uint8).numpy()
        picture=Image.fromarray(array).resize((width,height),Image.Resampling.LANCZOS)
        sheet.paste(picture,((i%4)*width,(i//4)*height))
    sheet.save(evidence_path)
    helper_class = comfy_nodes.NODE_CLASS_MAPPINGS.get('QwenJapanesePromptLMStudio')
    if helper_class is None:return [segment.index], 'unavailable: local visual inspector node missing'
    helper = helper_class()
    helper.SYSTEM_PROMPT = 'Evaluate only visible chronological continuity. Return the requested JSON without Markdown.'
    model, api_base, timeout = settings
    response, _ = helper.convert(audit_instruction(prompt,len(references),len(prior),offsets),
                                 model,api_base,0.0,1024,timeout,False,'',
                                 input_images=inspection_frames,reasoning='off')
    passed, detail = parse_verdict(response,len(current))
    verdict = json.loads(detail)
    appearance = verdict.get('appearance_comparison') if references else None
    if references:
        # Identity and continuity share the same labeled frames and one local-LM
        # request.  A second identical multimodal prefill doubled CPU audit time
        # and could time out after the continuity half had already succeeded.
        if (not isinstance(appearance, dict) or type(appearance.get('pass')) is not bool
                or appearance.get('frames_checked') != len(current)
                or not isinstance(appearance.get('issues'), list)):
            raise ValueError('combined visual audit omitted appearance comparison')
        if any(issue.get('kind') not in {'identity_mismatch', 'wardrobe_mismatch'}
               for issue in appearance['issues'] if isinstance(issue, dict)):
            raise ValueError('appearance comparison returned an unrelated issue')
        if appearance['pass'] != (not appearance['issues']):
            raise ValueError('appearance comparison contradicts its evidence')
        if not appearance['pass'] and passed:
            raise ValueError('top-level visual verdict ignored appearance failure')
    _atomic_json(evidence_path.with_suffix('.json'),{'scope':'visual_continuity_only',
                 'appearance_reference_frames':len(references),
                 'prior_delivered_frames':len(prior),'current_offsets_seconds':offsets,
                 'passed':passed,'detail':json.loads(detail),'appearance_comparison':appearance})
    return ([],'pass') if passed else ([segment.index],'fail: visual continuity: '+detail)

def _split_retake_feedback(feedback):
    """Separate concrete failures; a checker outage is not a visual defect."""
    if not feedback or feedback == "pass" or feedback.startswith("unavailable:"):
        return "", ""
    if feedback.startswith("fail: audiovisual:"):
        try:
            details = json.loads(feedback.split("fail: audiovisual:", 1)[1])
        except (ValueError, TypeError):
            return "", ""
        if not isinstance(details, dict):
            return "", ""
        def failure(value):
            return value if isinstance(value, str) and value != "pass" and not value.startswith("unavailable:") else ""
        return failure(details.get("audio")), failure(details.get("visual"))
    if feedback.startswith("fail: dialogue audio:"):
        return feedback, ""
    return "", feedback


def _saved_candidate_prompt(project, index, attempt, fallback):
    path = project / 'audit_attempts' / f'segment_{index:04d}_attempt_{attempt + 1}.json'
    if not path.is_file():
        return fallback
    value = json.loads(path.read_text(encoding='utf-8')).get('prompt')
    return value if isinstance(value, str) and value.strip() else fallback


def _strip_retake_notes(prompt):
    # Only remove notes emitted by this retry path, keeping subsequent H3 sections.
    return re.sub(
        r"(?ms)\n\[(?:AUDIO RETAKE CORRECTION|VISUAL RETAKE CORRECTION|RETAKE CORRECTION|CAUSE RECOVERY PHASE)"
        r"[^\]\n]*\].*?(?=\n(?:\[|(?:subject_definitions|summary|retention_analysis|"
        r"detailed_description|overall_soundscape|non_diegetic_music):)|\Z)",
        "", prompt)


def _retry_generation_prompt(local_prompt, audit_feedback):
    if not audit_feedback:
        return local_prompt
    local_prompt = _strip_retake_notes(local_prompt)
    if audit_feedback.startswith("fail: user review:"):
        correction = audit_feedback.split("fail: user review:", 1)[1]
        correction = correction.replace("<d>", "[review]").replace("</d>", "[/review]")[-1600:]
        return local_prompt + ("\n[RETAKE CORRECTION] Apply this correction to the current segment only: "
            + correction + ". Preserve the approved preceding visual and audio continuity, "
            "character voice, local tagged words and their order. The correction is not spoken dialogue.")
    audio_feedback, visual_feedback = _split_retake_feedback(audit_feedback)
    if audio_feedback:
        # Interpret issue kinds, never condition generation on rejected transcripts.
        try:
            detail = json.loads(audio_feedback.split("fail: dialogue audio:", 1)[1].strip())
            issues = []
            for key in ("verdict", "acoustic"):
                verdict = detail.get(key)
                if isinstance(verdict, dict) and verdict.get("pass") is False:
                    issues.extend(verdict.get("issues") or [])
            kinds = {issue.get("kind") for issue in issues if isinstance(issue, dict)}
        except (ValueError, TypeError, AttributeError, IndexError):
            kinds = set()
        corrections = []
        if "repeated_speech" in kinds:
            corrections.append("Deliver each prescribed clause once; never restart or echo it. "
                               "Do not replay speech from the preceding audio guide. "
                               "When the prescribed line is short, leave the remaining time without human speech.")
        if kinds & {"unscripted_speech", "added_speech", "repeated_vocalization"}:
            corrections.append("Keep the speech-free beats before and after the scheduled line free of vocal filler. "
                               "At the explicit speech time, deliver only the tagged words once. "
                               "Do not continue any word or vocal warm-up from the preceding guide.")
        if "repeated_vocalization" in kinds:
            corrections.append("Keep the mouth relaxed and quiet during the walking and other speech-free beats. "
                               "Do not hum, groan or loop nonverbal vocal sounds between the scripted lines. "
                               "A single brief natural breath is acceptable.")
        if "missing_speech" in kinds:
            corrections.append("Start the prescribed utterance promptly and use a natural, brisk pace "
                               "so every word, including its ending, finishes inside this segment. "
                               "Do not abbreviate or split it across the boundary.")
        if kinds & {"truncated_speech", "insufficient_speech_margin"}:
            corrections.append("The previous voice was cut off by the file boundary. Start at the scheduled "
                               "speech time without a delayed lead-in. Complete the final voiced sound "
                               "with a natural release before the speech deadline, leaving the prescribed "
                               "silent reaction. A sustained vowel is allowed only if it finishes naturally "
                               "inside the window; do not cut or fade a continuing voice.")
        if "changed_words" in kinds:
            corrections.append("Follow the local tagged text verbatim in the established reading; "
                               "do not substitute, paraphrase, or invent words. Articulate Japanese "
                               "grammatical particles and the boundary on each side of them clearly, "
                               "without inserting another word or changing the natural sentence rhythm.")
        if kinds & {"overlapping_speech", "wrong_speaker"}:
            corrections.append("Only the speaker assigned to each local turn may speak, "
                               "with a single voice and no overlapping voices from other characters.")
        if not corrections:
            corrections.append("Render the complete local tagged utterance in its prescribed order "
                               "and reading, without extra words or repeated clauses.")
        local_prompt += (
            "\n[AUDIO RETAKE CORRECTION — highest priority] " + " ".join(corrections)
            + " Preserve repetitions explicitly written in the authoritative dialogue. "
            "After the final prescribed syllable, keep all human voices silent. "
            "Preserve the original music policy, source identities, approved preceding "
            "visual continuity, and all unaffected scene content.")
    if visual_feedback:
        # Safe local fallback when shot replacement is unavailable. Raw audit prose
        # belongs to the repair request, never to the H3 generation prompt.
        local_prompt += (
            "\n[VISUAL RETAKE CORRECTION — highest priority] Keep one coherent body "
            "per intended visible identity, with the reference face, hairstyle, clothes "
            "and attached features. Use stable positions and a face-readable camera view "
            "while preserving the local action, its participants and dialogue ownership. "
            "Preserve off-screen and narration roles. A visible prescribed speaker "
            "articulates the line; other visible characters react with their eyes, "
            "brows and posture while keeping their lips still. "
            "Preserve the preceding continuity, exact dialogue clock, sound and music.")
    return local_prompt


def _prepare_segment_retake(local_prompt, audit_feedback, repair_visual=None):
    """Route compound audits through shot replacement, then add only audio notes."""
    base = _strip_retake_notes(local_prompt)
    audio_feedback, visual_feedback = _split_retake_feedback(audit_feedback)
    if visual_feedback and repair_visual is not None:
        try:
            repaired, record = repair_visual(base, visual_feedback)
            return _retry_generation_prompt(repaired, audio_feedback), dict(record, status="repaired")
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as exc:
            return _retry_generation_prompt(base, audit_feedback), {
                "status": "unavailable", "error_type": type(exc).__name__}
    return _retry_generation_prompt(base, audit_feedback), None


def _candidate_quality_rank(failed, status):
    """Lower is better; unknown inspection never outranks verified quality."""
    if not failed and status == "pass":
        return (0, 0)
    if status.startswith("fail: user review:"):
        return (4, 0)
    if status.startswith("unavailable:"):
        return (3, 0)
    if status.startswith("fail: audiovisual:"):
        try:
            details = json.loads(status.split("fail: audiovisual:", 1)[1])
            audio_rank = _candidate_quality_rank(details.get("audio") != "pass", details.get("audio", "unavailable:"))
            visual = details.get("visual", "unavailable:")
            unknown = int(audio_rank[0] == 3) + int(visual.startswith("unavailable:"))
            visual_failure = int(visual != "pass" and not visual.startswith("unavailable:"))
            visual_penalty = visual_failure * 10
            # A speaker-only fallback is permitted; identity/anatomy problems
            # retain their full weight. Never infer speaker-only from free text.
            if visual_failure:
                try:
                    payload = json.loads(visual[visual.index("{"):])
                    checks = [payload[k] for k in ("first", "recheck") if k in payload]
                    if not checks:
                        checks = [payload]
                    speaker_only = all(
                        isinstance(check, dict)
                        and isinstance(check.get("issues"), list)
                        and not check["issues"]
                        and isinstance(check.get("speaker_check"), dict)
                        and check["speaker_check"].get("status") in ("pass", "fail", "not_observable")
                        for check in checks
                    ) and any(check["speaker_check"]["status"] != "pass" for check in checks)
                    if speaker_only:
                        visual_penalty = 1
                except (ValueError, TypeError, KeyError):
                    pass
            return (1 + bool(unknown), visual_penalty + 100 * (audio_rank[1] + (6 if audio_rank[0] == 2 else 0)))
        except (ValueError, TypeError):
            return (3, 0)
    issues = []
    for offset, char in enumerate(status):
        if char != "{":
            continue
        try:
            detail, _ = json.JSONDecoder().raw_decode(status[offset:])
        except ValueError:
            continue
        def collect(value):
            if isinstance(value, dict):
                if value.get("kind"):
                    issues.append(value)
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(detail)
        break
    weights = {"repeated_speech": 5, "overlapping_speech": 6,
               "wrong_speaker": 5, "changed_words": 4, "missing_speech": 3, "truncated_speech": 4, "insufficient_speech_margin": 4, "unscripted_speech": 5, "repeated_vocalization": 4}
    # Duplicate acoustic/transcript diagnoses count once per kind.
    kinds = {str(issue["kind"]) for issue in issues}
    extra_size = max((issue.get('extra_characters', 0) for issue in issues
                      if isinstance(issue.get('extra_characters'), int)), default=0)
    repeat_count = max((issue.get('repeat_count', 0) for issue in issues
                        if isinstance(issue.get('repeat_count'), int)), default=0)
    return (1 if kinds else 2, sum(weights.get(kind, 6) for kind in kinds)
            + min(10, extra_size / 10) + min(5, repeat_count / 2))


def _checked_candidate_attempt_limit(value):
    if type(value) is not int or not 1 <= value <= 5:
        raise ValueError("candidate_attempt_limit must be an integer from 1 to 5")
    return value


def _generate_verified_segment(generate, audit, report, enabled, max_attempts=5, initial_feedback="",
                               start_attempt=0, initial_candidate=None, initial_attempt=0,
                               initial_verdict=None, on_best=None, candidate_context=None,
                               require_verified=False, recover=None,
                               recover_first=False,
                               allow_unverified=False):
    """Bounded advisory retries, including the previous matching best on resume."""
    if type(start_attempt) is not int or not 0 <= start_attempt <= 9:
        raise ValueError("invalid cumulative segment attempt count")
    best, best_attempt = initial_candidate, initial_attempt
    # Keep the actual verdict with the selected candidate. ``failed`` alone
    # cannot distinguish a bad generated segment from a checker that could not
    # run; treating both as a quality failure was the source of false stops.
    best_status = initial_verdict[1] if best is not None and initial_verdict is not None else None
    best_rank = (_candidate_quality_rank(*initial_verdict)
                 if best is not None and initial_verdict is not None else None)
    feedback = initial_feedback
    best_context = candidate_context() if candidate_context is not None else None
    if recover_first and recover is not None:
        # Legacy repeated-speech repairs muted the second utterance without
        # changing the matching repeated mouth/action frames.  Re-auditing
        # their audio would pass and return the stale AV candidate unchanged,
        # so run the shared-cutoff AV recovery before any ordinary recheck.
        recovered = recover()
        if recovered is not None:
            value, attempt, failed, status = recovered
            report(attempt, failed, status)
            if not failed and status == 'pass':
                if on_best is not None:
                    on_best(value, attempt, failed, status)
                return value, attempt
    if require_verified and enabled and best is not None:
        # A comparator update or transient inspection error should not require a
        # new GPU sample. Reinspect the exact persisted candidate first.
        failed, status = audit(best)
        report(initial_attempt, failed, status)
        best_rank = _candidate_quality_rank(failed, status)
        if on_best is not None:
            on_best(best, initial_attempt, failed, status)
        if not failed and status == 'pass':
            return best, initial_attempt
        feedback = status
    stop = min(9, max(1, int(max_attempts)))
    if not enabled:
        stop = min(stop, start_attempt + 1)
    for attempt in range(start_attempt, stop):
        candidate = generate(attempt, feedback)
        current_context = candidate_context() if candidate_context is not None else None
        if current_context != best_context:
            # Duration repair changed the latent geometry. An older short
            # candidate must never be decoded using the extended trim contract.
            best, best_rank, best_context = None, None, current_context
        if not enabled:
            if on_best is not None:
                on_best(candidate, attempt, False, "not_applicable")
            return candidate, attempt
        try:
            failed, status = audit(candidate)
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as exc:
            failed, status = True, "unavailable: " + type(exc).__name__
        report(attempt, failed, status)
        if require_verified and status.startswith('unavailable:'):
            # Inspection recovery spends an inspection, not another GPU sample.
            try:
                failed, status = audit(candidate)
            except comfy.model_management.InterruptProcessingException:
                raise
            except Exception as exc:
                failed, status = True, 'unavailable: ' + type(exc).__name__
            report(attempt, failed, status)
        rank = _candidate_quality_rank(failed, status)
        if best_rank is None or rank < best_rank:
            best, best_rank, best_attempt, best_status = candidate, rank, attempt, status
            if on_best is not None:
                on_best(candidate, attempt, failed, status)
        if (not failed and status == "pass") or status.startswith("unavailable:"):
            break
        feedback = status
    if best is None:
        raise RuntimeError("区間の累計候補上限に達しましたが、保存済み候補がありません。上限を自動でリセットせず停止しました。")
    if require_verified and enabled and best_rank != (0, 0):
        if isinstance(best_status, str) and best_status.startswith("unavailable:"):
            # A checker outage is neither a media-quality rejection nor a
            # reason to discard a generated candidate. Preserve its explicit
            # unverified status for the manifest and let the normal workflow
            # continue; a later audit/resume can recheck this exact candidate
            # without spending another GPU/API generation attempt.
            print("[品質検査未確認・続行] 区間候補を保持して後続工程へ進みます: " + best_status)
            return best, best_attempt
        if recover is not None:
            recovered = recover()
            if recovered is not None:
                value, attempt, failed, status = recovered
                report(attempt, failed, status)
                if not failed and status == 'pass':
                    if on_best is not None:
                        on_best(value, attempt, failed, status)
                    return value, attempt
        if not allow_unverified:
            raise RuntimeError(
                'この区間の原因別修復では品質を確定できませんでした。合格済み区間・参照素材・台詞・'
                '候補と検査結果を保存しています。同じ保存先で途中再開でき、先頭の画像生成と台詞確認は再利用します。'
                '最後の検査: ' + feedback[:800])
        # Do not abort the whole multi-segment workflow merely because the
        # bounded cause-recovery pass could not certify this saved candidate.
        # Keep the best persisted candidate, mark it as unverified, and let the
        # final manifest expose the audit failure for a later targeted resume.
        print('[品質検査未確定・続行] 原因別修復後も合格を確定できないため、最良候補を保持して後続区間へ進みます: ' + feedback[:800])
        return best, best_attempt
    return best, best_attempt


def _legacy_strict_segment_gate(generate, audit, report, enabled, max_attempts=5, initial_feedback=""):
    """Unused historical implementation."""
    feedback = initial_feedback
    attempt_count = max(1, int(max_attempts)) if enabled else 1
    for attempt in range(attempt_count):
        candidate = generate(attempt, feedback)
        if not enabled:
            return candidate, attempt
        failed, status = audit(candidate)
        report(attempt, failed, status)
        if status.startswith("unavailable:"):
            raise RuntimeError("区間検査を実行できません。次の区間へ進まず停止しました: " + status)
        if not failed and status == "pass":
            return candidate, attempt
        # The next attempt is given only the concrete gate report, so it can repair
        # the failed segment without changing prior approved continuity.
        feedback = status  # Preserve the type prefix and structured verdict before interpretation.
        del candidate
    raise RuntimeError("区間を{}回再生成しても、重大な対象・人数・台詞の検査に合格しませんでした。後続区間は生成していません。".format(attempt_count - 1))


def _segment_lineage(generation_fingerprint, predecessor_lineage, segment, prompt_hash, audit_attempt=0):
    payload = {
        "generation_fingerprint": generation_fingerprint,
        "predecessor_lineage": predecessor_lineage,
        "audit_attempt": audit_attempt,
        "index": segment.index,
        "raw_frames": segment.raw_frames,
        "context_frames": segment.context_frames,
        "output_start": segment.output_start,
        "output_frames": segment.output_frames,
        "prompt_sha256": prompt_hash,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _metadata_matches(metadata, segment, prompt_hash, width, height, noise_seed,
                      generation_fingerprint, predecessor_lineage, lineage):
    expected = {
        "schema": str(SCHEMA_VERSION),
        "index": str(segment.index),
        "raw_frames": str(segment.raw_frames),
        "context_frames": str(segment.context_frames),
        "output_start": str(segment.output_start),
        "output_frames": str(segment.output_frames),
        "width": str(width),
        "height": str(height),
        "seed": str(noise_seed),
        "prompt_sha256": prompt_hash,
        "generation_fingerprint": generation_fingerprint,
        "predecessor_lineage": predecessor_lineage,
        "lineage": lineage,
    }
    if segment.index > 0 and segment.context_frames == 0:
        # A hard cut has no preceding AV guide. Moving it on the assembled
        # timeline does not change its reference-conditioned latent content.
        for key in ('output_start', 'predecessor_lineage', 'lineage'):
            expected.pop(key)
    return all(metadata.get(key) == value for key, value in expected.items())


def _checkpoint_audit_reusable(metadata, audit_applicable, require_verified=False):
    # Advisory mode deliberately saved the best candidate even when all failed.
    # Explicit resume retains it; input hashes/lineage are still checked separately.
    if not require_verified:
        return not audit_applicable or metadata.get('segment_audit_policy') in (
            'before-next-segment-v1:pass', 'advisory-v2', 'cause-recovery-v1')
    return not audit_applicable or (
        str(metadata.get('segment_audit_failed')).lower() == 'false'
        and metadata.get('segment_audit_status') == 'pass'
        and metadata.get("segment_audit_policy") in (
            "before-next-segment-v1:pass", "advisory-v2", "cause-recovery-v1",
            "cause-recovery-v2"))


def _checkpoint_needs_av_sync_recovery(metadata):
    """Reject every legacy repair that muted audio or froze repeated motion."""
    return (metadata.get("selected_audio") in (
                "recovered_first_utterance_only", "refined_first_utterance_only")
            or bool(metadata.get("repeat_tail_freeze_seconds")))


def _restored_audit_record(metadata, previous_manifest, index, attempt):
    selected = (previous_manifest.get("selected_segment_audits") or {}).get(str(index))
    if isinstance(selected, dict) and selected.get("attempt") == attempt:
        return dict(selected)
    status = metadata.get("segment_audit_status")
    if status:
        return {"index": index, "attempt": attempt, "status": status,
                "failed": str(metadata.get("segment_audit_failed")).lower() == "true"}
    return {"index": index, "attempt": attempt,
            "status": "unavailable: reused checkpoint has no stored audit verdict",
            "failed": False}


def _final_audit_summary(selected_segment_audits, completed, audit_applicable):
    verdicts = {int(index): row for index, row in selected_segment_audits.items()
                if isinstance(row, dict)}
    missing = (sorted(set(range(completed)) - set(verdicts))
               if audit_applicable else [])
    failed = sorted(set(missing) | {
        index for index, row in verdicts.items()
        if row.get('failed') or (audit_applicable and row.get('status') != 'pass')
    })
    status = ('warning' if failed else 'pass') if audit_applicable else 'not_applicable'
    return status, failed, missing


def _validate_preserved_segments(segment_paths, segments, local_prompts, reroll_from_segment,
                                 width, height, noise_seed, generation_fingerprint,
                                 has_initial_latent):
    if reroll_from_segment <= 0:
        return
    predecessor_lineage = "initial" if has_initial_latent else "root"
    for checkpoint, segment, local_prompt in zip(segment_paths, segments, local_prompts):
        if segment.index >= reroll_from_segment:
            break
        if not checkpoint.is_file():
            raise ValueError(
                "segment {} cannot be kept because its checkpoint is missing; reroll from this segment or earlier".format(
                    segment.index))
        with safe_open(str(checkpoint), framework="pt", device="cpu") as handle:
            metadata = handle.metadata() or {}
        prompt_hash = _prompt_hash(local_prompt)
        attempt = int(metadata.get("segment_audit_attempt", "0"))
        if attempt not in range(9):
            raise ValueError("invalid segment audit attempt")
        segment_seed = _segment_noise_seed(noise_seed, segment.index, attempt)
        lineage = _segment_lineage(
            generation_fingerprint, predecessor_lineage, segment, prompt_hash, attempt)
        if not _metadata_matches(
                metadata, segment, prompt_hash, width, height, segment_seed,
                generation_fingerprint, predecessor_lineage, lineage):
            raise ValueError(
                "segment {} no longer matches the current generation inputs; reroll from this segment or earlier".format(
                    segment.index))
        predecessor_lineage = metadata.get('lineage', lineage)


def _may_reuse_segment(resume, generated, reroll_from_segment, index, previous_manifest):
    if not resume or generated:
        return False
    if reroll_from_segment >= 0:
        return index < reroll_from_segment
    unfinished = (previous_manifest.get("status") in (
        "sampling", "segment_auditing", "segment_recovering", "segment_check_failed")
        and previous_manifest.get("current_segment") == index)
    return not unfinished


def _segment_attempt_count(manifest, index):
    budget = (manifest.get("segment_attempt_counts") or {}).get(str(index), {})
    count = budget.get("count", 0)
    if type(count) is not int or not 0 <= count <= 9:
        raise ValueError("invalid saved cumulative segment attempt count")
    history = {}
    for row in manifest.get("segment_audits", []):
        if row.get("index") != index:
            continue
        attempt = row.get("attempt", -1)
        if type(attempt) is not int or not -1 <= attempt < 9:
            raise ValueError("invalid saved segment audit history")
        context = row.get("context", budget.get("context", "legacy"))
        history[context] = max(history.get(context, 0), attempt + 1)
    if manifest.get("attempt_limit_scope") == "cumulative-per-segment":
        used = max(history.values(), default=0)
    else:
        # Old histories numbered attempts separately for every predecessor.
        used = sum(history.values())
    return min(9, max(count, used))


def _resume_segment_candidate(checkpoint, segment, prompt_hash, width, height, noise_seed,
                              generation_fingerprint, predecessor_lineage, previous_manifest, context_key):
    if previous_manifest.get("generation_fingerprint") != generation_fingerprint:
        return None, {}, 0
    count = _segment_attempt_count(previous_manifest, segment.index)
    candidate, metadata = None, {}
    if checkpoint.is_file():
        with safe_open(str(checkpoint), framework="pt", device="cpu") as handle:
            saved = handle.metadata() or {}
        attempt = int(saved.get("segment_audit_attempt", "0"))
        if attempt not in range(9):
            raise ValueError("invalid saved candidate attempt")
        if saved.get("generation_fingerprint") == generation_fingerprint:
            count = max(count, attempt + 1)
        lineage = _segment_lineage(generation_fingerprint, predecessor_lineage, segment, prompt_hash, attempt)
        if _metadata_matches(saved, segment, prompt_hash, width, height,
                             _segment_noise_seed(noise_seed, segment.index, attempt),
                             generation_fingerprint, predecessor_lineage, lineage):
            candidate, metadata = _load_segment(checkpoint)
    return candidate, metadata, count


def _save_resume_fallback(project, segment_paths, segments, local_prompts, width, height,
                          noise_seed, generation_fingerprint, has_initial, manifest):
    previous = manifest.get("resume_fallback")
    if manifest.get("status") not in ("complete", "complete_with_audit_failure"):
        return previous
    if manifest.get("generation_fingerprint") != generation_fingerprint:
        return None
    try:
        _validate_preserved_segments(segment_paths, segments, local_prompts, len(segments),
                                     width, height, noise_seed, generation_fingerprint, has_initial)
    except (ValueError, OSError):
        return previous
    lineages = []
    for path in segment_paths:
        with safe_open(str(path), framework="pt", device="cpu") as handle:
            lineages.append(handle.metadata()["lineage"])
    key = hashlib.sha256(json.dumps(lineages).encode()).hexdigest()
    directory = project / "resume_fallback" / key
    if not (directory / "manifest.json").is_file():
        (directory / "latents").mkdir(parents=True, exist_ok=True)
        for path in segment_paths:
            shutil.copy2(path, directory / "latents" / path.name)
        _atomic_json(directory / "manifest.json", manifest)
    return key


def _restore_resume_fallback(project, segment_paths, segments, local_prompts, width, height,
                             noise_seed, generation_fingerprint, has_initial, manifest):
    key = manifest.get("resume_fallback")
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
        return None
    directory = project / "resume_fallback" / key
    if not (directory / "manifest.json").is_file():
        return None
    restored = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if restored.get("generation_fingerprint") != generation_fingerprint:
        return None
    paths = [directory / "latents" / p.name for p in segment_paths]
    _validate_preserved_segments(paths, segments, local_prompts, len(segments),
                                 width, height, noise_seed, generation_fingerprint, has_initial)
    restored["segment_attempt_counts"] = {
        str(segment.index): {"count": max(_segment_attempt_count(manifest, segment.index),
                                         _segment_attempt_count(restored, segment.index))}
        for segment in segments}
    restored["attempt_limit_scope"] = "cumulative-per-segment"
    restored["attempt_limit"] = manifest["attempt_limit"]
    restored["segment_audits"] = list(manifest["segment_audits"])
    for index, record in restored.get("selected_segment_audits", {}).items():
        updated = next((row for row in reversed(manifest["segment_audits"])
                        if row.get("index") == int(index) and row.get("attempt") == record.get("attempt")
                        and row.get("context") == record.get("context")), None)
        if updated is not None:
            restored["selected_segment_audits"][index] = dict(updated)
    restored["resume_fallback"] = key
    restored["budget_fallback"] = {
        "reason": "No remaining attempt for a candidate compatible with the new predecessor; retained the saved complete chain.",
        "requested_segment": manifest.get("current_segment")}
    restored["stop_after_segment"] = manifest.get("stop_after_segment", -1)
    for source, destination in zip(paths, segment_paths):
        shutil.copy2(source, destination)
    _atomic_json(project / "manifest.json", restored)
    return restored


def _manifest_segment(segment, status, checkpoint, prompt_hash, seed, lineage):
    return {
        "index": segment.index,
        "status": status,
        "file": checkpoint.name,
        "output_start": segment.output_start,
        "output_frames": segment.output_frames,
        "timeline_start": segment.output_start / FPS,
        "timeline_end": (segment.output_start + segment.output_frames) / FPS,
        "prompt_window_start": segment.prompt_start_seconds,
        "prompt_window_end": segment.prompt_end_seconds,
        "prompt_file": "prompts/segment_{:04d}.txt".format(segment.index),
        "raw_frames": segment.raw_frames,
        "context_frames": segment.context_frames,
        "seed": seed,
        "prompt_sha256": prompt_hash,
        "lineage": lineage,
    }


def _decode_segment(vae, audio_vae, latent, raw_frames):
    video, _ = _streams(latent)
    images = vae.decode(video)
    if images.ndim == 5:
        images = images.reshape(-1, images.shape[-3], images.shape[-2], images.shape[-1])
    if images.shape[0] < raw_frames:
        raise ValueError("video VAE decoded fewer frames than the H3 segment requires")
    audio = vae_decode_audio(audio_vae, latent)
    return images[:raw_frames], audio


def _normalized_audio_waveform(audio, sample_rate, minimum_samples=0):
    waveform = audio["waveform"]
    source_rate = int(audio["sample_rate"])
    if source_rate != sample_rate:
        raise ValueError("audio VAE changed sample rate between H3 segments")
    waveform = waveform[0]
    if waveform.shape[0] == 1:
        waveform = waveform.repeat(2, 1)
    elif waveform.shape[0] > 2:
        waveform = waveform[:2]
    # Do not retain the VAE graph across segment assembly; on long videos that
    # pins every decode's GPU intermediates and eventually exhausts VRAM.
    waveform = waveform.detach().to(device="cpu", dtype=torch.float32)
    if waveform.shape[-1] < minimum_samples:
        waveform = torch.nn.functional.pad(
            waveform, (0, minimum_samples - waveform.shape[-1]))
    return waveform


def _blend_matching_audio_context(previous_output, current_context, sample_rate,
                                  seconds=AUDIO_SEAM_CROSSFADE_SECONDS):
    """Dissolve the same H3 audio tail decoded from adjacent clips.

    ``current_context`` is not unrelated future audio: it is the pinned copy of
    the preceding latent tail.  Blending its final few milliseconds into the
    already assembled tail moves the waveform onto the current decoder's phase
    before the genuinely new samples begin.  Duration is unchanged.
    """
    overlap = min(
        max(0, round(float(seconds) * int(sample_rate))),
        int(previous_output.shape[-1]),
        int(current_context.shape[-1]),
    )
    if overlap < 2:
        return previous_output
    left = previous_output[..., -overlap:]
    right = current_context[..., -overlap:]
    fade = torch.linspace(
        0.0, 1.0, overlap, dtype=left.dtype, device=left.device)
    blended = left * (1.0 - fade) + right * fade
    result = previous_output.clone()
    result[..., -overlap:] = blended
    return result


def _tempo_fit_audio(waveform, output_samples, sample_rate):
    """Pitch-preserving tempo-fit of H3's complete post-context audio tail.

    H3's 17k+5 grid provides roughly 0.67 seconds beyond a five-second
    continuation window.  Dialogue often finishes in that grid tail.  FFmpeg's
    ``atempo`` recovers the full utterance and fits it into the exact output
    window instead of cutting the final syllable at the video boundary.
    """
    output_samples = int(output_samples)
    if output_samples < 1:
        return waveform[..., :0]
    available_samples = int(waveform.shape[-1])
    if available_samples <= output_samples:
        return torch.nn.functional.pad(
            waveform[..., :output_samples],
            (0, max(0, output_samples - available_samples)),
        )
    channels = int(waveform.shape[-2])
    tempo = available_samples / output_samples
    payload = (
        waveform.detach().to(device="cpu", dtype=torch.float32)
        .transpose(-1, -2).contiguous().numpy().tobytes()
    )
    completed = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "f32le", "-ar", str(int(sample_rate)),
            "-ac", str(channels), "-i", "pipe:0",
            "-filter:a", "atempo={:.10f}".format(tempo),
            "-f", "f32le", "-ar", str(int(sample_rate)),
            "-ac", str(channels), "pipe:1",
        ],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    fitted = torch.frombuffer(
        bytearray(completed.stdout), dtype=torch.float32)
    if fitted.numel() % channels:
        raise RuntimeError("FFmpeg returned malformed tempo-fitted audio")
    fitted = fitted.reshape(-1, channels).transpose(0, 1).contiguous()
    if fitted.shape[-1] < output_samples:
        fitted = torch.nn.functional.pad(
            fitted, (0, output_samples - fitted.shape[-1]))
    return fitted[..., :output_samples]


def _assemble_master_audio(segment_paths, segments, audio_vae, sample_rate,
                           dialogue_activity=None, preserve_generated_audio=False):
    """Decode and stitch H3 audio with the synchronized context overlap.

    H3 audio has a small grid tail and every continuation re-decodes its pinned
    context.  Relative butt-concatenation discards that useful overlap and can
    expose phase/room-tone discontinuities.  Absolute sample boundaries prevent
    accumulated rounding drift, while a short context dissolve preserves the
    exact requested AV duration.
    """
    activity = (
        [True] * len(segments)
        if dialogue_activity is None or preserve_generated_audio else list(dialogue_activity)
    )
    if len(activity) != len(segments):
        raise ValueError("dialogue activity must match the segment plan")
    chunks = []
    for checkpoint, segment, is_active in zip(segment_paths, segments, activity):
        latent, metadata = _load_segment(checkpoint)
        audio = vae_decode_audio(audio_vae, latent)
        raw_samples = round(segment.raw_frames / FPS * sample_rate)
        context_samples = round(segment.context_frames / FPS * sample_rate)
        delivery_frames = _metadata_delivery_frames(metadata, segment)
        output_samples = round(delivery_frames / FPS * sample_rate)
        waveform = _normalized_audio_waveform(audio, sample_rate, raw_samples)
        context_audio = waveform[..., :context_samples]
        post_context = waveform[..., context_samples:raw_samples]
        # Use the same interval as inspection; never import unheard padding,
        # tempo-compress speech, mute ambience, or rewrite the previous chunk.
        output_audio = post_context[..., :output_samples]
        if output_audio.shape[-1] < output_samples:
            output_audio = torch.nn.functional.pad(output_audio, (0, output_samples - output_audio.shape[-1]))
        chunks.append(output_audio)
    if not chunks:
        return torch.zeros((2, 0), dtype=torch.float32)
    return torch.cat(chunks, dim=-1)


def _write_master(path, segment_paths, segments, vae, audio_vae, width, height, crf,
                  local_prompts=None, preserve_generated_audio=False):
    temporary = path.with_name("{}.tmp-{}.mp4".format(path.stem, os.getpid()))
    sample_rate = int(getattr(audio_vae, "audio_sample_rate_output", getattr(audio_vae, "audio_sample_rate", 32000)))
    master_audio = _assemble_master_audio(
        segment_paths, segments, audio_vae, sample_rate,
        None if local_prompts is None else [
            has_tagged_dialogue(item) for item in local_prompts
        ], preserve_generated_audio=preserve_generated_audio)
    try:
        with av.open(str(temporary), mode="w", options={"movflags": "faststart"}) as container:
            video_stream = container.add_stream("h264", rate=Fraction(FPS, 1))
            video_stream.width = width
            video_stream.height = height
            video_stream.pix_fmt = "yuv420p"
            video_stream.options = {"crf": str(crf)}
            audio_stream = container.add_stream("aac", rate=sample_rate, layout="stereo")
            video_pts = 0
            audio_pts = 0
            audio_cursor = 0

            def write_images(images):
                nonlocal video_pts
                for image in images:
                    array = (image[..., :3] * 255).clamp(0, 255).to(device="cpu", dtype=torch.uint8).numpy()
                    frame = av.VideoFrame.from_ndarray(array, format="rgb24")
                    frame.pts = video_pts
                    frame.time_base = Fraction(1, FPS)
                    video_pts += 1
                    for packet in video_stream.encode(frame):
                        container.mux(packet)

            def write_audio(waveform):
                nonlocal audio_pts
                if waveform.shape[-1] == 0:
                    return
                frame = av.AudioFrame.from_ndarray(
                    waveform.float().contiguous().numpy(), format="fltp", layout="stereo")
                frame.sample_rate = sample_rate
                frame.pts = audio_pts
                frame.time_base = Fraction(1, sample_rate)
                audio_pts += waveform.shape[-1]
                for packet in audio_stream.encode(frame):
                    container.mux(packet)

            for checkpoint, segment in zip(segment_paths, segments):
                latent, metadata = _load_segment(checkpoint)
                video, _ = _streams(latent)
                images = vae.decode(video)
                if images.ndim == 5:
                    images = images.reshape(
                        -1, images.shape[-3], images.shape[-2], images.shape[-1])
                if images.shape[0] < segment.raw_frames:
                    raise ValueError(
                        "video VAE decoded fewer frames than the H3 segment requires")
                images = images[:segment.raw_frames]
                delivery_frames = _metadata_delivery_frames(metadata, segment)
                output_images = images[
                    segment.context_frames:segment.context_frames + delivery_frames]
                output_samples = round(delivery_frames / FPS * sample_rate)
                output_audio = master_audio[:, audio_cursor:audio_cursor + output_samples]
                audio_cursor += output_samples
                if metadata.get('delivered_trim_frames') and output_audio.shape[-1] > 1:
                    fade = min(output_audio.shape[-1], round(0.04 * sample_rate))
                    ramp = torch.linspace(1.0, 0.0, fade, dtype=output_audio.dtype)
                    output_audio = output_audio.clone()
                    output_audio[..., -fade:] *= ramp

                write_images(output_images)
                write_audio(output_audio)

            for packet in video_stream.encode(None):
                container.mux(packet)
            for packet in audio_stream.encode(None):
                container.mux(packet)
        os.replace(temporary, path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


class MiniMaxH3LongPromptPlanner(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3LongPromptPlanner",
            display_name="MiniMax H3 Long Prompt Planner",
            category="sampling/minimax/long video",
            description=(
                "Build and optionally override the exact segment prompts consumed by "
                "MiniMax H3 Long Reference Sampler."
            ),
            inputs=[
                io.String.Input(
                    "master_prompt", multiline=True, dynamic_prompts=True,
                    tooltip="Long master prompt containing the global [Shot N] timeline."),
                io.Int.Input("length", default=720, min=24, max=86400, step=1,
                             tooltip="Use the same length as the sampler."),
                io.Int.Input("max_raw_frames", default=124, min=73, max=362, step=17,
                             tooltip="Use the same H3-grid segment value as the sampler."),
                io.Combo.Input("context_frames", options=["22", "39"], default="22",
                               tooltip="Use the same continuation context as the sampler."),
                io.Boolean.Input(
                    "has_initial_latent", default=False, advanced=True,
                    tooltip="Enable only when the sampler's initial_latent will be connected."),
                io.Autogrow.Input(
                    "segment_prompts", optional=True,
                    template=io.Autogrow.TemplatePrefix(
                        input=io.String.Input(
                            "segment_prompt", multiline=True,
                            tooltip="Optional complete replacement for this local segment prompt."),
                        prefix="segment_prompt_", min=0, max=64,
                    ),
                ),
            ],
            outputs=[
                LONG_H3_PROMPT_PLAN.Output("prompt_plan"),
                io.String.Output(
                    "preview", is_output_list=True,
                    tooltip="Ordered STRING list containing one exact local prompt per segment."),
                io.Int.Output("segment_count"),
            ],
        )

    @classmethod
    def execute(cls, master_prompt, length, max_raw_frames, context_frames,
                has_initial_latent=False, segment_prompts=None):
        plan = build_prompt_plan(
            master_prompt, length, max_raw_frames, context_frames,
            has_initial_latent, segment_prompts)
        preview = [entry["prompt"] for entry in plan["segments"]]
        return io.NodeOutput(
            plan, preview, len(plan["segments"]))


class MiniMaxH3LongResumePromptPlan(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3LongResumePromptPlan",
            display_name="MiniMax H3 Long Resume Prompt Plan",
            category="sampling/minimax/long video",
            description=(
                "Load the exact saved segment prompts from an existing Long H3 "
                "project so an audited reroll can resume without regenerating its "
                "master prompt."
            ),
            inputs=[
                io.String.Input(
                    "source_path", multiline=False,
                    tooltip="Output-relative Long H3 project folder, manifest, or master.mp4."),
                io.Boolean.Input(
                    "has_initial_latent", default=False, advanced=True,
                    tooltip="Use the same initial-latent setting as the original sampler run."),
            ],
            outputs=[
                LONG_H3_PROMPT_PLAN.Output("prompt_plan"),
                io.String.Output(
                    "audit_prompt",
                    tooltip="Saved subject inventory used by the closed-cast audit."),
                io.Int.Output("segment_count"),
            ],
        )

    @classmethod
    def execute(cls, source_path, has_initial_latent=False):
        project, manifest_path = _source_bundle(source_path)
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        entries = manifest.get("segments")
        required_ints = ("length_input", "length", "max_raw_frames", "context_frames")
        if (manifest.get("schema") != SCHEMA_VERSION or
                not isinstance(entries, list) or
                any(not isinstance(manifest.get(name), int) for name in required_ints)):
            raise ValueError("source Long H3 manifest is not resume-compatible")
        # A sampling manifest is written incrementally.  After an interrupt it may
        # contain only the segments that finished, while the prompt bundle already
        # contains the complete plan.  Rebuild the authoritative layout from the
        # saved sampler settings instead of mistaking the partial progress list for
        # the requested video length.
        confirmed_windows = manifest.get("confirmed_dialogue_windows")
        expected_segments = (plan_confirmed_windows(confirmed_windows, manifest["context_frames"],
                                                    bool(has_initial_latent))
                             if confirmed_windows else plan_segments(
                                 manifest["length_input"], manifest["context_frames"],
                                 bool(has_initial_latent), manifest["max_raw_frames"]))
        if confirmed_windows and sum(s.output_frames for s in expected_segments) != manifest["length"]:
            raise ValueError("保存済みの確定台詞の区間尺と合計尺が一致しません。")
        for index, entry in enumerate(entries):
            if index >= len(expected_segments) or not isinstance(entry, dict):
                raise ValueError("source Long H3 manifest has an invalid segment layout")
            expected_segment = expected_segments[index]
            expected_record = {
                "index": expected_segment.index,
                "raw_frames": expected_segment.raw_frames,
                "context_frames": expected_segment.context_frames,
                "output_start": expected_segment.output_start,
                "output_frames": expected_segment.output_frames,
            }
            if any(entry.get(key) != value for key, value in expected_record.items()):
                raise ValueError("source Long H3 manifest has an invalid segment layout")
        plan_entries = []
        prompts = []
        for segment in expected_segments:
            index = segment.index
            prompt_path = (project / "prompts" / "segment_{:04d}.txt".format(index)).resolve()
            if (not folder_paths.is_within_directory(str(project), str(prompt_path)) or
                    not prompt_path.is_file()):
                raise ValueError("saved segment prompt is missing: {}".format(index))
            local_prompt = prompt_path.read_text(encoding="utf-8").strip()
            if not local_prompt:
                raise ValueError("saved segment prompt is empty: {}".format(index))
            record = {
                "index": segment.index,
                "raw_frames": segment.raw_frames,
                "context_frames": segment.context_frames,
                "output_start": segment.output_start,
                "output_frames": segment.output_frames,
                "prompt": local_prompt,
            }
            plan_entries.append(record)
            prompts.append(local_prompt)
        plan = {
            "schema": PROMPT_PLAN_SCHEMA_VERSION,
            "length_input": manifest["length_input"],
            "delivered_length": manifest["length"],
            "max_raw_frames": manifest["max_raw_frames"],
            "context_frames": manifest["context_frames"],
            "has_initial_latent": bool(has_initial_latent),
            "segments": plan_entries,
        }
        if confirmed_windows:
            plan["confirmed_dialogue_windows"] = confirmed_windows
        audit_prompt = re.split(
            r"\n\s*(?:CAST, IDENTITY|FULL-FRAME STORY-WORLD)",
            prompts[0], maxsplit=1, flags=re.I)[0].strip()
        if not _subject_inventory(audit_prompt):
            raise ValueError("saved prompts do not contain an auditable subject inventory")
        return io.NodeOutput(plan, audit_prompt, len(plan_entries))


class MiniMaxH3LongReferenceSampler(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3LongReferenceSampler",
            display_name="MiniMax H3 長時間動画生成（通常は設定変更不要）",
            category="sampling/minimax",
            description="Generate a long H3 reference video as sequential AV latent segments. Segment checkpoints are saved under output/h3_long.",
            inputs=[
                io.Model.Input("model", display_name="H3モデル（自動入力）"),
                io.Clip.Input("clip", display_name="テキストエンコーダー（自動入力）"),
                io.Vae.Input("vae", display_name="映像VAE（自動入力）"),
                io.Vae.Input("audio_vae", display_name="音声VAE（自動入力）"),
                io.String.Input(
                    "prompt", display_name="H3最終プロンプト（自動入力）",
                    multiline=True, dynamic_prompts=True,
                    tooltip="Use a timestamped master timeline, or connect prompt_plan to use prebuilt local prompts."),
                io.Int.Input("width", display_name="動画の幅（解像度から自動入力）",
                             default=1344, min=32, max=4096, step=32),
                io.Int.Input("height", display_name="動画の高さ（解像度から自動入力）",
                             default=768, min=32, max=4096, step=32),
                io.Int.Input("length", display_name="H3内部の総フレーム数（秒数から自動計算）",
                             default=720, min=24, max=86400, step=1,
                             tooltip="Total timeline frames at 24 fps. Accepts either 720 or its H3-grid form 736 as 30 seconds."),
                io.Int.Input("max_raw_frames", display_name="1区間のH3内部フレーム数（自動計算）",
                             default=124, min=73, max=362, step=17,
                             tooltip="H3-grid segment value reversed to its intended duration when possible. 362 means a 15-second timeline window. AV guide frames and H3 padding are added internally."),
                io.Combo.Input("context_frames", display_name="継続ガイド（通常39・変更不要）",
                               options=["22", "39"], default="22",
                               tooltip="Previous sampled AV latent generated as a guide at the start of each continuation segment. Guided frames are removed from the delivered video."),
                io.Int.Input("noise_seed", display_name="乱数シード",
                             default=0, min=0, max=0xffffffffffffffff, control_after_generate=True,
                             tooltip="Base seed. A stable independent seed is derived for every segment and visual-audit retry."),
                io.Sampler.Input("sampler", display_name="サンプラー（自動入力）"),
                io.Sigmas.Input("sigmas", display_name="ステップ設定（自動入力）"),
                io.String.Input("cache_name", display_name="途中保存フォルダー名（通常変更不要）",
                                default="h3_long_video",
                                tooltip="Output-relative bundle folder. Supports Save Video patterns, for example h3_long_video/%seed.seed%/. Existing folders become _2, _3, and so on unless resume is enabled."),
                io.Boolean.Input("resume", display_name="途中から再開する", default=False,
                                 tooltip="Reuse compatible segment checkpoints. Missing or incompatible later segments are regenerated."),
                io.Int.Input("reroll_from_segment", display_name="この区間から再生成（-1は自動）",
                             default=-1, min=-1, max=999, step=1,
                             tooltip="With resume enabled: -1 resumes the first missing segment; N keeps segments before N and regenerates N onward."),
                io.Int.Input("crf", display_name="動画圧縮品質（通常18）",
                             default=18, min=0, max=51, step=1, advanced=True),
                io.Combo.Input("ref_image_size", display_name="参照画像の処理サイズ（通常max）",
                               options=["match", "max"], default="match"),
                io.Int.Input(
                    "audio_refine_steps", display_name="音声再精錬ステップ（通常4）",
                    default=4, min=0, max=12, step=1, advanced=True,
                    tooltip="0で無効。映像を固定し、各区間の音声だけを追加デノイズします。"),
                io.Float.Input(
                    "audio_refine_denoise", display_name="音声再精錬の強さ（通常0.5）",
                    default=0.5, min=0.1, max=1.0, step=0.05, advanced=True,
                    tooltip="0.3～0.6は台詞内容を保ちながらエコーや劣化を整えます。"),
                io.Boolean.Input(
                    "auto_dialogue_duration", display_name="台詞数で尺を自動調整",
                    default=False,
                    tooltip="有効時は最終H3プロンプトの台詞1本につき1区間（通常5秒）を割り当て、台詞数に応じて上限なく尺を増減します。"),
                io.Latent.Input("initial_latent", display_name="前の動画潜在（任意）", optional=True,
                                tooltip="Optional sampled H3 AV latent. Its tail guides a removable head before this node's timeline starts at 0."),
                io.Autogrow.Input("ref_images", display_name="参照画像（最大9枚）", optional=True,
                    template=io.Autogrow.TemplatePrefix(
                        input=io.Image.Input("ref_image", display_name="参照画像"), prefix="ref_image_", min=0, max=9)),
                io.Autogrow.Input("ref_videos", display_name="参照動画（任意）", optional=True,
                    template=io.Autogrow.TemplatePrefix(
                        input=io.Image.Input("ref_video", display_name="参照動画", tooltip="Reference video frames at 24 fps"),
                        prefix="ref_video_", min=0, max=3)),
                io.Autogrow.Input("ref_video_audios", display_name="参照動画の音声（任意）", optional=True,
                    template=io.Autogrow.TemplatePrefix(
                        input=io.Audio.Input("ref_video_audio", display_name="参照動画の音声"), prefix="ref_video_audio_", min=0, max=3)),
                io.Autogrow.Input("ref_audios", display_name="参照音声（任意）", optional=True,
                    template=io.Autogrow.TemplatePrefix(
                        input=io.Audio.Input("ref_audio", display_name="参照音声"), prefix="ref_audio_", min=0, max=3)),
                io.Audio.Input(
                    "timeline_audio", display_name="MV元曲タイムライン（区間ごとに自動分割）",
                    optional=True,
                    tooltip="動画0秒に対応する元曲。長尺では各H3区間に合う音声だけを自動で切り出し、歌唱口元同期の参照にします。"),
                LONG_H3_PROMPT_PLAN.Input(
                    "prompt_plan", display_name="区間別プロンプト設計（自動入力・任意）", optional=True,
                    tooltip="Optional exact local prompts from MiniMax H3 Long Prompt Planner. When connected, these replace internal prompt splitting."),
                io.Model.Input(
                    "audio_refine_model", display_name="音声再精錬用H3ベースモデル（任意）",
                    optional=True,
                    tooltip="通常はTurbo LoRAやSpectrumより前のH3ベースモデルを接続します。融合Turboでは追加モデルをロードせず、Spectrum/SLA前の融合モデルを共用できます。未接続なら音声再精錬を行いません。"),
                io.Image.Input("first_frame", display_name="I2V開始フレーム（任意）", optional=True,
                               tooltip="最初の区間の0フレームを固定します。参照画像スロットとは用途が異なります。"),
                io.Boolean.Input("segment_audit", display_name="区間ごとのAI検査", default=True, optional=True,
                                 tooltip="OFFなら検査用API/LMを呼ばずに生成します。"),
                io.Boolean.Input("preserve_input_prompt", display_name="入力文を保持（後付け指示なし）", default=False, optional=True,
                                 tooltip="1区間は入力文そのまま。長尺は区間を切り出しますが人物・発話・音楽の指示を後付けしません。"),
                io.Int.Input("candidate_attempt_limit", display_name="区間ごとの最大候補数",
                             default=5, min=1, max=5, step=1, optional=True, advanced=True,
                              tooltip="同じ生成プロジェクトの各区間で再開前も含め最大5候補。前区間を修正しても回数を保持します。"),
                io.String.Input("reroll_feedback", display_name="再生成する区間の修正指示",
                                default="", multiline=True, optional=True, advanced=True,
                                tooltip="途中再開の開始区間だけに適用します。採用済みの前区間は変更しません。"),
                io.Int.Input("stop_after_segment", display_name="確認用・この区間まで出力（-1＝最後まで）",
                             default=-1, min=-1, max=999, optional=True, advanced=True,
                             tooltip="0始まり。指定区間までの動画を出力して後続の生成を保留します。続行時は-1へ戻して途中再開します。"),
                io.String.Input("resume_execution_token", display_name="途中再開実行トークン（自動）",
                                default="", optional=True, advanced=True,
                                tooltip="保存済み区間からの再開時に自動更新され、ComfyUIのノードキャッシュを確実に無効化します。"),
                H3_DIALOGUE_TIMING.Input("dialogue_timing", optional=True,
                    display_name="確定台詞ごとの秒数（読み確認から接続）",
                    tooltip="接続時は基本5秒、長い台詞だけ必要な秒数へ延長。確定後のプロンプト変更・台詞重複は生成前に検出します。"),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo, io.Hidden.unique_id],
            outputs=[
                io.Video.Output(display_name="video"),
                io.Latent.Output(display_name="last_latent"),
                io.String.Output(display_name="master_path"),
                io.Int.Output(display_name="segment_count"),
            ],
        )

    @classmethod
    def execute(cls, model, clip, vae, audio_vae, prompt, width, height, length, max_raw_frames,
                context_frames, noise_seed, sampler, sigmas, cache_name, resume,
                reroll_from_segment, crf, ref_image_size="match", initial_latent=None,
                ref_images=None, ref_videos=None, ref_video_audios=None, ref_audios=None,
                 timeline_audio=None, prompt_plan=None, audio_refine_model=None, audio_refine_steps=4,
                 audio_refine_denoise=0.5, auto_dialogue_duration=False,
                 _audit_retry_index=0, _audit_reroll_floor=None,
                _audit_history=None, first_frame=None, segment_audit=True,
                preserve_input_prompt=False, candidate_attempt_limit=5, reroll_feedback="", stop_after_segment=-1,
                dialogue_timing=None, resume_execution_token=""):
        if reroll_feedback and (not resume or reroll_from_segment < 0):
            raise ValueError("区間の修正指示には途中再開と再生成開始区間の指定が必要です")
        candidate_attempt_limit = _checked_candidate_attempt_limit(candidate_attempt_limit)
        if type(stop_after_segment) is not int or stop_after_segment < -1:
            raise ValueError("stop_after_segment must be -1 or a zero-based segment index")
        if prompt_plan and prompt_plan.get("lm_device_guard"):
            comfy_nodes.NODE_CLASS_MAPPINGS["H3StandardPrompt"].ensure_cpu_for_video(prompt_plan["lm_device_guard"])
        if width % 32 or height % 32:
            raise ValueError("width and height must be multiples of 32")
        context_frames = int(context_frames)
        initial_video = None
        if initial_latent is not None:
            initial_video, _ = _streams(initial_latent)
            if initial_video.shape[3] * 16 != height or initial_video.shape[4] * 16 != width:
                raise ValueError("initial_latent resolution does not match width and height")

        hidden = cls.hidden
        graph_prompt = hidden.prompt if hidden is not None else None
        unique_id = hidden.unique_id if hidden is not None else None
        cache_name = _expand_cache_name(
            cache_name,
            graph_prompt,
            hidden.extra_pnginfo if hidden is not None else None,
        )
        generation_fingerprint = _generation_fingerprint(
            graph_prompt, unique_id, model, audio_refine_model,
            audio_refine_steps, audio_refine_denoise, clip, sampler, sigmas, ref_image_size,
            initial_latent, ref_images, ref_videos, ref_video_audios, ref_audios, timeline_audio)
        if prompt_plan and prompt_plan.get("quality_policy") == THREE_MODE_POLICY:
            audit_contract = {"quality_policy": THREE_MODE_POLICY, "enabled": bool(segment_audit),
                              "settings": standard_audit_settings(upstream_graph(graph_prompt, unique_id)),
                              "local_audio_guard": local_audio_audit.config_contract()}
            generation_fingerprint = hashlib.sha256((generation_fingerprint + json.dumps(
                audit_contract, sort_keys=True)).encode("utf-8")).hexdigest()
        if first_frame is not None:
            if initial_latent is not None:
                raise ValueError("first_frame and initial_latent cannot both define the opening")
            digest = hashlib.sha256(generation_fingerprint.encode("ascii"))
            _update_runtime_hash(digest, first_frame)
            generation_fingerprint = digest.hexdigest()
        # Never silently degrade a reference prompt to text-only generation.
        # Without the actual pixels, H3 and the identity audit cannot preserve
        # the requested face even when the text contains plausible traits.
        _require_reference_images(prompt, ref_images, first_frame)
        require_timing_connection(prompt, dialogue_timing, prompt_plan)
        segments = plan_segments(
            length, context_frames, initial_latent is not None, max_raw_frames,
            exact_output_frames=(prompt_plan.get("requested_output_frames")
                                 if prompt_plan is not None else None))
        configured_segment_count = len(segments)
        dialogue_turns = count_timeline_dialogue_turns(prompt)
        confirmed_windows = None
        if dialogue_timing is not None:
            if prompt_plan is not None or not auto_dialogue_duration:
                raise ValueError("確定台詞の尺設計は自動尺ON・prompt_plan未接続で使ってください。")
            confirmed_windows = validate_timing(dialogue_timing, prompt)
            if confirmed_windows:
                segments = plan_confirmed_windows(confirmed_windows, context_frames, initial_latent is not None)
        elif prompt_plan is not None and prompt_plan.get("confirmed_dialogue_windows"):
            confirmed_windows = prompt_plan["confirmed_dialogue_windows"]
            segments = plan_confirmed_windows(confirmed_windows, context_frames, initial_latent is not None)
        elif auto_dialogue_duration and prompt_plan is None and dialogue_turns:
            segments = plan_segments(
                dialogue_duration_frames(dialogue_turns, max_raw_frames),
                context_frames,
                initial_latent is not None,
                max_raw_frames,
            )
        # Explicit cuts in confirmed four-panel reference prompts reconstruct
        # the same identities from the source, rather than pinning the old view.
        if dialogue_timing is not None and confirmed_windows and ref_images:
            segments = apply_explicit_camera_cuts(segments, prompt)
        requested_segment_count = len(segments)
        delivered_length = sum(segment.output_frames for segment in segments)
        source_segments = list(segments)
        source_length = delivered_length
        project, master_path, relative_folder = _output_paths(cache_name, resume, width, height)
        preprocessor = comfy_nodes.NODE_CLASS_MAPPINGS.get('NanoBananaH3Transform') if graph_prompt else None
        if preprocessor is not None and hasattr(preprocessor, 'checkpoint_preprocessing'):
            preprocessor.checkpoint_preprocessing(graph_prompt, unique_id, project, ref_images)
        if graph_prompt and dialogue_timing is not None:
            from .resume_api import resume_request
            from server import PromptServer
            saved_request = resume_request(graph_prompt, unique_id, relative_folder,
                                           (hidden.extra_pnginfo or {}).get('workflow'))
            _atomic_json(project / 'resume_prompt.json', saved_request)
            PromptServer.instance.send_sync('h3_long_video.resume_ready',
                {'node_id': str(unique_id), 'project': relative_folder, 'resume': bool(resume)},
                PromptServer.instance.client_id)
        resume_failure_feedback = {}
        previous_manifest = {}
        if resume and (project / "manifest.json").exists():
            previous_manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
            if (previous_manifest.get("generation_fingerprint") == generation_fingerprint
                    and previous_manifest.get("status") == "segment_check_failed"):
                failures = previous_manifest.get("segment_audits") or []
                if failures and failures[-1].get("failed") is True:
                    last = failures[-1]
                    resume_failure_feedback[int(last["index"])] = last["status"]

        duration_extensions = (dict(previous_manifest.get('duration_extensions', {}))
                               if resume and dialogue_timing is not None
                               and previous_manifest.get('generation_fingerprint') == generation_fingerprint else {})
        if duration_extensions:
            segments = recovery_segments(source_segments, duration_extensions)
            delivered_length = sum(item.output_frames for item in segments)
        segment_paths = [project / "latents" / "segment_{:04d}.safetensors".format(segment.index) for segment in segments]
        if prompt_plan is not None:
            local_prompts = prompt_plan_prompts(
                prompt_plan, segments, length, max_raw_frames, context_frames,
                initial_latent is not None)
        else:
            local_prompts = [
                slice_prompt(
                    prompt,
                    segment.prompt_start_seconds,
                    segment.prompt_end_seconds,
                    segment.context_frames / FPS,
                    segment.index,
                    len(segments),
                    source_length / FPS,
                    preserve_input_prompt=preserve_input_prompt,
                    confirmed_dialogue_timing=bool(confirmed_windows),
                    speech_deadline_seconds=(dialogue_timing["turns"][segment.index]["speech_deadline_seconds"]
                                             + duration_extensions.get(str(segment.index), 0)
                                             if dialogue_timing is not None and confirmed_windows
                                             and dialogue_timing["turns"][segment.index].get("speech_deadline_seconds") is not None else None),
                    output_duration_seconds=(segments[segment.index].output_frames / FPS
                                             if str(segment.index) in duration_extensions else None),
                )
                for segment in source_segments
            ]
        if dialogue_timing is not None and confirmed_windows:
            validate_local_dialogues(local_prompts, dialogue_timing)
        recovery_floor = _audit_reroll_floor
        if recovery_floor is not None and not preserve_input_prompt:
            recovery_guard = (
                "\n\nCAST IDENTITY RECOVERY PASS {retry}: Re-establish identities from "
                "the original reference, preserving entity kinds and the local shot's "
                "source-supported visible set. Each character retains its own face, "
                "hair length, silhouette, clothing, accessories and anatomical attachment "
                "points. Hair, tails and garments remain separate source features. "
                "Show each individual once per frame; occlusion and re-entry never create "
                "a second instance. Use a short, smooth lateral camera move with readable "
                "identity cues, avoiding foreground body crossings and extreme reverse "
                "angles. Preserve the source environment and story actions. Background "
                "crowds remain anonymous source crowds, not copies of the main cast. "
                "People, animals, robots and props keep their respective kinds."
            ).format(retry=max(1, _audit_retry_index))
            local_prompts = [
                _apply_cast_recovery(local_prompt, recovery_guard)
                if segment.index >= recovery_floor else local_prompt
                for segment, local_prompt in zip(segments, local_prompts)
            ]
        if resume:
            _validate_preserved_segments(
                segment_paths, segments, local_prompts, reroll_from_segment,
                width, height, noise_seed, generation_fingerprint,
                initial_latent is not None)
        fallback_key = (_save_resume_fallback(project, segment_paths, segments, local_prompts,
                         width, height, noise_seed, generation_fingerprint, initial_latent is not None,
                         previous_manifest) if resume else None)
        prompt_directory = project / "prompts"
        prompt_directory.mkdir(exist_ok=True)
        for segment, local_prompt in zip(segments, local_prompts):
            _atomic_text(
                prompt_directory / "segment_{:04d}.txt".format(segment.index),
                local_prompt,
            )
        manifest = {
            "schema": SCHEMA_VERSION,
            "status": "sampling",
            "fps": FPS,
            "latent_format": "minimax_h3_av",
            "width": width,
            "height": height,
            "length": delivered_length,
            "length_input": length,
            "auto_dialogue_duration": bool(auto_dialogue_duration),
            "dialogue_turns": dialogue_turns,
            "confirmed_dialogue_windows": confirmed_windows,
            "dialogue_timing": dialogue_timing,
            "configured_segment_count": configured_segment_count,
            "requested_segment_count": requested_segment_count,
            "max_raw_frames": max_raw_frames,
            "context_frames": context_frames,
            "seed": noise_seed,
            "segment_seed_strategy": SEGMENT_SEED_STRATEGY,
            "audio_continuity_strategy": AUDIO_CONTINUITY_STRATEGY,
            "timeline_audio_reference": {
                "enabled": timeline_audio is not None,
                "strategy": "per-delivered-segment-v1" if timeline_audio is not None else "disabled",
                "sample_rate": int(timeline_audio["sample_rate"]) if timeline_audio is not None else None,
                "sample_count": int(timeline_audio["waveform"].shape[-1]) if timeline_audio is not None else None,
            },
            "preserve_generated_audio": bool(
                prompt_plan and prompt_plan.get("preserve_generated_audio", False)),
            "audio_refine": {
                "enabled": audio_refine_model is not None and audio_refine_steps > 0,
                "strategy": AUDIO_REFINE_STRATEGY,
                "steps": int(audio_refine_steps),
                "denoise": float(audio_refine_denoise),
                "video_denoise": 0.0,
            },
            "visual_cast_audit_retry": _audit_retry_index,
            "visual_cast_audit_history": list(_audit_history or []),
            "prompt_source": "plan" if prompt_plan is not None else "master",
            "generation_fingerprint": generation_fingerprint,
            "duration_extensions": duration_extensions,
            "segments": [],
        }
        three_mode = bool(prompt_plan and prompt_plan.get("quality_policy") == THREE_MODE_POLICY)
        audit_graph = upstream_graph(graph_prompt, unique_id)
        audit_images = ref_images if first_frame is None else {"first_frame": first_frame}
        audit_applicable = (segment_audit and audit_images is not None and any(
            "CLOSED-CAST CONTINUITY" in item or "REFERENCE IDENTITY CONTINUITY" in item
            for item in local_prompts))
        audit_settings = standard_audit_settings(audit_graph) if three_mode else _visual_audit_settings(graph_prompt)
        local_audio_enabled = three_mode and bool(segment_audit) and local_audio_audit.config_contract()["enabled"]
        if three_mode:
            audit_applicable = bool(segment_audit)
            manifest["quality_policy"] = THREE_MODE_POLICY
            manifest["audit_scope"] = "visual_continuity_only" if segment_audit else "disabled"
            manifest["audio_content_audit"] = ("local_japanese_extra_speech_guard; not a full listening verdict"
                                                if local_audio_enabled else "not_performed: local visual LM cannot listen to audio")
            if local_audio_enabled:
                manifest["audit_scope"] = "visual_continuity_and_local_scripted_speech"
                manifest["audio_guard_policy"] = local_audio_audit.config_contract()['policy']
            manifest["attempt_limit"] = candidate_attempt_limit
            manifest["on_attempt_limit"] = "select_best_and_report_remaining_issues"
        if audit_applicable and not audit_settings:
            print('[H3 warning] 区間検査設定なし。未確認として続行。')
            audit_applicable = False
        manifest["audit_mode"] = "before-next-segment-v1"
        compatible_history = resume and previous_manifest.get("generation_fingerprint") == generation_fingerprint
        manifest["segment_audits"] = list(previous_manifest.get("segment_audits", [])) if compatible_history else []
        manifest["segment_attempt_counts"] = ({str(item.index): {"count": _segment_attempt_count(previous_manifest, item.index)}
                                               for item in segments} if compatible_history else {})
        recovery_attempts = 4 if dialogue_timing is not None and audit_applicable else 0
        total_candidate_attempt_limit = candidate_attempt_limit + recovery_attempts
        manifest["attempt_limit"] = candidate_attempt_limit
        manifest["cause_recovery_attempts"] = recovery_attempts
        manifest["attempt_limit_total"] = total_candidate_attempt_limit
        manifest["on_attempt_limit"] = ("switch_to_cause_recovery_then_require_pass"
                                        if recovery_attempts else "select_best_and_report_remaining_issues")
        manifest["attempt_limit_scope"] = "cumulative-per-segment"
        manifest["stop_after_segment"] = stop_after_segment
        manifest["resume_fallback"] = fallback_key
        audit_images_for_segment = audit_images
        opening_images = None
        locked_downstream = [item.index for item in segments
                             if item.index > reroll_from_segment >= 0
                             and (stop_after_segment < 0 or item.index <= stop_after_segment)
                             and _segment_attempt_count(manifest, item.index) >= total_candidate_attempt_limit]
        keep_complete_chain = bool(fallback_key and locked_downstream and previous_manifest.get("status")
                                   in ("complete", "complete_with_audit_failure"))
        if keep_complete_chain:
            manifest["budget_fallback"] = {"reason": "A downstream segment has exhausted its budget; kept the compatible complete chain.",
                                            "locked_segments": locked_downstream}
        manifest["segment_references"] = dict(previous_manifest.get("segment_references", {})) if compatible_history else {}
        manifest["resume_failure_feedback"] = resume_failure_feedback
        _atomic_json(project / "manifest.json", manifest)

        previous = initial_latent
        previous_delivery_frames = None
        generated = False
        completed = 0
        ref_items = None
        ref_blocks = None
        prepared_reference_key = None
        latent = None
        conditioning = None
        guider = None
        noise = None
        sampled = None
        cached = None
        predecessor_lineage = "initial" if initial_latent is not None else "root"
        for segment, checkpoint, local_prompt in zip(segments, segment_paths, local_prompts):
            prompt_hash = _prompt_hash(local_prompt)
            retry_stream = (
                _audit_retry_index
                if _audit_reroll_floor is not None and segment.index >= _audit_reroll_floor
                else 0
            )
            segment_seed = _segment_noise_seed(
                noise_seed, segment.index, retry_stream)
            lineage = _segment_lineage(
                generation_fingerprint, predecessor_lineage, segment, prompt_hash)
            may_reuse = keep_complete_chain or _may_reuse_segment(
                resume, generated and bool(segment.context_frames), reroll_from_segment, segment.index, previous_manifest)
            if may_reuse and checkpoint.exists():
                cached, metadata = _load_segment(checkpoint)
                cached_attempt = int(metadata.get("segment_audit_attempt", "0"))
                if cached_attempt not in range(9):
                    raise ValueError("invalid cached audit attempt")
                segment_seed = _segment_noise_seed(noise_seed, segment.index, cached_attempt)
                lineage = _segment_lineage(generation_fingerprint, predecessor_lineage, segment, prompt_hash, cached_attempt)
                approved = _checkpoint_audit_reusable(metadata, audit_applicable,
                                                     require_verified=dialogue_timing is not None)
                # Checkpoints produced by the first repeated-speech repair
                # silenced only the audio.  Reusing one without the matching
                # video hold would preserve a second silent lip-sync/action.
                # Force it through cause recovery once so the shared cutoff is
                # recorded and applied to both streams during master assembly.
                if _checkpoint_needs_av_sync_recovery(metadata):
                    approved = False
                if approved and _metadata_matches(
                        metadata, segment, prompt_hash, width, height, segment_seed,
                        generation_fingerprint, predecessor_lineage, lineage):
                    lineage = metadata.get('lineage', lineage)
                    previous = cached
                    previous_delivery_frames = _metadata_delivery_frames(metadata, segment)
                    completed += 1
                    if audit_applicable:
                        restored_audit = _restored_audit_record(metadata, previous_manifest, segment.index, cached_attempt)
                        if keep_complete_chain and reroll_feedback and segment.index == reroll_from_segment:
                            restored_audit.update(failed=True, status="fail: user review: " + reroll_feedback, source="user_review")
                        manifest.setdefault("selected_segment_audits", {})[str(segment.index)] = restored_audit
                        manifest["segment_audits"].append(restored_audit)
                        manifest.setdefault("candidate_selection", {})[str(segment.index)] = {
                            "attempt": cached_attempt + 1, "policy": "reused matching best candidate",
                            "rank": _candidate_quality_rank(restored_audit["failed"], restored_audit["status"])}
                    manifest_segment = _manifest_segment(
                        segment, "reused", checkpoint, prompt_hash, segment_seed, lineage)
                    manifest_segment['delivered_output_frames'] = previous_delivery_frames
                    manifest["segments"].append(manifest_segment)
                    _atomic_json(project / "manifest.json", manifest)
                    predecessor_lineage = lineage
                    if segment.index == stop_after_segment:
                        break
                    continue
                if reroll_from_segment >= 0 and segment.index < reroll_from_segment:
                    raise ValueError(
                        "segment {} no longer matches the current generation inputs; reroll from this segment or earlier".format(segment.index))

            generated = True
            context_key = _segment_lineage(generation_fingerprint, predecessor_lineage, segment, prompt_hash)
            old_candidate, old_metadata, start_attempt = _resume_segment_candidate(
                checkpoint, segment, prompt_hash, width, height, noise_seed,
                generation_fingerprint, predecessor_lineage, previous_manifest if resume else {}, context_key)
            old_attempt = int(old_metadata.get("segment_audit_attempt", "0"))
            old_record = (_restored_audit_record(old_metadata, previous_manifest, segment.index, old_attempt)
                          if old_candidate is not None else None)
            segment_feedback = resume_failure_feedback.get(segment.index, "")
            if reroll_feedback and segment.index == reroll_from_segment:
                segment_feedback = "fail: user review: " + reroll_feedback
                if old_record is not None:
                    old_record = {"index": segment.index, "attempt": old_attempt,
                                  "failed": True, "status": segment_feedback, "source": "user_review", "context": context_key}
                    manifest["segment_audits"].append(old_record)
            if old_record is not None:
                if old_record not in manifest["segment_audits"]:
                    manifest["segment_audits"].append(old_record)
                manifest.setdefault("selected_segment_audits", {})[str(segment.index)] = old_record
            if start_attempt >= total_candidate_attempt_limit and old_candidate is None:
                restored = _restore_resume_fallback(project, segment_paths, segments, local_prompts,
                           width, height, noise_seed, generation_fingerprint, initial_latent is not None, manifest)
                if restored is not None:
                    manifest = restored
                    completed = len(segments) if stop_after_segment < 0 else min(len(segments), stop_after_segment + 1)
                    previous, _ = _load_segment(segment_paths[completed - 1])
                    break
            if three_mode and audit_applicable and audit_images is None and segment.index > 0:
                if opening_images is None:
                    opening, _ = _load_segment(segment_paths[0])
                    opening_images = _opening_audit_images(opening, segments[0], vae)
                audit_images_for_segment = opening_images
            if start_attempt < total_candidate_attempt_limit:
                segment_ref_images = ref_images
                reference_key = "master"
                segment_ref_audios = dict(ref_audios or {})
                if timeline_audio is not None:
                    segment_ref_audios["timeline_audio"] = _timeline_audio_segment(timeline_audio, segment)
                    reference_key += ":timeline_audio:{}".format(segment.index)
                if (dialogue_timing is not None and confirmed_windows and ref_images
                        and isinstance(audit_settings, dict) and audit_settings.get("backend") == "nanobanana"
                        and any(row.get("class_type") == "NanoBananaH3Transform" for row in audit_graph.values())):
                    auditor = comfy_nodes.NODE_CLASS_MAPPINGS["NanoBananaH3Transform"]
                    manifest.update(status="preparing_shot_reference", current_segment=segment.index,
                                    current_attempt=start_attempt + 1)
                    _atomic_json(project / "manifest.json", manifest)
                    try:
                        scoped_image, reference_receipt = auditor.prepare_shot_reference(
                            audit_settings["provider"], _flatten_image_tensors(ref_images), prompt, local_prompt,
                            project / "shot_references")
                    except Exception as exc:
                        manifest.update(status="error", error={"stage": "shot_reference",
                                        "segment_index": segment.index, "exception_type": type(exc).__name__})
                        _atomic_json(project / "manifest.json", manifest)
                        raise
                    if reference_receipt is not None:
                        segment_ref_images = {next(iter(ref_images)): scoped_image}
                        reference_key = reference_receipt["signature"]
                        manifest.setdefault("segment_references", {})[str(segment.index)] = {
                            "image_file": "shot_references/" + reference_receipt["image_file"],
                            "image_sha256": reference_receipt["image_sha256"],
                            "subject_ids": reference_receipt["subject_ids"], "passed": True}
                        _atomic_json(project / "manifest.json", manifest)
                if ref_items is None or reference_key != prepared_reference_key:
                    ref_items, ref_blocks = _prepare_references(
                        vae, audio_vae, width, height,
                        max(item.raw_frames for item in segments), ref_image_size,
                        segment_ref_images, ref_videos, ref_video_audios, segment_ref_audios)
                    prepared_reference_key = reference_key
            guide = None
            if segment.context_frames and start_attempt < total_candidate_attempt_limit:
                source_segment = segments[segment.index - 1] if segment.index else None
                if source_segment is None:
                    # Caller-supplied initial latent has no saved trim contract.
                    v, a = _streams(previous)
                    guide = {"resolved_frame_index": 0,
                             "latent": v[:, :, -h3.video_latent_t(segment.context_frames):].clone(),
                             "audio_latent": a[..., -round(segment.context_frames / FPS * h3.AUDIO_LATENT_FPS):].clone()}
                else:
                    guide = _delivered_continuation_guide(
                        previous, source_segment, segment.context_frames, vae, audio_vae,
                        silence_audio=has_tagged_dialogue(local_prompt),
                        output_frames=previous_delivery_frames)
            repaired_visual_base = local_prompt
            last_visual_feedback = ''
            attempt_audio_result = None
            local_attempt_audio_result = None
            attempt_visual_prompt = _saved_candidate_prompt(project, segment.index, old_attempt, local_prompt)
            audio_sources = ({old_attempt: old_metadata.get("selected_audio", "original")}
                             if old_candidate is not None else {})
            delivery_trim_frames = ({old_attempt: int(old_metadata['delivered_trim_frames'])}
                                    if old_candidate is not None
                                    and old_metadata.get('delivered_trim_frames') else {})
            def generate_candidate(attempt, audit_feedback):
                nonlocal repaired_visual_base, attempt_audio_result, local_attempt_audio_result, segment, local_prompt, prompt_hash, context_key, delivered_length, last_visual_feedback
                nonlocal ref_items, ref_blocks, prepared_reference_key
                nonlocal attempt_visual_prompt
                attempt_audio_result = None
                local_attempt_audio_result = None
                delivery_trim_frames.pop(attempt, None)
                duration_changed = False
                audio_feedback, visual_feedback = _split_retake_feedback(audit_feedback)
                if visual_feedback:
                    last_visual_feedback = visual_feedback
                if (audit_feedback and dialogue_timing is not None and confirmed_windows
                        and needs_duration_repair(audit_feedback)
                        and duration_extensions.get(str(segment.index), 0) < 2
                        and segment.output_frames < 15 * FPS):
                    source = source_segments[segment.index]
                    extra = duration_extensions.get(str(segment.index), 0) + 1
                    duration_extensions[str(segment.index)] = extra
                    duration_changed = True
                    segments[:] = recovery_segments(source_segments, duration_extensions)
                    segment = segments[segment.index]
                    delivered_length = sum(item.output_frames for item in segments)
                    local_prompt = slice_prompt(
                        prompt, source.prompt_start_seconds, source.prompt_end_seconds,
                        source.context_frames / FPS, source.index, len(segments), source_length / FPS,
                        preserve_input_prompt=preserve_input_prompt, confirmed_dialogue_timing=True,
                        speech_deadline_seconds=(dialogue_timing['turns'][source.index].get('speech_deadline_seconds')
                                                 or source.output_frames / FPS - 1) + extra,
                        output_duration_seconds=segment.output_frames / FPS)
                    local_prompts[segment.index] = local_prompt
                    validate_local_dialogues(local_prompts, dialogue_timing)
                    repaired_visual_base = local_prompt
                    prompt_hash = _prompt_hash(local_prompt)
                    context_key = _segment_lineage(generation_fingerprint, predecessor_lineage, segment, prompt_hash)
                    manifest['length'] = delivered_length
                    manifest['duration_extensions'] = dict(duration_extensions)
                    manifest.setdefault('duration_repairs', []).append({
                        'index': segment.index, 'attempt': attempt, 'extra_seconds': extra,
                        'reason': 'observed_truncated_speech', 'output_frames': segment.output_frames})
                    _atomic_text(project / 'prompts' / f'segment_{segment.index:04d}.txt', local_prompt)
                manifest["status"] = "sampling"
                manifest["current_segment"] = segment.index
                manifest["current_attempt"] = attempt + 1
                manifest["segment_attempt_counts"][str(segment.index)] = {
                    "context": context_key, "count": attempt + 1}
                _atomic_json(project / "manifest.json", manifest)
                candidate_seed = _segment_noise_seed(noise_seed, segment.index, attempt)
                repair_visual = None
                if duration_changed and last_visual_feedback:
                    audit_feedback = 'fail: audiovisual: ' + json.dumps({
                        'audio': audio_feedback or 'pass', 'visual': last_visual_feedback})
                if (_split_retake_feedback(audit_feedback)[1]
                        and isinstance(audit_settings, dict) and audit_settings.get("backend") == "nanobanana"):
                    auditor = comfy_nodes.NODE_CLASS_MAPPINGS["NanoBananaH3Transform"]
                    visual_failures = sum(
                        bool(_split_retake_feedback(row.get("status", ""))[1])
                        for row in manifest["segment_audits"]
                        if row["index"] == segment.index and row.get("context") == context_key and row.get("failed"))
                    def repair_visual(base, feedback):
                        return auditor.repair_video_segment_prompt(
                            audit_settings["provider"], _flatten_image_tensors(audit_images), base, feedback,
                            simplify_framing=visual_failures >= 2)
                attempt_prompt, repair_record = _prepare_segment_retake(
                    repaired_visual_base, audit_feedback, repair_visual)
                if attempt >= candidate_attempt_limit:
                    attempt_prompt += (
                        "\n[CAUSE RECOVERY PHASE — highest priority] The regular candidate strategy "
                        "has repeatedly failed inspection. Use a stable, simple performance for this "
                        "segment: begin the one assigned tagged line promptly, speak it exactly once, "
                        "and then keep every mouth closed and every human voice silent. Do not add a "
                        "lead-in, acknowledgment, filler, echoed clause, reply or off-screen voice. "
                        "Keep the camera and non-speakers calm so the assigned speaker and exact words "
                        "remain unambiguous. Preserve the accepted preceding segment and source identities."
                    )
                    manifest.setdefault("cause_recovery", []).append({
                        "index": segment.index, "attempt": attempt + 1,
                        "regular_attempt_limit": candidate_attempt_limit,
                        "strategy": "stable_single_speaker_exact_line_v1"})
                attempt_dir = project / "audit_attempts"
                attempt_dir.mkdir(exist_ok=True)
                _atomic_json(attempt_dir / f"segment_{segment.index:04d}_attempt_{attempt + 1}.json",
                             {"seed": candidate_seed, "prompt": attempt_prompt,
                              "previous_audit": audit_feedback, "visual_repair": repair_record})
                if repair_record and repair_record["status"] == "repaired":
                    repaired_visual_base = _strip_retake_notes(attempt_prompt)
                    if (dialogue_timing is not None and ref_images and isinstance(audit_settings,dict)
                            and audit_settings.get('backend')=='nanobanana'):
                        # A corrected camera prompt must not retain an image conditioned on the rejected view.
                        auditor=comfy_nodes.NODE_CLASS_MAPPINGS['NanoBananaH3Transform']
                        manifest.update(status='preparing_shot_reference', current_segment=segment.index,
                                        current_attempt=attempt + 1)
                        _atomic_json(project/'manifest.json',manifest)
                        try:
                            repaired_reference,receipt=auditor.prepare_shot_reference(
                                audit_settings['provider'],_flatten_image_tensors(ref_images),prompt,
                                attempt_prompt,project/'shot_references')
                        except Exception as exc:
                            manifest.update(status='error', error={'stage':'shot_reference',
                                            'segment_index':segment.index, 'attempt':attempt + 1,
                                            'exception_type':type(exc).__name__})
                            _atomic_json(project/'manifest.json',manifest)
                            raise
                        if receipt is not None and receipt['signature']!=prepared_reference_key:
                            ref_items,ref_blocks=_prepare_references(vae,audio_vae,width,height,
                                max(item.raw_frames for item in segments),ref_image_size,
                                {next(iter(ref_images)):repaired_reference},ref_videos,ref_video_audios,segment_ref_audios)
                            prepared_reference_key=receipt['signature']
                            manifest.setdefault('segment_references',{})[str(segment.index)]={
                                'image_file':'shot_references/'+receipt['image_file'],
                                'image_sha256':receipt['image_sha256'],'subject_ids':receipt['subject_ids'],
                                'passed':True,'camera_setup':receipt.get('camera_setup','')}
                            _atomic_json(project/'manifest.json',manifest)
                        manifest['status']='sampling'
                        _atomic_json(project/'manifest.json',manifest)
                attempt_visual_prompt = attempt_prompt

                latent, _ = h3._empty_av_latent(width, height, segment.raw_frames)
                if segment.context_frames:
                    if previous is None:
                        raise ValueError("a previous H3 AV latent is required for this continuation segment")
                if first_frame is not None or not ref_items:
                    # Keyframe conditioning is applied only to the opening;
                    # continuation segments are guided by the preceding AV latent.
                    opening = first_frame if segment.index == 0 else None
                    conditioning, latent = h3.MiniMaxH3ImageToVideo.execute(
                        clip, vae, attempt_prompt, width, height, segment.raw_frames,
                        first_frame=opening)
                else:
                    conditioning = _conditioning(clip, attempt_prompt, ref_items, ref_blocks)
                if segment.context_frames:
                    conditioning = _add_continuation_guide(
                        conditioning, guide)
                guider = custom_sampler.BasicGuider.execute(model, conditioning)[0]
                noise = custom_sampler.RandomNoise.execute(candidate_seed)[0]
                sampled = custom_sampler.SamplerCustomAdvanced.execute(noise, guider, sampler, sigmas, latent)[0]
                segment_has_dialogue = has_tagged_dialogue(attempt_prompt)
                # Rebuild audio with the clean base H3 model for every segment,
                # including deliberately speech-free reaction segments.  Skipping
                # those gaps allowed LoRA audio artefacts or invented speech to be
                # fed into the next continuation and compound into late-video
                # echo/howling.  Video remains frozen inside AudioRefine.
                candidate = _refine_segment_audio(
                    audio_refine_model,
                    conditioning,
                    {"samples": sampled["samples"]},
                    candidate_seed,
                    audio_refine_steps,
                    audio_refine_denoise,
                )
                audio_sources[attempt] = ("refined" if audio_refine_model is not None and int(audio_refine_steps) > 0 else "original")
                cause_local_result = None
                # A short H3 line can be spoken correctly and then restart or
                # drift into invented speech.  Shorten both streams only when
                # two ASR contexts prove a complete line and a safe boundary.
                # The next segment then conditions on this delivered tail.
                if (attempt >= candidate_attempt_limit and dialogue_timing is not None
                        and local_audio_audit.config_contract().get('enabled')):
                    cause_evidence = attempt_dir / f"segment_{segment.index:04d}_attempt_{attempt + 1}.cause-audio.json"
                    delivered = _delivered_audio(vae_decode_audio(audio_vae, candidate), segment)
                    cause_verdict = local_audio_audit.audit(delivered, local_prompt, cause_evidence, locate_repeats=True)
                    cutoff = local_audio_audit.repeated_speech_cutoff(cause_verdict, local_prompt)
                    cutoff_kind = 'repeated_speech'
                    if cutoff is None:
                        cutoff = local_audio_audit.unprescribed_speech_cutoff(cause_verdict, local_prompt)
                        cutoff_kind = 'unprescribed_speech'
                    if cutoff is not None:
                        trim_seconds = (local_audio_audit.natural_tail_trim_seconds(
                            cause_verdict, local_prompt, cutoff, min_reaction=0.125)
                            if cutoff_kind == 'unprescribed_speech'
                            else local_audio_audit.natural_tail_trim_seconds(
                                cause_verdict, local_prompt, cutoff))
                        if trim_seconds is not None:
                            trim_frames = max(1, min(
                                segment.output_frames, round(trim_seconds * FPS)))
                            minimum_frames = (segments[segment.index + 1].context_frames
                                              if segment.index + 1 < len(segments) else 1)
                            if trim_frames < minimum_frames:
                                trim_frames = None
                        if trim_seconds is not None and trim_frames is not None:
                            repaired_evidence = cause_evidence.with_name(
                                cause_evidence.stem + '.trimmed.json')
                            trimmed_delivered = _delivered_audio(
                                vae_decode_audio(audio_vae, candidate), segment, trim_frames)
                            trimmed_verdict = local_audio_audit.audit(
                                trimmed_delivered, local_prompt, repaired_evidence)
                            cause_local_result = _local_audio_verdict_result(trimmed_verdict)
                        else:
                            trim_frames = None
                        if trim_frames is not None and cause_local_result[0] is True:
                            audio_sources[attempt] = 'refined_tail_trimmed'
                            delivery_trim_frames[attempt] = trim_frames
                            manifest.setdefault('cause_recovery_audio_repairs', []).append({
                                'index': segment.index, 'attempt': attempt + 1,
                                'strategy': 'invalid_av_tail_trim_v3',
                                'cutoff_kind': cutoff_kind,
                                'cutoff_seconds': round(float(cutoff), 3),
                                'trim_frames': trim_frames,
                            })
                if audit_applicable and local_audio_enabled:
                    evidence = attempt_dir / f"segment_{segment.index:04d}_attempt_{attempt + 1}.audio.json"
                    audit_segment = _segment_with_delivery_frames(
                        segment, delivery_trim_frames.get(attempt))
                    refined_result = (cause_local_result if cause_local_result is not None else
                                      _audit_local_script_audio(candidate, audit_segment, audio_vae, local_prompt, evidence))
                    local_attempt_audio_result = refined_result
                    attempt_audio_result = refined_result
                    if (refined_result[0] is False and attempt not in delivery_trim_frames
                            and audio_refine_model is not None and int(audio_refine_steps) > 0):
                        original = {"samples": sampled["samples"]}
                        original_result = _audit_local_script_audio(original, segment, audio_vae, local_prompt,
                                              evidence.with_name(evidence.stem + '.original.json'))
                        refined_rank = _candidate_quality_rank(True, _audio_result_status(refined_result))
                        original_rank = _candidate_quality_rank(original_result[0] is not True, _audio_result_status(original_result))
                        if original_rank < refined_rank:
                            candidate, attempt_audio_result = original, original_result
                            local_attempt_audio_result = original_result
                            audio_sources[attempt] = 'original'
                        _atomic_json(evidence.with_name(evidence.stem + '.comparison.json'),
                                     {'refined': refined_result, 'original': original_result,
                                      'selected': audio_sources[attempt]})
                if (audit_applicable and isinstance(audit_settings, dict)
                        and audit_settings.get("backend") == "nanobanana"):
                    audit_segment = _segment_with_delivery_frames(
                        segment, delivery_trim_frames.get(attempt))
                    refined_result = _audit_delivered_audio(
                        candidate, audit_segment, audio_vae, local_prompt, audit_settings)
                    if cause_local_result is not None:
                        refined_result = _reconcile_repaired_audio_audits(
                            cause_local_result, refined_result)
                    else:
                        refined_result = _attach_local_audio_evidence(
                            local_attempt_audio_result, refined_result)
                    attempt_audio_result = refined_result
                    if (refined_result[0] is False and attempt not in delivery_trim_frames
                            and audio_refine_model is not None and int(audio_refine_steps) > 0):
                        original = {"samples": sampled["samples"]}
                        original_result = _audit_delivered_audio(original, segment, audio_vae, local_prompt, audit_settings)
                        refined_rank = _candidate_quality_rank(True, _audio_result_status(refined_result))
                        original_rank = _candidate_quality_rank(original_result[0] is not True, _audio_result_status(original_result))
                        selected_audio = "refined"
                        if original_rank < refined_rank:
                            candidate, attempt_audio_result = original, original_result
                            selected_audio = "original"
                        audio_sources[attempt] = selected_audio
                        _atomic_json(attempt_dir / f"segment_{segment.index:04d}_attempt_{attempt + 1}.audio-comparison.json",
                                     {"original": original_result, "refined": refined_result,
                                      "original_rank": original_rank, "refined_rank": refined_rank,
                                      "selected": selected_audio})
                candidate = _cpu_latent(candidate)
                if dialogue_timing is not None:
                    _save_segment(attempt_dir / f'segment_{segment.index:04d}_attempt_{attempt + 1}.safetensors',
                                  candidate, {'index': segment.index, 'attempt': attempt,
                                              'raw_frames': segment.raw_frames,
                                              'context_frames': segment.context_frames,
                                              'output_frames': segment.output_frames,
                                              'prompt_sha256': prompt_hash})
                return candidate
            def report_audit(attempt, failed, status):
                manifest["status"] = "segment_auditing"
                manifest["current_segment"] = segment.index
                manifest["segment_audits"].append({"index": segment.index, "attempt": attempt, "status": status,
                                                   "failed": bool(failed), "context": context_key})
                _atomic_json(project / "audit_attempts" / f"segment_{segment.index:04d}_attempt_{attempt + 1}.verdict.json",
                             {"index": segment.index, "attempt": attempt + 1, "status": status, "failed": bool(failed)})
                _atomic_json(project / "manifest.json", manifest)
                print(f"[H3 segment audit] {segment.index + 1}/{len(segments)} attempt {attempt + 1}: {status}")

            def save_best(value, attempt, failed, status):
                selected_seed = _segment_noise_seed(noise_seed, segment.index, attempt)
                selected_lineage = _segment_lineage(generation_fingerprint, predecessor_lineage, segment, prompt_hash, attempt)
                record = {"index": segment.index, "attempt": attempt, "failed": bool(failed),
                          "status": status, "context": context_key}
                metadata = {
                    "schema": SCHEMA_VERSION,
                    "segment_audit_attempt": attempt,
                    "segment_audit_policy": ("cause-recovery-v2" if dialogue_timing is not None else "advisory-v2") if audit_applicable else "not_applicable",
                    "segment_audit_status": status,
                    "segment_audit_failed": bool(failed),
                    "index": segment.index,
                    "raw_frames": segment.raw_frames,
                    "context_frames": segment.context_frames,
                    "output_start": segment.output_start,
                    "output_frames": segment.output_frames,
                    "width": width,
                    "height": height,
                    "seed": selected_seed,
                    "prompt_sha256": prompt_hash,
                    "generation_fingerprint": generation_fingerprint,
                    "predecessor_lineage": predecessor_lineage,
                    "lineage": selected_lineage,
                    "has_tagged_dialogue": has_tagged_dialogue(local_prompt),
                    "audio_refine_attempted": bool(
                        audio_refine_model is not None and int(audio_refine_steps) > 0),
                    "audio_refine_applied": audio_sources.get(attempt) == "refined",
                    "selected_audio": audio_sources.get(attempt, "original"),
                    "delivered_trim_frames": delivery_trim_frames.get(attempt, ''),
                    "delivery_trim_policy": (
                        "complete-line-native-reaction-v1"
                        if attempt in delivery_trim_frames else ""),
                    "repeat_tail_freeze_seconds": "",
                }
                _save_segment(checkpoint, value, metadata)
                manifest.setdefault("selected_segment_audits", {})[str(segment.index)] = record
                manifest.setdefault("candidate_selection", {})[str(segment.index)] = {
                    "attempt": attempt + 1, "policy": "best including previous matching candidates",
                    "rank": _candidate_quality_rank(failed, status), "selected_audio": audio_sources.get(attempt, "original")}
                _atomic_json(project / "manifest.json", manifest)

            def recover_saved():
                nonlocal attempt_audio_result
                def inspect_visual(value, attempt, output_frames=None):
                    manifest['current_attempt'] = attempt + 1
                    inspect_segment = _segment_with_delivery_frames(segment, output_frames)
                    return _audit_segment_latent(
                        value, inspect_segment, vae, audio_vae, local_prompt, audit_images, audit_settings,
                        project / 'audit_attempts' / f'segment_{segment.index:04d}_attempt_{attempt + 1}.recovery.frames.png',
                        audio_result=(True, 'visual-only phase; audio checked separately'),
                        previous=previous, previous_segment=segments[segment.index-1] if segment.index else None,
                        visual_prompt=_saved_candidate_prompt(project, segment.index, attempt, local_prompt))
                manifest['status'] = 'segment_recovering'
                _atomic_json(project / 'manifest.json', manifest)
                value, metadata, audio_result, receipt = _recover_saved_repeated_speech_candidate(
                    project, segment, prompt_hash, audio_vae, local_prompt, audit_settings, inspect_visual,
                    allow_tail_trim=True,
                    min_output_frames=(segments[segment.index + 1].context_frames
                                       if segment.index + 1 < len(segments) else 1),
                    legacy_tail_freeze_seconds=old_metadata.get('repeat_tail_freeze_seconds'),
                    legacy_attempt=old_metadata.get('segment_audit_attempt'))
                manifest.setdefault('cause_recovery_audio_repairs', []).append(receipt)
                _atomic_json(project / 'manifest.json', manifest)
                if value is None:
                    return None
                attempt = int(metadata['segment_audit_attempt'])
                attempt_audio_result = audio_result
                audio_sources[attempt] = metadata['selected_audio']
                if metadata.get('delivered_trim_frames'):
                    delivery_trim_frames[attempt] = int(metadata['delivered_trim_frames'])
                return value, attempt, False, 'pass'

            manifest["status"] = "sampling"
            manifest["current_segment"] = segment.index
            manifest['current_attempt'] = old_attempt + 1
            _atomic_json(project / "manifest.json", manifest)
            def current_audit_segment():
                return _segment_with_delivery_frames(
                    segment, delivery_trim_frames.get(manifest['current_attempt'] - 1))
            try:
                candidate, audit_attempt = _generate_verified_segment(
                    generate_candidate,
                    lambda value: (_combine_three_mode_audits(_audit_three_mode_segment(
                        value, previous, segments[segment.index-1] if segment.index else None,
                        current_audit_segment(), vae, local_prompt, audit_images_for_segment, audit_settings,
                        project / "audit_attempts" / f"segment_{segment.index:04d}_attempt_{manifest['current_attempt']}.boundary.png"), attempt_audio_result)
                        if three_mode else _audit_segment_latent(
                        value, current_audit_segment(), vae, audio_vae, local_prompt, audit_images, audit_settings,
                        project / "audit_attempts" / f"segment_{segment.index:04d}_attempt_{manifest['current_attempt']}.frames.png",
                        audio_result=attempt_audio_result, previous=previous,
                        previous_segment=segments[segment.index-1] if segment.index else None,
                        visual_prompt=attempt_visual_prompt)),
                    report_audit, audit_applicable, max_attempts=total_candidate_attempt_limit,
                    initial_feedback=segment_feedback, start_attempt=start_attempt,
                    initial_candidate=old_candidate, initial_attempt=old_attempt,
                    initial_verdict=(old_record["failed"], old_record["status"]) if old_record else None,
                    on_best=save_best, candidate_context=lambda: context_key,
                    require_verified=dialogue_timing is not None and audit_applicable,
                    allow_unverified=dialogue_timing is not None and audit_applicable,
                    recover=recover_saved if dialogue_timing is not None and audit_applicable else None,
                    recover_first=_checkpoint_needs_av_sync_recovery(old_metadata))
            except Exception:
                if manifest.get('status') != 'error':
                    manifest["status"] = "segment_check_failed"
                _atomic_json(project / "manifest.json", manifest)
                raise
            selected = next((row for row in reversed(manifest["segment_audits"])
                             if row["index"] == segment.index and row["attempt"] == audit_attempt), None)
            manifest.setdefault("selected_segment_audits", {})[str(segment.index)] = selected
            manifest.setdefault("candidate_selection", {})[str(segment.index)] = {
                "attempt": audit_attempt + 1,
                "policy": "lowest-quality-issue-rank; earliest on ties",
                "rank": _candidate_quality_rank(selected["failed"], selected["status"]) if selected else None,
                "selected_audio": audio_sources.get(audit_attempt, "original")}
            previous = candidate
            previous_delivery_frames = delivery_trim_frames.get(audit_attempt, segment.output_frames)
            segment_seed = _segment_noise_seed(noise_seed, segment.index, audit_attempt)
            lineage = _segment_lineage(generation_fingerprint, predecessor_lineage, segment, prompt_hash, audit_attempt)
            segment_has_dialogue = has_tagged_dialogue(local_prompt)
            save_best(previous, audit_attempt, bool(selected and selected["failed"]),
                      selected["status"] if selected else "not_applicable")
            completed += 1
            manifest_segment = _manifest_segment(
                segment, "reused_best_at_limit" if start_attempt >= candidate_attempt_limit else "generated",
                checkpoint, prompt_hash, segment_seed, lineage)
            manifest_segment['delivered_output_frames'] = previous_delivery_frames
            manifest["segments"].append(manifest_segment)
            _atomic_json(project / "manifest.json", manifest)
            predecessor_lineage = lineage
            if segment.index == stop_after_segment:
                break

        if previous is None:
            raise RuntimeError("MiniMax H3 Long Video did not produce a latent")
        segments = segments[:completed]
        manifest["segments"] = manifest["segments"][:completed]
        for key in ("selected_segment_audits", "candidate_selection", "segment_references"):
            manifest[key] = {index: value for index, value in manifest.get(key, {}).items() if int(index) < completed}
        segment_paths = segment_paths[:completed]
        local_prompts = local_prompts[:completed]
        manifest["completed_output_frames"] = sum(
            int(item.get('delivered_output_frames', item['output_frames']))
            for item in manifest["segments"])
        manifest["master_is_partial"] = completed < requested_segment_count
        manifest["status"] = "decoding"
        _atomic_json(project / "manifest.json", manifest)
        previous = None
        latent = None
        conditioning = None
        guider = None
        noise = None
        sampled = None
        cached = None
        ref_items = None
        ref_blocks = None
        _write_master(
            master_path, segment_paths, segments, vae, audio_vae,
            width, height, crf, local_prompts,
            preserve_generated_audio=manifest["preserve_generated_audio"])

        audit_status, failed_segments, missing_segments = _final_audit_summary(
            manifest.get('selected_segment_audits', {}), completed, audit_applicable)
        manifest["visual_cast_audit"] = {
            "status": audit_status, "mode": "advisory-v2",
            "failed_segments": failed_segments, "missing_segments": missing_segments}
        last_latent, _ = _load_segment(segment_paths[-1])
        last_latent = _cpu_latent(last_latent)
        manifest["status"] = (
            ("partial_complete" if manifest["master_is_partial"] else "complete")
            + ("_with_audit_failure" if failed_segments else "")
        )
        manifest['quality_status'] = (
            'pass' if audit_status == 'pass' and not manifest['master_is_partial']
            else ('not_applicable' if audit_status == 'not_applicable' else 'needs_review'))
        manifest["master"] = master_path.name
        _atomic_json(project / "manifest.json", manifest)

        model = None
        audio_refine_model = None
        clip = None
        sampler = None
        sigmas = None
        initial_latent = None
        initial_video = None
        ref_images = None
        ref_videos = None
        ref_video_audios = None
        ref_audios = None

        video = InputImpl.VideoFromFile(str(master_path))
        preview = ui.PreviewVideo([ui.SavedResult(master_path.name, relative_folder, io.FolderType.output)])
        return io.NodeOutput(video, last_latent, str(master_path), completed, ui=preview)


class MiniMaxH3LongLatentUpscale(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3LongLatentUpscale",
            display_name="MiniMax H3 Long Latent Upscale & Assemble",
            category="sampling/minimax",
            description="Upscale Long H3 AV latent checkpoints one at a time and assemble a new MP4 without loading the full movie into memory.",
            is_output_node=True,
            inputs=[
                io.Vae.Input("vae"),
                io.Vae.Input("audio_vae"),
                io.String.Input("source_path", default="h3_long_video",
                                tooltip="Output-relative Long H3 bundle folder, manifest.json, or master.mp4. An absolute path is accepted only inside ComfyUI's output folder."),
                io.Combo.Input("model_name", options=_upscaler_models()),
                io.Int.Input("target_width", default=1344, min=32, max=8192, step=32),
                io.Int.Input("target_height", default=768, min=32, max=8192, step=32),
                io.Int.Input("align", default=2, min=2, max=64, step=2,
                             tooltip="Target latent-grid alignment. 2 preserves typical Long H3 dimensions that are multiples of 32 pixels."),
                io.Combo.Input("device", options=["cuda", "cpu"], default="cuda"),
                io.Combo.Input("precision", options=["fp32", "fp16", "bf16"], default="fp16"),
                io.String.Input("output_cache_name", default="h3_long_upscaled",
                                tooltip="Output-relative bundle folder for upscaled checkpoints and master.mp4. Save Video patterns are supported."),
                io.Boolean.Input("resume", default=False,
                                 tooltip="Reuse upscaled checkpoints whose source and settings still match."),
                io.Int.Input("reroll_from_segment", default=-1, min=-1, max=999, step=1,
                             tooltip="With resume enabled: -1 processes only missing or incompatible segments; N regenerates N onward."),
                io.Int.Input("crf", default=18, min=0, max=51, step=1, advanced=True),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
            outputs=[
                io.Video.Output(display_name="video"),
                io.Latent.Output(display_name="last_latent"),
                io.String.Output(display_name="master_path"),
                io.Int.Output(display_name="segment_count"),
            ],
        )

    @classmethod
    def execute(cls, vae, audio_vae, source_path, model_name, target_width,
                target_height, align, device, precision, output_cache_name,
                resume, reroll_from_segment, crf):
        hidden = cls.hidden
        prompt = hidden.prompt if hidden is not None else None
        extra_pnginfo = hidden.extra_pnginfo if hidden is not None else None
        source_path = _expand_cache_name(source_path, prompt, extra_pnginfo)
        output_cache_name = _expand_cache_name(
            output_cache_name, prompt, extra_pnginfo)

        source, source_manifest_path = _source_bundle(source_path)
        with source_manifest_path.open("r", encoding="utf-8") as handle:
            source_manifest = json.load(handle)
        if source_manifest.get("fps", FPS) != FPS:
            raise ValueError("source Long H3 bundle must use 24 fps")
        if source_manifest.get("latent_format", "minimax_h3_av") != "minimax_h3_av":
            raise ValueError("source manifest is not a MiniMax H3 AV latent bundle")
        segments, source_checkpoints = _manifest_segments(source, source_manifest)

        model_path = _upscaler_model_path(model_name)
        upscaler_class = comfy_nodes.NODE_CLASS_MAPPINGS.get(
            "H3LatentUpscalerNodeResolution")
        if upscaler_class is None:
            raise ValueError(
                "install Comfyui_Minimax_h3_latent_Upscaler and restart ComfyUI")
        upscaler = upscaler_class()

        project, master_path, relative_folder = _output_paths(
            output_cache_name, resume, target_width, target_height)
        if project == source:
            raise ValueError("output_cache_name must be different from the source bundle")
        checkpoints = [
            project / "latents" / "segment_{:04d}.safetensors".format(segment.index)
            for segment in segments
        ]
        manifest = {
            "schema": UPSCALE_SCHEMA_VERSION,
            "status": "upscaling",
            "fps": FPS,
            "latent_format": "minimax_h3_av",
            "source": os.path.relpath(source, Path(folder_paths.get_output_directory()).resolve()),
            "source_schema": source_manifest.get("schema"),
            "source_width": source_manifest.get("width"),
            "source_height": source_manifest.get("height"),
            "length": source_manifest.get("length"),
            "requested_width": target_width,
            "requested_height": target_height,
            "align": align,
            "model_name": model_name,
            "device": device,
            "precision": precision,
            "segments": [],
        }
        _atomic_json(project / "manifest.json", manifest)

        actual_width = None
        actual_height = None
        completed = 0
        for segment, source_checkpoint, checkpoint in zip(
                segments, source_checkpoints, checkpoints):
            expected = _upscale_metadata(
                source_checkpoint, model_path, model_name, target_width,
                target_height, align, device, precision, segment)
            may_reuse = resume and (
                reroll_from_segment < 0 or segment.index < reroll_from_segment)
            status = "upscaled"
            latent = None
            upscaled = None
            if may_reuse and checkpoint.exists():
                cached, metadata = _load_segment(checkpoint)
                if _upscale_metadata_matches(metadata, expected):
                    upscaled = cached
                    status = "reused"
                elif reroll_from_segment >= 0:
                    raise ValueError(
                        "upscaled segment {} no longer matches; reroll from this segment or earlier".format(
                            segment.index))

            if upscaled is None:
                latent, _ = _load_segment(source_checkpoint)
                upscaled = upscaler.run(
                    latent, model_name, target_width, target_height,
                    align, device, precision)[0]
                _streams(upscaled)
                _save_segment(checkpoint, upscaled, expected)

            video_latent, audio_latent = _streams(upscaled)
            segment_width = video_latent.shape[-1] * 16
            segment_height = video_latent.shape[-2] * 16
            if actual_width is None:
                actual_width = segment_width
                actual_height = segment_height
            elif segment_width != actual_width or segment_height != actual_height:
                raise ValueError("upscaled H3 segments do not have a consistent resolution")

            completed += 1
            manifest["segments"].append({
                "index": segment.index,
                "status": status,
                "file": checkpoint.name,
                "source_file": source_checkpoint.name,
                "output_start": segment.output_start,
                "output_frames": segment.output_frames,
                "timeline_start": segment.output_start / FPS,
                "timeline_end": (segment.output_start + segment.output_frames) / FPS,
                "raw_frames": segment.raw_frames,
                "context_frames": segment.context_frames,
            })
            _atomic_json(project / "manifest.json", manifest)
            latent = None
            upscaled = None
            video_latent = None
            audio_latent = None

        if actual_width is None or actual_height is None:
            raise RuntimeError("MiniMax H3 Long Latent Upscale produced no segments")
        manifest["width"] = actual_width
        manifest["height"] = actual_height
        manifest["status"] = "decoding"
        _atomic_json(project / "manifest.json", manifest)
        upscaler = None
        _write_master(
            master_path, checkpoints, segments, vae, audio_vae,
            actual_width, actual_height, crf)
        last_latent, _ = _load_segment(checkpoints[-1])
        last_latent = _cpu_latent(last_latent)
        manifest["status"] = "complete"
        manifest["master"] = master_path.name
        _atomic_json(project / "manifest.json", manifest)

        video = InputImpl.VideoFromFile(str(master_path))
        preview = ui.PreviewVideo([
            ui.SavedResult(master_path.name, relative_folder, io.FolderType.output)
        ])
        return io.NodeOutput(
            video, last_latent, str(master_path), completed, ui=preview)


class MiniMaxH3LongUpscalePrepare(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3LongUpscalePrepare",
            display_name="MiniMax H3 Long Upscale Prepare",
            category="sampling/minimax/long upscale",
            description="Prepare a persistent upscale bundle for segment processing in an EasyUse For Loop.",
            not_idempotent=True,
            inputs=[
                io.String.Input("master_path", default="h3_long_video/master.mp4",
                                tooltip="Connect MiniMax H3 Long Reference Sampler's master_path, or enter a Long H3 bundle folder, manifest.json, or master.mp4 inside the ComfyUI output folder."),
                io.Boolean.Input("resume", default=False, advanced=True,
                                 tooltip="Continue the newest incomplete upscale bundle under this source and process only its missing segments. Use the same upscale workflow settings."),
            ],
            outputs=[
                LONG_H3_UPSCALE_JOB.Output("job"),
                io.Int.Output("segment_count"),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, master_path, resume=False):
        return time.time_ns()

    @classmethod
    def execute(cls, master_path, resume=False):
        source, manifest_path = _source_bundle(master_path)
        with manifest_path.open("r", encoding="utf-8") as handle:
            source_manifest = json.load(handle)
        if source_manifest.get("latent_format") != "minimax_h3_av":
            raise ValueError("source manifest is not a MiniMax H3 AV latent bundle")
        segments, source_checkpoints = _manifest_segments(source, source_manifest)

        source_width = source_manifest.get("width")
        source_height = source_manifest.get("height")
        if not isinstance(source_width, int) or not isinstance(source_height, int):
            raise ValueError("source Long H3 manifest has no valid resolution")
        output_root = Path(folder_paths.get_output_directory()).resolve()
        if resume:
            project, previous_manifest = _incomplete_loop_upscale_bundle(source)
            previous_entries = previous_manifest.get("segments")
            if not isinstance(previous_entries, list) or len(previous_entries) != len(segments):
                raise ValueError("incomplete Long H3 upscale bundle no longer matches the source timeline")
        else:
            output_cache_name = os.path.relpath(source / "upscale", output_root)
            project, _, _ = _output_paths(
                output_cache_name, False, source_width, source_height)
            previous_manifest = None
            previous_entries = None
        prompt_directory = project / "prompts"
        prompt_directory.mkdir(exist_ok=True)

        source_entries = source_manifest["segments"]
        job_segments = []
        all_job_segments = []
        output_entries = []
        for segment, source_checkpoint, source_entry in zip(
                segments, source_checkpoints, source_entries):
            seed = source_entry.get("seed", source_manifest.get("seed"))
            if not isinstance(seed, int):
                with safe_open(str(source_checkpoint), framework="pt", device="cpu") as handle:
                    checkpoint_metadata = handle.metadata() or {}
                try:
                    seed = int(checkpoint_metadata["seed"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError(
                        "source Long H3 segment {} has no seed metadata".format(segment.index))
            prompt_file = source_entry.get(
                "prompt_file", "prompts/segment_{:04d}.txt".format(segment.index))
            if not isinstance(prompt_file, str):
                raise ValueError("source Long H3 manifest has an invalid prompt filename")
            source_prompt = (source / prompt_file).resolve()
            if (not folder_paths.is_within_directory(str(source), str(source_prompt)) or
                    not source_prompt.is_file()):
                raise ValueError("source Long H3 prompt is missing: {}".format(prompt_file))
            with source_prompt.open("r", encoding="utf-8") as handle:
                prompt = handle.read()
            output_prompt = prompt_directory / "segment_{:04d}.txt".format(segment.index)
            _atomic_text(output_prompt, prompt)
            output_checkpoint = project / "latents" / "segment_{:04d}.safetensors".format(segment.index)
            source_size, source_mtime = _file_fingerprint(source_checkpoint)
            prompt_hash = _prompt_hash(prompt)
            item = {
                "index": segment.index,
                "raw_frames": segment.raw_frames,
                "context_frames": segment.context_frames,
                "output_start": segment.output_start,
                "output_frames": segment.output_frames,
                "seed": seed,
                "width": source_width,
                "height": source_height,
                "source_checkpoint": str(source_checkpoint),
                "output_checkpoint": str(output_checkpoint),
                "prompt_path": str(output_prompt),
            }
            all_job_segments.append(item)
            output_entry = {
                "index": segment.index,
                "status": "pending",
                "file": output_checkpoint.name,
                "source_file": source_checkpoint.name,
                "source_size": source_size,
                "source_mtime_ns": source_mtime,
                "prompt_file": "prompts/{}".format(output_prompt.name),
                "prompt_sha256": prompt_hash,
                "raw_frames": segment.raw_frames,
                "context_frames": segment.context_frames,
                "output_start": segment.output_start,
                "output_frames": segment.output_frames,
                "seed": seed,
                "timeline_start": segment.output_start / FPS,
                "timeline_end": (segment.output_start + segment.output_frames) / FPS,
            }
            if resume:
                previous = previous_entries[segment.index]
                if previous.get("index") != segment.index:
                    raise ValueError("incomplete Long H3 upscale bundle has invalid segment ordering")
                source_matches = all(previous.get(key) == output_entry[key] for key in (
                    "source_file", "source_size", "source_mtime_ns", "prompt_sha256",
                    "raw_frames", "context_frames", "output_start", "output_frames",
                ))
                if previous.get("status") == "saved" and source_matches and output_checkpoint.is_file():
                    with safe_open(str(output_checkpoint), framework="pt", device="cpu") as handle:
                        metadata = handle.metadata() or {}
                        keys = set(handle.keys())
                    expected = {
                        "upscale_schema": str(LOOP_UPSCALE_SCHEMA_VERSION),
                        "index": str(segment.index),
                        "raw_frames": str(segment.raw_frames),
                        "context_frames": str(segment.context_frames),
                        "output_start": str(segment.output_start),
                        "output_frames": str(segment.output_frames),
                        "width": str(previous.get("width")),
                        "height": str(previous.get("height")),
                    }
                    if {"video", "audio"}.issubset(keys) and all(
                            metadata.get(key) == value for key, value in expected.items()):
                        output_entry["status"] = "saved"
                        output_entry["width"] = previous["width"]
                        output_entry["height"] = previous["height"]
            if output_entry["status"] == "pending":
                job_segments.append(item)
            output_entries.append(output_entry)

        if resume and not job_segments:
            output_entries[-1]["status"] = "pending"
            job_segments.append(all_job_segments[-1])

        output_manifest = {
            "schema": LOOP_UPSCALE_SCHEMA_VERSION,
            "kind": "minimax_h3_long_ultimate_upscale",
            "status": "processing",
            "fps": FPS,
            "latent_format": "minimax_h3_av",
            "source": os.path.relpath(
                source, Path(folder_paths.get_output_directory()).resolve()),
            "source_schema": source_manifest.get("schema"),
            "source_width": source_width,
            "source_height": source_height,
            "length": source_manifest.get("length"),
            "segments": output_entries,
        }
        if previous_manifest is not None:
            if isinstance(previous_manifest.get("width"), int) and isinstance(previous_manifest.get("height"), int):
                output_manifest["width"] = previous_manifest["width"]
                output_manifest["height"] = previous_manifest["height"]
        _atomic_json(project / "manifest.json", output_manifest)
        job = {
            "project": str(project),
            "segment_count": len(job_segments),
            "total_segment_count": len(output_entries),
            "segments": job_segments,
        }
        return io.NodeOutput(job, len(job_segments))


class MiniMaxH3LongSegmentLoad(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3LongSegmentLoad",
            display_name="MiniMax H3 Long Segment Load",
            category="sampling/minimax/long upscale",
            description="Load one Long H3 latent and its local timeline prompt by EasyUse loop index.",
            inputs=[
                LONG_H3_UPSCALE_JOB.Input("job"),
                io.Int.Input("segment_index", default=0, min=0, max=9999, step=1),
            ],
            outputs=[
                io.Latent.Output("latent"),
                io.String.Output("prompt"),
                io.Int.Output("seed"),
                io.Int.Output("width"),
                io.Int.Output("height"),
                io.Int.Output("raw_frames"),
                LONG_H3_SEGMENT.Output("segment"),
            ],
        )

    @classmethod
    def execute(cls, job, segment_index):
        segments = job.get("segments") if isinstance(job, dict) else None
        if not isinstance(segments, list) or not 0 <= segment_index < len(segments):
            raise ValueError("segment_index is outside the prepared Long H3 job")
        segment = segments[segment_index]
        index = segment.get("index")
        if not isinstance(index, int):
            raise ValueError("prepared Long H3 job has an invalid segment index")
        checkpoint = Path(segment["source_checkpoint"])
        prompt_path = Path(segment["prompt_path"])
        if not checkpoint.is_file() or not prompt_path.is_file():
            raise ValueError("prepared Long H3 source files are missing for segment {}".format(index))
        latent, _ = _load_segment(checkpoint)
        with prompt_path.open("r", encoding="utf-8") as handle:
            prompt = handle.read()
        token = dict(segment)
        token["project"] = job["project"]
        token["segment_count"] = job["total_segment_count"]
        return io.NodeOutput(
            latent, prompt, segment["seed"], segment["width"],
            segment["height"], segment["raw_frames"], token)


class MiniMaxH3LongSegmentSave(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3LongSegmentSave",
            display_name="MiniMax H3 Long Segment Save",
            category="sampling/minimax/long upscale",
            description="Save one processed H3 AV latent to its persistent upscale bundle and pass a small progress value to EasyUse For Loop End.",
            inputs=[
                io.Latent.Input("latent"),
                LONG_H3_SEGMENT.Input("segment"),
            ],
            outputs=[LONG_H3_UPSCALE_PROGRESS.Output("progress")],
        )

    @classmethod
    def execute(cls, latent, segment):
        video, audio = _streams(latent)
        expected_tokens = h3.video_latent_t(segment["raw_frames"])
        if video.shape[2] != expected_tokens:
            raise ValueError(
                "processed segment {} changed its temporal length".format(segment["index"]))
        expected_audio_tokens = round(
            segment["raw_frames"] / FPS * h3.AUDIO_LATENT_FPS)
        if audio.shape[-1] != expected_audio_tokens:
            raise ValueError(
                "processed segment {} changed its audio length".format(segment["index"]))
        width = video.shape[-1] * 16
        height = video.shape[-2] * 16
        checkpoint = Path(segment["output_checkpoint"]).resolve()
        project = Path(segment["project"]).resolve()
        manifest_path = project / "manifest.json"
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        if (manifest.get("schema") != LOOP_UPSCALE_SCHEMA_VERSION or
                manifest.get("kind") != "minimax_h3_long_ultimate_upscale" or
                manifest.get("status") != "processing"):
            raise ValueError("processed Long H3 bundle has an incompatible manifest")
        _loop_upscale_source(project, manifest)
        entries = manifest.get("segments")
        index = segment["index"]
        if not isinstance(entries, list) or index >= len(entries) or entries[index].get("index") != index:
            raise ValueError("processed Long H3 manifest has invalid segment ordering")
        entry = entries[index]
        expected_checkpoint = (project / "latents" / entry["file"]).resolve()
        if (checkpoint != expected_checkpoint or
                checkpoint.parent != (project / "latents").resolve()):
            raise ValueError("processed segment output path is invalid")
        if entry.get("status") != "pending":
            raise ValueError("processed segment {} has already been saved".format(index))
        metadata = {
            "upscale_schema": LOOP_UPSCALE_SCHEMA_VERSION,
            "index": segment["index"],
            "raw_frames": segment["raw_frames"],
            "context_frames": segment["context_frames"],
            "output_start": segment["output_start"],
            "output_frames": segment["output_frames"],
            "width": width,
            "height": height,
        }
        _save_segment(checkpoint, latent, metadata)

        existing_width = manifest.get("width")
        existing_height = manifest.get("height")
        if existing_width is not None and (existing_width != width or existing_height != height):
            raise ValueError("processed H3 segments do not have a consistent resolution")
        manifest["width"] = width
        manifest["height"] = height
        entries[index]["status"] = "saved"
        entries[index]["width"] = width
        entries[index]["height"] = height
        _atomic_json(manifest_path, manifest)
        progress = {
            "project": str(project),
            "segment_count": segment["segment_count"],
            "last_index": index,
        }
        return io.NodeOutput(progress)


class MiniMaxH3LongUpscaleAssemble(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3LongUpscaleAssemble",
            display_name="MiniMax H3 Long Upscale Assemble",
            category="sampling/minimax/long upscale",
            description="Decode the processed segment checkpoints after the EasyUse loop and assemble one MP4.",
            is_output_node=True,
            inputs=[
                LONG_H3_UPSCALE_PROGRESS.Input("progress"),
                io.Vae.Input("vae"),
                io.Vae.Input("audio_vae"),
                io.Int.Input("crf", default=18, min=0, max=51, step=1, advanced=True),
            ],
            outputs=[
                io.Video.Output("video"),
                io.Latent.Output("last_latent"),
                io.String.Output("master_path"),
                io.Int.Output("segment_count"),
            ],
        )

    @classmethod
    def execute(cls, progress, vae, audio_vae, crf):
        project = Path(progress["project"]).resolve()
        manifest_path = project / "manifest.json"
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        if (manifest.get("schema") != LOOP_UPSCALE_SCHEMA_VERSION or
                manifest.get("kind") != "minimax_h3_long_ultimate_upscale"):
            raise ValueError("processed Long H3 bundle has an incompatible manifest")
        output_root, _ = _loop_upscale_source(project, manifest)
        entries = manifest.get("segments")
        count = progress.get("segment_count")
        if not isinstance(entries, list) or not isinstance(count, int) or len(entries) != count:
            raise ValueError("processed Long H3 manifest has an invalid segment count")
        segments = []
        checkpoints = []
        for index, entry in enumerate(entries):
            if entry.get("index") != index or entry.get("status") != "saved":
                raise ValueError("Long H3 segment {} has not been processed".format(index))
            checkpoint = (project / "latents" / entry["file"]).resolve()
            if checkpoint.parent != (project / "latents").resolve() or not checkpoint.is_file():
                raise ValueError("processed Long H3 checkpoint is missing: {}".format(entry["file"]))
            prompt = (project / entry["prompt_file"]).resolve()
            if (not folder_paths.is_within_directory(str(project), str(prompt)) or
                    not prompt.is_file()):
                raise ValueError("processed Long H3 prompt is missing: {}".format(entry["prompt_file"]))
            segments.append(Segment(
                index, int(entry["raw_frames"]), int(entry["context_frames"]),
                int(entry["output_start"]), int(entry["output_frames"])))
            checkpoints.append(checkpoint)
        width = manifest.get("width")
        height = manifest.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            raise ValueError("processed Long H3 segments have no output resolution")

        manifest["status"] = "decoding"
        _atomic_json(manifest_path, manifest)
        master_path = project / "master.mp4"
        _write_master(master_path, checkpoints, segments, vae, audio_vae, width, height, crf)
        last_latent, _ = _load_segment(checkpoints[-1])
        last_latent = _cpu_latent(last_latent)
        manifest["status"] = "complete"
        manifest["master"] = master_path.name
        _atomic_json(manifest_path, manifest)
        relative_folder = os.path.relpath(project, output_root)
        video = InputImpl.VideoFromFile(str(master_path))
        preview = ui.PreviewVideo([
            ui.SavedResult(master_path.name, relative_folder, io.FolderType.output)
        ])
        return io.NodeOutput(video, last_latent, str(master_path), count, ui=preview)


class TimestampedSaveVideo(io.ComfyNode):
    """Save a video with a wall-clock timestamp instead of ComfyUI's counter."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="TimestampedSaveVideo",
            display_name="Save Video (YYYYMMDD-HHMMSS)",
            category="video",
            description=(
                "Saves a video as <prefix>_YYYYMMDD-HHMMSS.ext without the "
                "standard ComfyUI sequence counter. Existing files are never overwritten."
            ),
            inputs=[
                io.Video.Input("video", tooltip="The video to save."),
                io.String.Input(
                    "filename_prefix",
                    default="video/MiniMax_H3/video",
                    tooltip="Output-relative folder and filename prefix.",
                ),
                io.Combo.Input(
                    "format",
                    options=["auto", "mp4", "mkv", "webm"],
                    default="auto",
                ),
                io.Combo.Input(
                    "codec",
                    options=["auto", "h264", "av1"],
                    default="auto",
                ),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
            is_output_node=True,
            outputs=[io.Video.Output("video", tooltip="The input video, unchanged.")],
        )

    @classmethod
    def execute(cls, video, filename_prefix, format, codec):
        format_name = format
        codec_name = codec
        if format_name == "auto":
            format_name = "webm" if codec_name == "av1" else "mp4"

        clean_prefix = str(filename_prefix).rstrip(" _-")
        if not clean_prefix:
            clean_prefix = "video/MiniMax_H3/video"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dated_prefix = "{}_{}".format(clean_prefix, timestamp)

        width, height = video.get_dimensions()
        full_output_folder, filename, _, subfolder, _ = folder_paths.get_save_image_path(
            dated_prefix,
            folder_paths.get_output_directory(),
            width,
            height,
        )
        extension = Types.VideoContainer.get_extension(format_name)
        file = "{}.{}".format(filename, extension)
        output_path = os.path.join(full_output_folder, file)
        if os.path.exists(output_path):
            raise FileExistsError(
                "A timestamped video already exists for this second: {}. "
                "Wait one second and run again.".format(output_path)
            )

        saved_metadata = None
        if not args.disable_metadata:
            metadata = {}
            hidden = getattr(cls, "hidden", None)
            extra_pnginfo = getattr(hidden, "extra_pnginfo", None)
            prompt = getattr(hidden, "prompt", None)
            if extra_pnginfo is not None:
                metadata.update(extra_pnginfo)
            if prompt is not None:
                metadata["prompt"] = prompt
            if metadata:
                saved_metadata = metadata

        video.save_to(
            output_path,
            format=Types.VideoContainer(format_name),
            codec=Types.VideoCodec(codec_name),
            metadata=saved_metadata,
        )
        return io.NodeOutput(
            video,
            ui=ui.PreviewVideo([
                ui.SavedResult(file, subfolder, io.FolderType.output)
            ]),
        )


class MiniMaxH3LongVideoExtension(ComfyExtension):
    @override
    async def get_node_list(self):
        return [
            MiniMaxH3CloudPrompt,
            MiniMaxH3LongPromptPlanner,
            MiniMaxH3LongResumePromptPlan,
            MiniMaxH3LongReferenceSampler,
            MiniMaxH3LongLatentUpscale,
            MiniMaxH3LongUpscalePrepare,
            MiniMaxH3LongSegmentLoad,
            MiniMaxH3LongSegmentSave,
            MiniMaxH3LongUpscaleAssemble,
            TimestampedSaveVideo,
        ]


async def comfy_entrypoint():
    from .resume_api import register_routes
    register_routes()
    return MiniMaxH3LongVideoExtension()
