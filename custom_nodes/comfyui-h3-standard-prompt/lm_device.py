"""Local LM Studio device handoff for the three-mode prompt node.

The CLI preserves the selected quantization, context, concurrency and TTL. The
SDK independently verifies device placement before allowing H3 to continue.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import tempfile
import time
from urllib.parse import urlsplit
import urllib.request

_LOCK = threading.Lock()


def _run(argv, timeout=180):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    # Windows LM Studio helpers can inherit stdout pipes under WSL. A file
    # keeps command completion tied to the CLI process, not a descendant's pipe.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout, **options)
        out.seek(0)
        output = out.read().decode("utf-8", errors="replace").strip()
        if result.returncode:
            err.seek(0)
            detail = err.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"LM Studio CLI failed ({result.returncode}): {(detail or output)[-500:]}")
    return output


def _local_cli():
    executable = shutil.which("lms")
    if executable:
        return executable
    relative = Path("Programs/LM Studio/resources/app/.webpack/lms.exe")
    if os.name == "nt":
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / relative
    elif "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower():
        # Only read the current Windows user's installation path; no shell-built
        # command contains a model name, prompt, or caller-controlled argument.
        windows_path = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                             "[Console]::Write($env:LOCALAPPDATA)"], 15)
        candidate = Path(_run(["wslpath", "-u", windows_path], 10)) / relative
    else:
        raise RuntimeError("LM Studio CLI が見つかりません。lms を PATH に追加してください。")
    if not candidate.is_file():
        raise RuntimeError(f"LM Studio CLI が見つかりません: {candidate}")
    return str(candidate)


def _local_host(host):
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    if os.name != "nt" and Path("/proc/net/route").is_file():
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            fields = line.split()
            if fields[1] == "00000000":
                import socket, struct
                if host == socket.inet_ntoa(struct.pack("<L", int(fields[2], 16))):
                    return True
    return False


class LocalLM:
    def __init__(self, api_base, identifier):
        parsed = urlsplit(api_base)
        if parsed.scheme != "http" or parsed.port != 1234 or not _local_host(parsed.hostname):
            raise RuntimeError("GPU自動切替は、このPCのLM Studio（ポート1234）で使用してください。")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/v1":
            raise ValueError("LM Studio API のURLを確認してください。")
        if not identifier or identifier.startswith("-"):
            raise ValueError("LM Studio モデルIDが不正です。")
        self.api_base = api_base.rstrip("/")
        self.identifier = identifier
        self.cli = _local_cli()
        try:
            import lmstudio
        except ImportError as exc:
            raise RuntimeError("GPU自動切替には、このノードの requirements.txt のインストールが必要です。") from exc
        self.client = lmstudio.Client(parsed.netloc)

    def close(self):
        self.client.close()

    def _api_instance(self):
        request = urllib.request.Request(self.api_base[:-3] + "/api/v1/models",
                                         headers={"Authorization": "Bearer lm-studio"})
        with urllib.request.urlopen(request, timeout=10) as response:
            models = json.load(response)["models"]
        for model in models:
            for instance in model.get("loaded_instances", []):
                if instance["id"] == self.identifier:
                    return model, instance
        raise RuntimeError(f"LM Studio に対象モデルが読み込まれていません: {self.identifier}")

    def _read_cli_json(self, command):
        if command not in {"ps", "ls"}:
            raise ValueError("Only read-only LM Studio status commands may retry.")
        for attempt in range(2):
            try:
                return json.loads(_run([self.cli, command, "--json"], 20))
            except subprocess.TimeoutExpired as exc:
                if attempt:
                    raise RuntimeError("LM Studioの状態取得が2回タイムアウトしました。切替を中止します。") from exc
                # A retry is safe only while the same local instance is still
                # reachable. Never restart or reload a model to hide a read error.
                try:
                    self._api_instance()
                except Exception as health_error:
                    raise RuntimeError("LM Studioへの接続が失われたため、状態取得の再試行を中止しました。") from health_error
                print("[H3 LLM] APIの生存を確認しました。状態取得だけを1回再試行します。")

    def snapshot(self):
        # CLI and API must identify the same local instance before any unload.
        loaded = self._read_cli_json("ps")
        matches = [m for m in loaded if m.get("identifier") == self.identifier]
        if len(matches) != 1:
            raise RuntimeError(f"ローカルCLIの対象モデルが一致しません: {self.identifier}")
        model = matches[0]
        if model.get("status") != "idle" or model.get("queued", 0):
            raise RuntimeError("対象LLMは別の推論を実行中です。完了後に再実行してください。")
        api_model, instance = self._api_instance()
        if model["modelKey"] != api_model["key"] or model["contextLength"] != instance["config"]["context_length"]:
            raise RuntimeError("CLIとAPIのモデルが一致しないため切替を中止しました。")
        variant = model.get("selectedVariant") or model["modelKey"]
        if variant.startswith("-"):
            raise ValueError("LM Studio モデル名が不正です。")
        catalog = self._read_cli_json("ls")
        candidates = [item for item in catalog if item.get("modelKey") == model["modelKey"]]
        if len(candidates) != 1 or (candidates[0].get("selectedVariant") or candidates[0]["modelKey"]) != variant:
            raise RuntimeError("読み込み候補の量子化が現在のLLMと一致しません。")
        return {"model_key": variant, "load_key": candidates[0].get("indexedModelIdentifier") or model["modelKey"], "identifier": self.identifier,
                "context": model["contextLength"], "parallel": model["parallel"],
                "ttl": max(1, int(model["ttlMs"] / 1000)) if model.get("ttlMs") else None}

    def placement(self):
        handles = [m for m in self.client.llm.list_loaded() if m.identifier == self.identifier]
        if len(handles) != 1:
            raise RuntimeError("LM Studio の読み込み結果を確認できません。")
        return handles[0].get_load_config().to_dict()

    def is_loaded(self):
        return any(m.identifier == self.identifier for m in self.client.llm.list_loaded())

    def unload(self):
        loaded = self._read_cli_json("ps")
        if any(m.get("identifier") == self.identifier for m in loaded):
            _run([self.cli, "unload", self.identifier], 60)

    def load(self, snapshot, device):
        argv = [self.cli, "load", snapshot["load_key"], "--gpu", device,
                "--context-length", str(snapshot["context"]), "--parallel", str(snapshot["parallel"]),
                "--identifier", self.identifier, "--yes"]
        if snapshot["ttl"] is not None:
            argv += ["--ttl", str(snapshot["ttl"])]
        _run(argv, 180)
        actual = self.snapshot()
        if any(actual[k] != snapshot[k] for k in ("model_key", "context", "parallel")):
            raise RuntimeError("LLMのモデル・コンテキスト長・並列数が切替前と一致しません。")
        ratio = self.placement().get("gpu", {}).get("ratio")
        if (device == "off" and ratio != 0) or (device == "max" and ratio not in (1, "max")):
            raise RuntimeError(f"LLMのGPU配置を確認できません: requested={device}, actual={ratio}")
        return ratio


def _restore_cpu(backend, snapshot, report):
    backend.unload()
    try:
        backend.load(snapshot, "off")
    except BaseException:
        # A failed CPU verification must never release H3 to compete with a
        # surviving GPU instance. Leave the node failed, even if unload succeeds.
        backend.unload()
        report["cpu_restored"] = False
        raise
    report["cpu_restored"] = True


@contextmanager
def device_session(backend, release_models):
    report = {"enabled": True, "gpu_loaded": False, "cpu_restored": False}
    with _LOCK:
        snapshot = backend.snapshot()
        report["model"] = snapshot["model_key"]
        started = time.monotonic()
        release_models()
        # Set up the cleanup scope BEFORE the first state-changing operation.
        try:
            backend.unload()
            backend.load(snapshot, "max")
            report["gpu_loaded"] = True
            report["gpu_switch_seconds"] = round(time.monotonic() - started, 3)
            print("[H3 LLM] GPUへの切替を確認しました。プロンプトを変換します。")
            yield report
        finally:
            started = time.monotonic()
            _restore_cpu(backend, snapshot, report)
            report["cpu_restore_seconds"] = round(time.monotonic() - started, 3)
            print("[H3 LLM] CPUへの復帰を確認しました。")


@contextmanager
def prompt_gpu_session(helper, model, api_base, enabled):
    if not enabled:
        yield {"enabled": False}
        return
    import sys
    module = sys.modules[type(helper).__module__]
    # Honor a nonlocal explicit endpoint by refusing device mutation instead of
    # silently redirecting the request to a different machine.
    parsed = urlsplit(api_base)
    if not _local_host(parsed.hostname):
        raise RuntimeError("このAPI接続先ではGPU自動切替を使用できません。設定をOFFにしてください。")
    bases = module._ready_lm_studio_bases(api_base)
    if not bases:
        started, message = module._auto_start_lm_studio(180)
        if not started:
            raise RuntimeError(message)
        bases = module._ready_lm_studio_bases(api_base)
    if not bases:
        raise RuntimeError("LM Studioへの接続を確認できません。")
    if model not in module._loaded_lm_studio_models(bases[0]):
        started, message = module.ensure_lm_studio_for_prompt(model, 180)
        if not started:
            raise RuntimeError(message)
    backend = LocalLM(bases[0], model)
    try:
        # This is the same endpoint whose model placement was verified.
        helper.H3_MANAGED_API_BASE = bases[0]
        import comfy.model_management as mm
        def release_models():
            mm.unload_all_models()
            mm.soft_empty_cache()
        with device_session(backend, release_models) as report:
            report["api_base"] = bases[0]
            yield report
    finally:
        helper.__dict__.pop("H3_MANAGED_API_BASE", None)
        backend.close()


def ensure_cpu_for_video(settings):
    """Recheck cached prompt nodes: their convert() may not run on this queue."""
    backend = LocalLM(settings["api_base"], settings["identifier"])
    try:
        with _LOCK:
            if not backend.is_loaded():
                return  # No LM weights occupy the GPU; visual audit can start CPU.
            if backend.placement().get("gpu", {}).get("ratio") == 0:
                return
            snapshot = backend.snapshot()
            _restore_cpu(backend, snapshot, {})
            print("[H3 LLM] キャッシュ利用時のCPU復帰を確認しました。")
    finally:
        backend.close()
