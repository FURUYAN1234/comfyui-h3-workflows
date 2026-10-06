import importlib.util
import os
from nodes import LoraLoader, CheckpointLoaderSimple
import folder_paths
from server import PromptServer
from folder_paths import get_directory_by_type
from aiohttp import web

# The extension loads py/*.py with absolute-path module names, not packages.
_spec = importlib.util.spec_from_file_location(
    "pysssss_model_info_paths", os.path.join(os.path.dirname(__file__), "..", "model_info_paths.py"))
paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paths)


@PromptServer.instance.routes.get("/pysssss/view/{name}")
async def view(request):
    try:
        model = paths.resolve_model(request.match_info["name"])
        image_path = paths.child(model.parent, os.path.basename(model.path))
    except FileNotFoundError:
        return web.Response(status=404)
    except (paths.InvalidPath, OSError):
        return web.Response(status=400)

    filename = os.path.basename(image_path)
    return web.FileResponse(image_path, headers={"Content-Disposition": f"filename=\"{filename}\""})


@PromptServer.instance.routes.post("/pysssss/save/{name}")
async def save_preview(request):
    try:
        model = paths.resolve_model(request.match_info["name"])
        body = await request.json()
        if not isinstance(body, dict) or body.get("type", "output") not in ("input", "output", "temp"):
            raise paths.InvalidPath("Invalid image directory")
        directory = get_directory_by_type(body.get("type", "output"))
        if not directory:
            raise paths.InvalidPath("Unknown image directory")
        root = os.path.realpath(directory)
        filename = paths.relative_name(body.get("filename"), nested=False)
        subfolder = body.get("subfolder", "")
        if subfolder:
            filename = os.path.join(paths.relative_name(subfolder), filename)
        source = paths.child(root, filename)
        extension = os.path.splitext(filename)[1].lower()
        if extension not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            raise paths.InvalidPath("An image extension is required")
        paths.copy_preview(root, source, model, extension)
    except FileNotFoundError:
        return web.Response(status=404)
    except (paths.InvalidPath, OSError, ValueError):
        return web.Response(status=400)
    return web.json_response({
        "image": model.kind + "/" + model.stem + extension
    })


@PromptServer.instance.routes.get("/pysssss/examples/{name}")
async def get_examples(request):
    try:
        model = paths.resolve_model(request.match_info["name"])
        example_dir = model.sidecar("")
        info_file = model.sidecar(".txt")
        examples = []
        if os.path.isdir(example_dir):
            root = os.path.realpath(example_dir)
            for name in sorted(os.listdir(root)):
                if name.endswith(".txt"):
                    try:
                        path = paths.child(root, name)
                    except paths.InvalidPath:
                        continue
                    if os.path.isfile(path):
                        examples.append(name)
        if os.path.isfile(info_file):
            examples.append("notes")
    except FileNotFoundError:
        return web.Response(status=404)
    except (paths.InvalidPath, OSError):
        return web.Response(status=400)
    return web.json_response(examples)


@PromptServer.instance.routes.post("/pysssss/examples/{name}")
async def save_example(request):
    try:
        model = paths.resolve_model(request.match_info["name"])
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("example"), str):
            raise paths.InvalidPath("Example text is required")
        example_name = paths.relative_name(body.get("name"), nested=False)
        if not example_name.endswith(".txt"):
            example_name += ".txt"
        example_dir = model.sidecar("")
        os.makedirs(example_dir, exist_ok=True)
        # Recheck after directory creation before choosing the write anchor.
        root = os.path.realpath(model.sidecar(""))
        example_file = paths.child(root, example_name)
        paths.write_text(root, example_file, body["example"])
    except FileNotFoundError:
        return web.Response(status=404)
    except (paths.InvalidPath, OSError, ValueError):
        return web.Response(status=400)
    return web.Response(status=201)


@PromptServer.instance.routes.get("/pysssss/images/{type}")
async def get_images(request):
    type = request.match_info["type"]
    try:
        paths.relative_name(type, nested=False)
        if not folder_paths.get_folder_paths(type):
            raise paths.InvalidPath("Unknown model category")
        names = folder_paths.get_filename_list(type)
    except (paths.InvalidPath, KeyError, OSError):
        return web.Response(status=400)

    images = {}
    for item_name in names:
        file_name = os.path.splitext(item_name)[0]
        try:
            model = paths.resolve_model(type + "/" + item_name)
        except (paths.InvalidPath, OSError):
            continue
        for ext in ["png", "jpg", "jpeg", "preview.png", "preview.jpeg"]:
            try:
                image_path = model.sidecar("." + ext)
            except (paths.InvalidPath, OSError):
                continue
            if os.path.isfile(image_path):
                images[item_name] = f"{type}/{file_name}.{ext}"
                break

    return web.json_response(images)


class LoraLoaderWithImages(LoraLoader):
    RETURN_TYPES = (*LoraLoader.RETURN_TYPES, "STRING",)
    RETURN_NAMES = (*getattr(LoraLoader, "RETURN_NAMES",
                    LoraLoader.RETURN_TYPES), "example")

    @classmethod
    def INPUT_TYPES(s):
        types = super().INPUT_TYPES()
        types["optional"] = {"prompt": ("STRING", {"hidden": True})}
        return types

    def load_lora(self, **kwargs):
        prompt = kwargs.pop("prompt", "")
        return (*super().load_lora(**kwargs), prompt)


class CheckpointLoaderSimpleWithImages(CheckpointLoaderSimple):
    RETURN_TYPES = (*CheckpointLoaderSimple.RETURN_TYPES, "STRING",)
    RETURN_NAMES = (*getattr(CheckpointLoaderSimple, "RETURN_NAMES",
                    CheckpointLoaderSimple.RETURN_TYPES), "example")

    @classmethod
    def INPUT_TYPES(s):
        types = super().INPUT_TYPES()
        types["optional"] = {"prompt": ("STRING", {"hidden": True})}
        return types

    def load_checkpoint(self, **kwargs):
        prompt = kwargs.pop("prompt", "")
        return (*super().load_checkpoint(**kwargs), prompt)


NODE_CLASS_MAPPINGS = {
    "LoraLoader|pysssss": LoraLoaderWithImages,
    "CheckpointLoader|pysssss": CheckpointLoaderSimpleWithImages,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoraLoader|pysssss": "Lora Loader 🐍",
    "CheckpointLoader|pysssss": "Checkpoint Loader 🐍",
}
