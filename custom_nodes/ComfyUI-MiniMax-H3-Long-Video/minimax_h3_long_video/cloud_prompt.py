"""Local export of the delivered H3 prompt and its image inputs."""
import hashlib
import shutil
import json
import re
from pathlib import Path

import folder_paths
import torch
from PIL import Image
from comfy_api.latest import io

from .timeline import format_timestamp, parse_timestamp


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Prompt export path must remain inside its video bundle")
    return path


def portable_segment(prompt, context_seconds):
    # Only the local continuation head is removed. Preserve words, identities,
    # shot actions and selected retake corrections.
    prompt = re.sub(r"(?m)^PACKED-PASS CLOCK:.*\n?", "", prompt)
    prompt = re.sub(r"(?m)^.*supplied preceding AV guide occupies.*\n?", "", prompt)
    if context_seconds:
        prompt = re.sub(r"\b\d{2}:\d{2}(?:\.\d{3})?\b",
                        lambda m: format_timestamp(max(0, parse_timestamp(m.group()) - context_seconds)), prompt)
    prompt = prompt.replace("packed-pass time", "clip time")
    prompt = prompt.replace("From the packed guide boundary", "From the start of this clip")
    prompt = prompt.replace("from the supplied AV guide", "from the preceding clip when supported")
    prompt = prompt.replace("established by the preceding AV guide", "described for the scene")
    return prompt.strip()


def export_text(final_prompt, mode, project, manifest, reference_count):
    fps = float(manifest["fps"])
    duration = sum(row["output_frames"] for row in manifest["segments"]) / fps
    header = f"Create a {duration:g}-second video at {manifest['width']}x{manifest['height']}, {fps:g} fps."
    if mode == "I2V":
        header += " Use the attached image as the exact opening frame."
    elif reference_count:
        header += f" Use the {reference_count} attached reference image(s), numbered <Picture 1> onward in upload order."
    whole = header + "\n\n" + final_prompt.strip()
    sections = [{"label": "Whole video / 全体", "text": whole}]
    for row in manifest["segments"]:
        index = int(row["index"])
        selected = manifest.get("candidate_selection", {}).get(str(index), {}).get("attempt")
        attempt_file = inside(project, f"audit_attempts/segment_{index:04d}_attempt_{selected}.json")
        if selected is not None and attempt_file.is_file():
            local = json.loads(attempt_file.read_text(encoding="utf-8"))["prompt"]
            provenance = f"selected attempt {selected}"
        else:
            local = inside(project, row["prompt_file"]).read_text(encoding="utf-8")
            provenance = "saved segment prompt; retake details unavailable"
        seconds = row["output_frames"] / fps
        reference = manifest.get("segment_references", {}).get(str(index))
        attachment = (f" Attach clip_{index + 1:02d}_reference.png as <Picture 1>; this clip uses its own cast-scoped image."
                      if reference else "")
        sections.append({"label": f"Clip {index + 1} / 区間{index + 1} ({seconds:g}s)",
                         "text": f"Create a {seconds:g}-second clip.{attachment}\n\n" + portable_segment(local, row.get("context_frames", 0) / fps),
                         "source": provenance, "native_prompt": local})
    info = (f"Mode / 方式: {mode}\nDuration / 尺: {duration:g} s\n"
            f"Resolution / 解像度: {manifest['width']} × {manifest['height']}\nFPS: {fps:g}\n"
            f"Local seed / ローカルseed: {manifest.get('seed', 'unknown')}\n"
            f"Segments / 区間数: {len(manifest['segments'])}\n"
            "Use the same model family and attach the exported images in order. / 同系統のモデルを選び、書き出した画像を順番どおり添付してください。\n"
            "If the service cannot accept the whole duration, use the clips in order. / 全体尺に対応しない場合は区間順に生成してください。\n"
            "Whole-video text is the final input; clip text includes the selected retake. / 全体欄は最終入力文、区間欄は採用された再試行の内容を含みます。\n"
            "Model, duration limits, reference support, voice and continuation behavior vary by service; identical output is not guaranteed. / モデル・尺上限・参照画像・声・継続方法の差により、同一映像は保証されません。\n"
            "Local latent guidance and post-production titles/credits are not reproduced by text alone. / ローカルの潜在継続と後合成のタイトル・クレジットは文章だけでは再現されません。\n"
            "Nothing is uploaded automatically. / 外部サービスへの自動送信は行いません。")
    if manifest.get("master_is_partial"):
        info += "\nPartial video: whole-video input may describe unfinished shots. / 部分出力のため全体欄には未生成の場面が含まれる場合があります。"
    # Exportable text is not evidence that the generated content passed inspection.
    selected_audits = manifest.get("selected_segment_audits", {})
    failed = [int(row["index"]) + 1 for row in manifest["segments"]
              if selected_audits.get(str(row["index"]), {}).get("failed") is True]
    unverified = [int(row["index"]) + 1 for row in manifest["segments"]
                  if selected_audits.get(str(row["index"]), {}).get("status") != "pass"
                  or selected_audits.get(str(row["index"]), {}).get("failed") is not False]
    warning = ""
    if failed or manifest.get("status") == "complete_with_audit_failure":
        clips = ", ".join(map(str, failed)) or "unknown / 不明"
        warning = f"Local inspection failed: clips {clips}. / ローカル検査未合格の区間: {clips}。"
    elif unverified:
        warning = "Local content inspection is unverified. / ローカルの内容検査は未確認です。"
    if warning:
        info = warning + "\n" + info
    return {"sections": sections, "info": info, "audit_warning": warning}


