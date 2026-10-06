import hashlib
import importlib.util
import json
from aiohttp import web
from server import PromptServer
import os

# The extension loads py/*.py with absolute-path module names, not packages.
_spec = importlib.util.spec_from_file_location(
    "pysssss_model_info_paths", os.path.join(os.path.dirname(__file__), "..", "model_info_paths.py"))
paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paths)


def get_metadata(filepath):
    with open(filepath, "rb") as file:
        # https://github.com/huggingface/safetensors#format
        # 8 bytes: N, an unsigned little-endian 64-bit integer, containing the size of the header
        header_size = int.from_bytes(file.read(8), "little", signed=False)

        if header_size <= 0:
            raise BufferError("Invalid header size")

        header = file.read(header_size)
        if header_size <= 0:
            raise BufferError("Invalid header")

        header_json = json.loads(header)
        return header_json["__metadata__"] if "__metadata__" in header_json else None


@PromptServer.instance.routes.post("/pysssss/metadata/notes/{name}")
async def save_notes(request):
    try:
        model = paths.resolve_model(request.match_info["name"], match_stem=True)
        paths.write_text(model.parent, model.sidecar(".txt"), await request.text())
    except FileNotFoundError:
        return web.Response(status=404)
    except (paths.InvalidPath, OSError):
        return web.Response(status=400)
    return web.Response(status=200)


@PromptServer.instance.routes.get("/pysssss/metadata/{name}")
async def load_metadata(request):
    try:
        model = paths.resolve_model(request.match_info["name"], match_stem=True)
        info_file = model.sidecar(".txt")
        hash_file = model.sidecar(".sha256")
        try:
            meta = get_metadata(model.path)
        except (OSError, ValueError, BufferError, OverflowError, TypeError):
            meta = None
        if not isinstance(meta, dict):
            meta = {}
        if os.path.isfile(info_file):
            meta["pysssss.notes"] = paths.read_notes(info_file)
        if os.path.isfile(hash_file):
            with open(hash_file, "rt", encoding="utf-8") as file:
                meta["pysssss.sha256"] = file.read()
        else:
            digest = hashlib.sha256()
            with open(model.path, "rb") as file:
                for block in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(block)
            meta["pysssss.sha256"] = digest.hexdigest()
            paths.write_text(model.parent, hash_file, meta["pysssss.sha256"])
    except FileNotFoundError:
        return web.Response(status=404)
    except (paths.InvalidPath, OSError, UnicodeError):
        return web.Response(status=400)
    return web.json_response(meta)
