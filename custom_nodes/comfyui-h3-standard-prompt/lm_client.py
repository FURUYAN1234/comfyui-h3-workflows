"""Portable LM Studio request client, derived from the installed Qwen prompt helper.
Uses the same native /api/v1/chat payload and image preprocessing.
Start LM Studio and load the model manually as described in README.
"""
import base64, io, json, os, re, socket, struct, urllib.error, urllib.request
import numpy as np
from PIL import Image
def _clean_api_base(api_base):
    value = (api_base or "").strip().rstrip("/")
    if value.endswith("/v1"):
        return value
    return value + "/v1"

def _windows_host_from_resolv_conf():
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8") as handle:
            for line in handle:
                match = re.match(r"\s*nameserver\s+([^\s]+)", line)
                if match:
                    return match.group(1)
    except OSError:
        return None
    return None

def _windows_host_from_default_route():
    """Return the WSL host-side gateway without spawning a shell command."""
    try:
        with open("/proc/net/route", "r", encoding="ascii") as handle:
            next(handle, None)
            for line in handle:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except (OSError, ValueError, struct.error):
        return None
    return None

def _candidate_api_bases(api_base):
    candidates = []
    route_host = _windows_host_from_default_route()
    route_url = f"http://{route_host}:1234/v1" if route_host else None
    for value in (api_base, route_url, "http://127.0.0.1:1234/v1", "http://localhost:1234/v1"):
        if value:
            value = _clean_api_base(value)
            if value not in candidates:
                candidates.append(value)

    # Slower fallbacks are last so a stale DNS address cannot delay the normal WSL route.
    for value in ("http://host.docker.internal:1234/v1",):
        value = _clean_api_base(value)
        if value not in candidates:
            candidates.append(value)
    host = _windows_host_from_resolv_conf()
    if host:
        value = f"http://{host}:1234/v1"
        if value not in candidates:
            candidates.append(value)
    return candidates

def _post_json(url, payload, timeout):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer lm-studio"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def _tensor_image_to_pil(image):
    pixels = image[0].detach().cpu().numpy()
    pixels = np.clip(pixels[:, :, :3] * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(pixels, mode="RGB")

def _ready_lm_studio_bases(api_base="http://127.0.0.1:1234/v1", timeout=2):
    """Return reachable LM Studio API bases without spending inference time."""
    ready = []
    for base in _candidate_api_bases(api_base):
        try:
            root = _clean_api_base(base)[: -len("/v1")]
            request = urllib.request.Request(
                root + "/api/v1/models",
                headers={"Authorization": "Bearer lm-studio"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                json.loads(response.read().decode("utf-8"))
            ready.append(base)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            urllib.error.URLError,
            socket.timeout,
        ):
            continue
    if os.name != "nt":
        route_host = _windows_host_from_default_route()
        route_base = (
            _clean_api_base("http://{}:1234/v1".format(route_host))
            if route_host else None
        )
        if route_base in ready:
            ready.remove(route_base)
            ready.insert(0, route_base)
    return ready

def _native_chat_url(api_base):
    base = _clean_api_base(api_base)
    return base[: -len("/v1")] + "/api/v1/chat"

def _auto_start_lm_studio(timeout_seconds):
    return False, 'LM StudioのDeveloper画面でサーバーと画像対応モデルを起動し、モデル名と接続先を確認してください。配布版は個人用自動起動スクリプトを実行しません。'

class LocalLMHelper:
    SYSTEM_PROMPT = ''
    def convert(
        self,
        japanese_instruction,
        model,
        api_base,
        temperature,
        max_tokens,
        timeout_seconds,
        fallback_to_japanese,
        extra_rules="",
        reference_image_1=None,
        reference_image_2=None,
        reference_image_3=None,
        reference_image_4=None,
        input_images=None,
        reasoning="off",
    ):
        instruction = (japanese_instruction or "").strip()
        if not instruction:
            return ("", "入力が空です")

        system_prompt = self.SYSTEM_PROMPT
        if extra_rules and extra_rules.strip():
            system_prompt += "\nAdditional user rules:\n" + extra_rules.strip()

        schema_images = [
            image for image in (
                reference_image_1,
                reference_image_2,
                reference_image_3,
                reference_image_4,
            ) if image is not None
        ]
        if input_images is None:
            input_images = schema_images
        elif schema_images:
            input_images = schema_images + list(input_images)

        if input_images and "CONNECTED_VISUAL_ATTACHMENTS:" not in instruction:
            labels = ", ".join(
                "Picture {}".format(index) for index in range(1, len(input_images) + 1)
            )
            instruction = (
                instruction
                + "\n\nCONNECTED_VISUAL_ATTACHMENTS: "
                + labels
                + ". Inspect these images directly and do not invent unchanged visual attributes."
            )

        request_input = instruction
        if input_images:
            request_input = [{"type": "text", "content": instruction}]
            for image in input_images:
                if image is None:
                    continue
                picture = _tensor_image_to_pil(image)
                picture.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                picture.convert("RGB").save(buffer, format="JPEG", quality=92, optimize=True)
                request_input.append({
                    "type": "image",
                    "data_url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
                })

        payload = {
            "model": (model or "qwen-prompt-ja").strip(),
            "system_prompt": system_prompt,
            "input": request_input,
            "temperature": float(temperature),
            "max_output_tokens": int(max_tokens),
            "reasoning": reasoning if reasoning in ("off", "on", "low", "medium", "xhigh") else "off",
            "stream": False,
            "store": False,
        }

        def request_prompt(request_timeout, bases=None):
            request_errors = []
            for base in (bases or _candidate_api_bases(api_base)):
                try:
                    data = _post_json(_native_chat_url(base), payload, int(request_timeout))
                    prompt = "\n".join(
                        item.get("content", "").strip()
                        for item in data.get("output", [])
                        if item.get("type") == "message" and item.get("content", "").strip()
                    ).strip()
                    prompt = re.sub(r"^```(?:text)?\s*|\s*```$", "", prompt, flags=re.IGNORECASE).strip()
                    if not prompt:
                        raise ValueError("LM Studio returned an empty prompt")
                    return prompt, base, request_errors
                except (
                    OSError,
                    KeyError,
                    IndexError,
                    ValueError,
                    json.JSONDecodeError,
                    urllib.error.URLError,
                    socket.timeout,
                ) as exc:
                    request_errors.append(f"{base}: {exc}")
            return None, None, request_errors

        # Detect availability with the lightweight models endpoint. A healthy local
        # model can legitimately need far more than three seconds for a vision or
        # long-prompt response, so never use an inference timeout as a health check.
        ready_bases = _ready_lm_studio_bases(api_base)
        errors = []
        if ready_bases:
            prompt, base, errors = request_prompt(
                int(timeout_seconds), ready_bases[:1]
            )
            if prompt:
                return (prompt, f"OK: {base} / {payload['model']}")

        started, start_status = _auto_start_lm_studio(timeout_seconds)
        if started:
            retry_bases = _ready_lm_studio_bases(api_base)
            prompt, base, retry_errors = request_prompt(
                int(timeout_seconds), retry_bases[:1] or None
            )
            if prompt:
                return (prompt, f"OK（自動起動）: {base} / {payload['model']}")
            errors.extend(retry_errors)
        else:
            errors.append(start_status)

        message = "LM Studioへ接続できませんでした: " + " | ".join(errors)
        if fallback_to_japanese:
            return (instruction, message + " / 日本語原文をそのまま使用")
        raise RuntimeError(message)
