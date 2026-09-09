# SPDX-License-Identifier: GPL-3.0-only
# Portions of the MiniMax H3 reference conditioning were adapted and modified
# from ComfyUI's built-in MiniMax H3 implementation in 2026.

import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import time
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import av
import torch
from PIL import Image
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

from .timeline import (
    FPS,
    PROMPT_PLAN_SCHEMA_VERSION,
    Segment,
    build_prompt_plan,
    count_timeline_dialogue_turns,
    dialogue_duration_frames,
    has_tagged_dialogue,
    plan_segments,
    prompt_plan_prompts,
    slice_prompt,
)


SCHEMA_VERSION = 25
SEGMENT_SEED_STRATEGY = "splitmix64-per-segment-v1"
AUDIO_CONTINUITY_STRATEGY = "auto-duration-dialogue-plus-h3-bgm-v8"
AUDIO_SEAM_CROSSFADE_SECONDS = 0.125
AUDIO_REFINE_STRATEGY = "frozen-video-partial-audio-denoise-v1"
CAST_AUDIT_MAX_RETRIES = 5
CAST_AUDIT_MAX_TOKENS = 256
_QWEN_AUDIT_MODULE = None
UPSCALE_SCHEMA_VERSION = 1
LOOP_UPSCALE_SCHEMA_VERSION = 2

LONG_H3_PROMPT_PLAN = io.Custom("MINIMAX_H3_LONG_PROMPT_PLAN")
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

    for image in (ref_images or {}).values():
        if image is None:
            continue
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


def _add_continuation_guide(conditioning, previous, context_frames):
    video, audio = _streams(previous)
    video_steps = h3.video_latent_t(context_frames)
    audio_steps = round(context_frames / FPS * h3.AUDIO_LATENT_FPS)
    if video.shape[2] < video_steps or audio.shape[-1] < audio_steps:
        raise ValueError(
            "the previous H3 AV latent is shorter than context_frames")
    keyframes = list(conditioning[0][1].get("minimax_keyframes", []))
    keyframes.append({
        "resolved_frame_index": 0,
        "latent": video[:, :, -video_steps:].detach().clone(),
        # Audio and video are one synchronized H3 state.  Zeroing only the audio
        # half creates an out-of-distribution boundary and can turn a voice
        # metallic/echoey in the middle of an utterance.  Preserve the exact AV
        # tail, then reconcile the two VAE decodes during final stitching.
        "audio_latent": audio[..., -audio_steps:].detach().clone(),
    })
    return node_helpers.conditioning_set_values(
        conditioning, {"minimax_keyframes": keyframes})


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
                            ref_video_audios, ref_audios):
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
    digest = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for name, value in (
            ("sigmas", sigmas),
            ("initial_latent", initial_latent),
            ("ref_images", ref_images),
            ("ref_videos", ref_videos),
            ("ref_video_audios", ref_video_audios),
            ("ref_audios", ref_audios)):
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
        for key in sorted(value):
            images.extend(_flatten_image_tensors(value[key]))
    return images


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