class MiniMaxH3CloudPrompt(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMaxH3CloudPrompt", display_name="Cloud prompt + Copy / クラウド用プロンプト＋コピー",
            category="sampling/minimax", is_output_node=True,
            inputs=[io.String.Input("final_prompt", force_input=True),
                    io.String.Input("master_path", force_input=True),
                    io.Combo.Input("mode", options=["T2V", "I2V", "Ref2V", "Comic Ref2V"]),
                    io.String.Input("continuous_bgm_file", force_input=True, optional=True),
                    io.Autogrow.Input("images", optional=True,
                        template=io.Autogrow.TemplatePrefix(input=io.Image.Input("image"), prefix="image_", min=0, max=9))],
            outputs=[io.String.Output("cloud_prompt"), io.String.Output("export_folder")])

    @classmethod
    def execute(cls, final_prompt, master_path, mode, images=None, continuous_bgm_file=""):
        root = Path(folder_paths.get_output_directory()).resolve()
        master = Path(master_path).resolve()
        if not master.is_relative_to(root) or not master.is_file():
            raise ValueError("Cloud export requires an existing video inside ComfyUI/output")
        project = master.parent
        manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("master") != master.name:
            raise ValueError("Video does not match the bundle manifest")
        tensors = [frame for key in sorted(images or {}, key=lambda key: int(key.rsplit('_', 1)[-1]))
                   for frame in images[key]]
        payload = export_text(final_prompt, mode, project, manifest, len(tensors))
        destination = inside(project, "cloud_prompt")
        destination.mkdir(exist_ok=True)
        if continuous_bgm_file:
            score = Path(continuous_bgm_file).resolve()
            if not score.is_relative_to(root) or not score.is_file():
                raise ValueError("Continuous BGM must be an existing file inside ComfyUI/output")
            import av
            with av.open(str(score)) as container:
                if not container.streams.audio:
                    raise ValueError("Continuous BGM file has no audio")
            name = "continuous_bgm" + score.suffix
            shutil.copyfile(score, destination / name)
            note = (f"After joining all clips, add {name} once from the beginning across the whole video, "
                    "ducked under dialogue. Do not restart music at cuts or ask the video model to add another score.")
            payload["sections"][0]["text"] += "\n\nPOST-PRODUCTION: " + note
            payload["info"] += ("\nContinuous score / 通しBGM: " + name
                + "\nAdd this track once after joining clips; lower it during dialogue. / 区間を結合してから通しで1回だけ重ね、会話中は音量を下げます。")
            payload["bgm_file"] = name
        files = []
        for index, tensor in enumerate(tensors, 1):
            name = f"image_{index:02d}.png"
            pixels = tensor.detach().cpu().clamp(0, 1).mul(255).round().to(torch.uint8).numpy()
            Image.fromarray(pixels).save(destination / name)
            files.append(name)
        for index, reference in manifest.get("segment_references", {}).items():
            source = inside(project, reference["image_file"])
            if (reference.get("passed") is not True
                    or hashlib.sha256(source.read_bytes()).hexdigest() != reference.get("image_sha256")):
                raise ValueError("Scoped reference is missing, unverified or changed")
            name = f"clip_{int(index) + 1:02d}_reference.png"
            shutil.copyfile(source, destination / name)
            payload["info"] += f"\nClip {int(index) + 1} reference / 区間{int(index) + 1}の添付画像: {name}"
        payload["info"] += "\n\nWhole-video images / 全体の添付画像（順番）: " + (", ".join(files) if files else "None / なし")
        payload["folder"] = str(destination)
        for index, section in enumerate(payload["sections"]):
            name = "whole_video.txt" if index == 0 else f"clip_{index:02d}.txt"
            (destination / name).write_text(section["text"], encoding="utf-8")
        (destination / "README.txt").write_text(payload["info"], encoding="utf-8")
        (destination / "prompts.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return io.NodeOutput(payload["sections"][0]["text"], str(destination), ui={"cloud_prompt": [payload]})