def _audit_segment_latent(candidate, segment, vae, audio_vae, prompt, ref_images, settings, evidence_path=None):
    # Check exactly the delivered interval, excluding predecessor context audio.
    if isinstance(settings, dict) and settings.get("backend") == "nanobanana":
        try:
            auditor = comfy_nodes.NODE_CLASS_MAPPINGS["NanoBananaH3Transform"]
            audio = vae_decode_audio(audio_vae, candidate)
            rate = int(audio["sample_rate"])
            start = round(segment.context_frames / FPS * rate)
            count = round(segment.output_frames / FPS * rate)
            audio = dict(audio, waveform=audio["waveform"][..., start:start + count])
            passed, detail = auditor.audit_dialogue_audio(settings["provider"], audio, prompt)
            print("[H3 segment audio audit] " + str(segment.index) + ": " + detail)
            if not passed:
                return [segment.index], "fail: dialogue audio: " + detail
        except comfy.model_management.InterruptProcessingException:
            raise
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError):
            return [segment.index], "unavailable: dialogue audio inspection failed"

    video, _ = _streams(candidate)
    images = vae.decode(video)
    if images.ndim == 5:
        images = images.reshape(-1, *images.shape[-3:])
    if images.shape[0] < segment.raw_frames:
        raise ValueError("segment audit: VAE returned too few frames")
    duration = segment.output_frames / FPS
    offsets = (0.5, duration / 2, max(0.5, duration - 0.5))
    frames = []
    for offset in offsets:
        index = segment.context_frames + min(segment.output_frames - 1, round(offset * FPS))
        array = (images[index, ..., :3] * 255).clamp(0, 255).to(device="cpu", dtype=torch.uint8).numpy()
        frames.append(Image.fromarray(array))
    del images, video
    width = min(768, frames[0].width)
    height = round(frames[0].height * width / frames[0].width)
    sheet = Image.new("RGB", (width * 2, height * 2), "black")
    for i, frame in enumerate(frames):
        sheet.paste(frame.resize((width, height), Image.Resampling.LANCZOS), ((i % 2) * width, (i // 2) * height))
    if evidence_path is not None:
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(evidence_path)
    tensor = torch.from_numpy(__import__("numpy").asarray(sheet).copy()).float().div(255).unsqueeze(0)
    return _audit_closed_cast_video(None, [segment], prompt, ref_images, settings,
                                   {segment.index: prompt}, {segment.index: tensor})


def _retry_generation_prompt(local_prompt, audit_feedback):
    if not audit_feedback:
        return local_prompt
    prefix = "fail: dialogue audio:"
    if audit_feedback.startswith(prefix):
        # Interpret verdict issues, never condition generation on the rejected transcript.
        try:
            detail = json.loads(audit_feedback[len(prefix):].strip())
            issues = []
            for key in ("verdict", "acoustic"):
                verdict = detail.get(key)
                if isinstance(verdict, dict) and verdict.get("pass") is False:
                    issues.extend(verdict.get("issues") or [])
            kinds = {issue.get("kind") for issue in issues if isinstance(issue, dict)}
        except (ValueError, TypeError, AttributeError):
            kinds = set()
        corrections = []
        if "repeated_speech" in kinds:
            corrections.append("Deliver each prescribed clause once; never restart or echo it. "
                               "Do not replay speech from the preceding audio guide. "
                               "When the prescribed line is short, leave the remaining time without human speech.")
        if "missing_speech" in kinds:
            corrections.append("Start the prescribed utterance promptly and use a natural, brisk pace "
                               "so every word, including its ending, finishes inside this segment. "
                               "Do not abbreviate or split it across the boundary.")
        if "changed_words" in kinds:
            corrections.append("Follow the local tagged text verbatim in the established reading; "
                               "do not substitute, paraphrase, or invent words.")
        if kinds & {"overlapping_speech", "wrong_speaker"}:
            corrections.append("Only the speaker assigned to each local turn may speak, "
                               "with a single voice and no overlapping voices from other characters.")
        if not corrections:
            corrections.append("Render the complete local tagged utterance in its prescribed order "
                               "and reading, without extra words or repeated clauses.")
        return local_prompt + (
            "\n[AUDIO RETAKE CORRECTION — highest priority] " + " ".join(corrections)
            + " Preserve repetitions explicitly written in the authoritative dialogue. "
            "After the final prescribed syllable, keep all human voices silent. "
            "Preserve the original music policy, source identities, approved preceding "
            "visual continuity, and all unaffected scene content.")
    return local_prompt + (
        "\n[VISUAL RETAKE CORRECTION — highest priority] Correct the observed "
        "visual mismatch below while preserving the approved preceding continuity, "
        "source identities, story and dialogue ownership. Do not speak this report. "
        + audit_feedback.replace("<d>", "[reported dialogue]").replace("</d>", "[/reported dialogue]")[-1600:])


def _generate_verified_segment(generate, audit, report, enabled, max_attempts=3, initial_feedback=""):
    """Keep generated content; quality inspection is advisory, not a gate."""
    previous = None
    feedback = initial_feedback
    for attempt in range(max(1, int(max_attempts)) if enabled else 1):
        try:
            candidate = generate(attempt, feedback)
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception:
            raise  # Real generation failures must remain visible.
        previous = candidate
        if not enabled:
            return candidate, attempt
        try:
            failed, status = audit(candidate)
        except comfy.model_management.InterruptProcessingException:
            raise
        except Exception as exc:
            failed, status = True, 'unavailable: ' + type(exc).__name__
        report(attempt, failed, status)
        if not failed and status == 'pass':
            return candidate, attempt
        if status.startswith('unavailable:'):
            return candidate, attempt  # Regeneration cannot repair the inspector.
        feedback = status
    return previous, attempt  # Preserve the final candidate with its failed verdict.


def _legacy_strict_segment_gate(generate, audit, report, enabled, max_attempts=3, initial_feedback=""):
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
    return all(metadata.get(key) == value for key, value in expected.items())


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
        if attempt not in (0, 1):
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
        predecessor_lineage = lineage


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
        latent, _ = _load_segment(checkpoint)
        audio = vae_decode_audio(audio_vae, latent)
        raw_samples = round(segment.raw_frames / FPS * sample_rate)
        context_samples = round(segment.context_frames / FPS * sample_rate)
        output_start = round(segment.output_start / FPS * sample_rate)
        output_end = round(
            (segment.output_start + segment.output_frames) / FPS * sample_rate)
        output_samples = output_end - output_start
        waveform = _normalized_audio_waveform(audio, sample_rate, raw_samples)
        context_audio = waveform[..., :context_samples]
        post_context = waveform[..., context_samples:raw_samples]
        if preserve_generated_audio:
            # Ordinary H3 prompts can request music, ambience and sound effects
            # without any tagged speech. Keep them, aligned with video frames;
            # grid padding is cropped, never tempo-compressed into the story.
            output_audio = post_context[..., :output_samples]
        else:
            # Preserve the existing dialogue-only policy for legacy workflows.
            output_audio = (
                _tempo_fit_audio(post_context, output_samples, sample_rate)
                if is_active else torch.zeros(
                    (post_context.shape[-2], output_samples), dtype=torch.float32)
            )
        if output_audio.shape[-1] < output_samples:
            output_audio = torch.nn.functional.pad(
                output_audio, (0, output_samples - output_audio.shape[-1]))
        if chunks and context_samples:
            chunks[-1] = _blend_matching_audio_context(
                chunks[-1], context_audio, sample_rate)
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
                latent, _ = _load_segment(checkpoint)
                video, _ = _streams(latent)
                images = vae.decode(video)
                if images.ndim == 5:
                    images = images.reshape(
                        -1, images.shape[-3], images.shape[-2], images.shape[-1])
                if images.shape[0] < segment.raw_frames:
                    raise ValueError(
                        "video VAE decoded fewer frames than the H3 segment requires")
                images = images[:segment.raw_frames]
                output_images = images[
                    segment.context_frames:segment.context_frames + segment.output_frames]
                output_start = round(segment.output_start / FPS * sample_rate)
                output_end = round((segment.output_start + segment.output_frames) / FPS * sample_rate)
                output_audio = master_audio[:, output_start:output_end]

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
        expected_segments = plan_segments(
            manifest["length_input"], manifest["context_frames"],
            bool(has_initial_latent), manifest["max_raw_frames"])
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
                 prompt_plan=None, audio_refine_model=None, audio_refine_steps=4,
                 audio_refine_denoise=0.5, auto_dialogue_duration=False,
                 _audit_retry_index=0, _audit_reroll_floor=None,
                _audit_history=None, first_frame=None, segment_audit=True,
                preserve_input_prompt=False):
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
            initial_latent, ref_images, ref_videos, ref_video_audios, ref_audios)
        if first_frame is not None:
            if initial_latent is not None:
                raise ValueError("first_frame and initial_latent cannot both define the opening")
            digest = hashlib.sha256(generation_fingerprint.encode("ascii"))
            _update_runtime_hash(digest, first_frame)
            generation_fingerprint = digest.hexdigest()
        segments = plan_segments(
            length, context_frames, initial_latent is not None, max_raw_frames,
            exact_output_frames=(prompt_plan.get("requested_output_frames")
                                 if prompt_plan is not None else None))
        configured_segment_count = len(segments)
        dialogue_turns = count_timeline_dialogue_turns(prompt)
        if auto_dialogue_duration and prompt_plan is None and dialogue_turns:
            segments = plan_segments(
                dialogue_duration_frames(dialogue_turns, max_raw_frames),
                context_frames,
                initial_latent is not None,
                max_raw_frames,
            )
        requested_segment_count = len(segments)
        delivered_length = sum(segment.output_frames for segment in segments)
        project, master_path, relative_folder = _output_paths(cache_name, resume, width, height)
        resume_failure_feedback = {}
        if resume and (project / "manifest.json").exists():
            previous_manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
            if (previous_manifest.get("generation_fingerprint") == generation_fingerprint
                    and previous_manifest.get("status") == "segment_check_failed"):
                failures = previous_manifest.get("segment_audits") or []
                if failures and failures[-1].get("failed") is True:
                    last = failures[-1]
                    resume_failure_feedback[int(last["index"])] = last["status"]

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
                    delivered_length / FPS,
                    preserve_input_prompt=preserve_input_prompt,
                )
                for segment in segments
            ]
        recovery_floor = _audit_reroll_floor
        if recovery_floor is None and reroll_from_segment >= 0:
            recovery_floor = reroll_from_segment
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
            "configured_segment_count": configured_segment_count,
            "requested_segment_count": requested_segment_count,
            "max_raw_frames": max_raw_frames,
            "context_frames": context_frames,
            "seed": noise_seed,
            "segment_seed_strategy": SEGMENT_SEED_STRATEGY,
            "audio_continuity_strategy": AUDIO_CONTINUITY_STRATEGY,
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
            "segments": [],
        }
        _atomic_json(project / "manifest.json", manifest)

        audit_images = ref_images if first_frame is None else {"first_frame": first_frame}
        audit_applicable = (segment_audit and audit_images is not None and any(
            "CLOSED-CAST CONTINUITY" in item or "REFERENCE IDENTITY CONTINUITY" in item
            for item in local_prompts))
        audit_settings = _visual_audit_settings(graph_prompt)
        if audit_applicable and not audit_settings:
            print('[H3 warning] 区間検査設定なし。未確認として続行。')
            audit_applicable = False
        manifest["audit_mode"] = "before-next-segment-v1"
        manifest["segment_audits"] = []
        manifest["resume_failure_feedback"] = resume_failure_feedback

        previous = initial_latent
        generated = False
        completed = 0
        ref_items = None
        ref_blocks = None
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
            may_reuse = resume and not generated and (
                reroll_from_segment < 0 or segment.index < reroll_from_segment)
            if may_reuse and checkpoint.exists():
                cached, metadata = _load_segment(checkpoint)
                cached_attempt = int(metadata.get("segment_audit_attempt", "0"))
                if cached_attempt not in (0, 1, 2):
                    raise ValueError("invalid cached audit attempt")
                segment_seed = _segment_noise_seed(noise_seed, segment.index, cached_attempt)
                lineage = _segment_lineage(generation_fingerprint, predecessor_lineage, segment, prompt_hash, cached_attempt)
                approved = not audit_applicable or metadata.get("segment_audit_policy") == "before-next-segment-v1:pass"
                if approved and _metadata_matches(
                        metadata, segment, prompt_hash, width, height, segment_seed,
                        generation_fingerprint, predecessor_lineage, lineage):
                    previous = cached
                    completed += 1
                    manifest["segments"].append(_manifest_segment(
                        segment, "reused", checkpoint, prompt_hash, segment_seed, lineage))
                    _atomic_json(project / "manifest.json", manifest)
                    predecessor_lineage = lineage
                    continue
                if reroll_from_segment >= 0:
                    raise ValueError(
                        "segment {} no longer matches the current generation inputs; reroll from this segment or earlier".format(segment.index))

            generated = True
            if ref_items is None:
                ref_items, ref_blocks = _prepare_references(
                    vae, audio_vae, width, height,
                    max(item.raw_frames for item in segments), ref_image_size,
                    ref_images, ref_videos, ref_video_audios, ref_audios)
            repaired_visual_base = local_prompt
            def generate_candidate(attempt, audit_feedback):
                nonlocal repaired_visual_base
                manifest["status"] = "sampling"
                manifest["current_segment"] = segment.index
                manifest["current_attempt"] = attempt + 1
                _atomic_json(project / "manifest.json", manifest)
                candidate_seed = _segment_noise_seed(noise_seed, segment.index, attempt)
                repair_record = None
                if (audit_feedback and not audit_feedback.startswith("fail: dialogue audio:")
                        and isinstance(audit_settings, dict) and audit_settings.get("backend") == "nanobanana"):
                    auditor = comfy_nodes.NODE_CLASS_MAPPINGS["NanoBananaH3Transform"]
                    try:
                        attempt_prompt, repair_record = auditor.repair_video_segment_prompt(
                            audit_settings["provider"], _flatten_image_tensors(audit_images), repaired_visual_base, audit_feedback)
                    except comfy.model_management.InterruptProcessingException:
                        raise
                    except Exception as exc:
                        attempt_prompt = _retry_generation_prompt(repaired_visual_base, audit_feedback)
                        repair_record = {'status': 'unavailable', 'error_type': type(exc).__name__}
                    repaired_visual_base = attempt_prompt
                else:
                    attempt_prompt = _retry_generation_prompt(repaired_visual_base, audit_feedback)
                attempt_dir = project / "audit_attempts"
                attempt_dir.mkdir(exist_ok=True)
                _atomic_json(attempt_dir / f"segment_{segment.index:04d}_attempt_{attempt + 1}.json",
                             {"seed": candidate_seed, "prompt": attempt_prompt,
                              "previous_audit": audit_feedback, "visual_repair": repair_record})

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
                        conditioning, previous, segment.context_frames)
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
                return _cpu_latent(candidate)
            def report_audit(attempt, failed, status):
                manifest["status"] = "segment_auditing"
                manifest["current_segment"] = segment.index
                manifest["segment_audits"].append({"index": segment.index, "attempt": attempt, "status": status, "failed": bool(failed)})
                _atomic_json(project / "audit_attempts" / f"segment_{segment.index:04d}_attempt_{attempt + 1}.verdict.json",
                             {"index": segment.index, "attempt": attempt + 1, "status": status, "failed": bool(failed)})
                _atomic_json(project / "manifest.json", manifest)
                print(f"[H3 segment audit] {segment.index + 1}/{len(segments)} attempt {attempt + 1}: {status}")

            manifest["status"] = "sampling"
            manifest["current_segment"] = segment.index
            _atomic_json(project / "manifest.json", manifest)
            try:
                candidate, audit_attempt = _generate_verified_segment(
                    generate_candidate,
                    lambda value: _audit_segment_latent(
                        value, segment, vae, audio_vae, local_prompt, audit_images, audit_settings,
                        project / "audit_attempts" / f"segment_{segment.index:04d}_attempt_{manifest['current_attempt']}.frames.png"),
                    report_audit, audit_applicable, max_attempts=3,
                    initial_feedback=resume_failure_feedback.get(segment.index, ""))
            except Exception:
                manifest["status"] = "segment_check_failed"
                _atomic_json(project / "manifest.json", manifest)
                raise
            previous = candidate
            segment_seed = _segment_noise_seed(noise_seed, segment.index, audit_attempt)
            lineage = _segment_lineage(generation_fingerprint, predecessor_lineage, segment, prompt_hash, audit_attempt)
            segment_has_dialogue = has_tagged_dialogue(local_prompt)
            metadata = {
                "schema": SCHEMA_VERSION,
                "segment_audit_attempt": audit_attempt,
                "segment_audit_policy": "advisory-v2" if audit_applicable else "not_applicable",
                "index": segment.index,
                "raw_frames": segment.raw_frames,
                "context_frames": segment.context_frames,
                "output_start": segment.output_start,
                "output_frames": segment.output_frames,
                "width": width,
                "height": height,
                "seed": segment_seed,
                "prompt_sha256": prompt_hash,
                "generation_fingerprint": generation_fingerprint,
                "predecessor_lineage": predecessor_lineage,
                "lineage": lineage,
                "has_tagged_dialogue": segment_has_dialogue,
                "audio_refine_applied": bool(
                    audio_refine_model is not None and int(audio_refine_steps) > 0),
            }
            _save_segment(checkpoint, previous, metadata)
            completed += 1
            manifest["segments"].append(_manifest_segment(
                segment, "generated", checkpoint, prompt_hash, segment_seed, lineage))
            _atomic_json(project / "manifest.json", manifest)
            predecessor_lineage = lineage

        if previous is None:
            raise RuntimeError("MiniMax H3 Long Video did not produce a latent")
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

        final_verdicts = {row['index']: row for row in manifest['segment_audits']}
        failed_segments = sorted(index for index, row in final_verdicts.items() if row['failed'] or row['status'] != 'pass')
        audit_status = ('warning' if failed_segments else 'pass') if audit_applicable else 'not_applicable' 
        manifest["visual_cast_audit"] = {"status": audit_status, "mode": "advisory-v2", "failed_segments": failed_segments}
        last_latent, _ = _load_segment(segment_paths[-1])
        last_latent = _cpu_latent(last_latent)
        manifest["status"] = (
            "complete" if not failed_segments else "complete_with_audit_failure"
        )
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
    return MiniMaxH3LongVideoExtension()
