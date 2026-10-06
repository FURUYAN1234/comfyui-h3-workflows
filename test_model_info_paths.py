"""Isolated HTTP-handler regressions; all writes stay in temporary fixtures.

Run with: python -B -m unittest -v test_model_info_paths
No ComfyUI installation, models, server or GPU is required.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import quote

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


PLUGIN = Path(__file__).parent / "custom_nodes" / "ComfyUI-Custom-Scripts"


class Request:
    def __init__(self, name, body=None, text="new notes"):
        self.match_info = {"name": name}
        self.body = body
        self.content = text

    async def json(self):
        return self.body

    async def text(self):
        return self.content


class ModelInfoPathsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="h3-model-info-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.models = self.base / "models"
        self.models.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.output = self.base / "output"
        self.output.mkdir()
        self.model = self.models / "model.safetensors"
        header = json.dumps({"__metadata__": {"name": "fixture"}}).encode()
        self.model.write_bytes(len(header).to_bytes(8, "little") + header)
        self.external_model = self.outside / self.model.name
        self.external_model.write_bytes(self.model.read_bytes())
        self.sentinel = self.outside / "sentinel.txt"
        self.sentinel.write_text("DO NOT CHANGE", encoding="utf-8")

        self.folder_paths = types.ModuleType("folder_paths")
        self.folder_paths.get_folder_paths = lambda kind: [str(self.models)] if kind in (
            "checkpoints", "loras", "embeddings") else []
        self.folder_paths.get_filename_list = lambda kind: [
            str(p.relative_to(self.models)) for p in self.models.rglob("*.safetensors")]

        def get_full_path(kind, name):
            if kind not in ("checkpoints", "loras", "embeddings"):
                return None
            candidate = self.models / name
            return str(candidate) if candidate.is_file() else None

        self.folder_paths.get_full_path = get_full_path
        self.folder_paths.get_directory_by_type = lambda kind: str(self.output)
        server = types.ModuleType("server")
        server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(
            routes=web.RouteTableDef()))
        self.routes = server.PromptServer.instance.routes
        nodes = types.ModuleType("nodes")
        nodes.LoraLoader = type("LoraLoader", (), {"RETURN_TYPES": ("MODEL", "CLIP")})
        nodes.CheckpointLoaderSimple = type("CheckpointLoaderSimple", (), {"RETURN_TYPES": ("MODEL", "CLIP", "VAE")})
        self.modules_patch = patch.dict(sys.modules, {
            "folder_paths": self.folder_paths, "server": server, "nodes": nodes})
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)

        # Match the plugin's actual absolute-path spec_from_file_location loader.
        self.loaded = {}
        for stem in ("model_info", "better_combos"):
            path = PLUGIN / "py" / (stem + ".py")
            name = os.path.splitext(str(path))[0]
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.loaded[stem] = module
        self.info = self.loaded["model_info"]
        self.combos = self.loaded["better_combos"]

    def request(self, model="model.safetensors", **kwargs):
        return Request("checkpoints/" + model, **kwargs)

    def link_file(self, target, link):
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("File symlink creation is unavailable without privileges: " + str(exc))

    def link_directory(self, target, link):
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            try:
                os.symlink(target, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symlink creation is unavailable")
        # Remove just the link before TemporaryDirectory cleanup, never its target.
        def remove_link():
            if os.path.islink(link):
                os.unlink(link)
            elif os.path.lexists(link):
                os.rmdir(link)
        self.addCleanup(remove_link)

    async def test_normal_notes_metadata_and_hash_cache(self):
        response = await self.info.save_notes(self.request(text="日本語 <b>plain</b>"))
        self.assertEqual(response.status, 200)
        response = await self.info.load_metadata(self.request())
        self.assertEqual(response.status, 200)
        data = json.loads(response.text)
        self.assertEqual(data["pysssss.notes"], "日本語 <b>plain</b>")
        self.assertEqual(data["pysssss.sha256"], hashlib.sha256(self.model.read_bytes()).hexdigest())
        self.assertEqual(self.model.with_suffix(".sha256").read_text(), data["pysssss.sha256"])

    async def test_legacy_windows_notes_remain_readable(self):
        self.model.with_suffix(".txt").write_bytes("以前のメモ".encode("cp932"))
        with patch("locale.getpreferredencoding", return_value="cp932"):
            response = await self.info.load_metadata(self.request())
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)["pysssss.notes"], "以前のメモ")

    async def test_non_object_model_header_preserves_hash_and_notes(self):
        header = b"123"
        self.model.write_bytes(len(header).to_bytes(8, "little") + header)
        response = await self.info.load_metadata(self.request())
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)["pysssss.sha256"], hashlib.sha256(self.model.read_bytes()).hexdigest())

    async def test_normal_nested_model_and_extensionless_lora(self):
        nested = self.models / "nested"
        nested.mkdir()
        (nested / "CaseModel.safetensors").write_bytes(self.model.read_bytes())
        response = await self.info.save_notes(Request("loras/" + os.path.join("nested", "casemodel"), text="nested"))
        self.assertEqual(response.status, 200)
        self.assertEqual((nested / "CaseModel.txt").read_text(), "nested")

    async def test_normal_example_save_and_list(self):
        response = await self.combos.save_example(self.request(body={"name": "sample", "example": "日本語"}))
        self.assertEqual(response.status, 201)
        self.assertEqual((self.models / "model" / "sample.txt").read_text(encoding="utf-8"), "日本語")
        response = await self.combos.get_examples(self.request())
        self.assertEqual(json.loads(response.text), ["sample.txt"])

    async def test_example_invalid_body_returns_bad_request(self):
        for body in (None, [], {}, {"name": "sample"}, {"name": 3, "example": "text"},
                     {"name": "sample", "example": 3}):
            with self.subTest(body=body):
                response = await self.combos.save_example(self.request(body=body))
                self.assertEqual(response.status, 400)

    async def test_example_parent_traversal_cannot_overwrite_notes(self):
        notes = self.model.with_suffix(".txt")
        notes.write_text("original")
        response = await self.combos.save_example(self.request(body={"name": "../model.txt", "example": "attack"}))
        self.assertEqual(response.status, 400)
        self.assertEqual(notes.read_text(), "original")

    async def test_example_absolute_path_cannot_overwrite_fixture(self):
        response = await self.combos.save_example(self.request(body={"name": str(self.sentinel), "example": "attack"}))
        self.assertEqual(response.status, 400)
        self.assertEqual(self.sentinel.read_text(), "DO NOT CHANGE")

    async def test_example_invalid_names_rejected_before_filesystem_open(self):
        # Block opens and existence checks so even the vulnerable baseline never
        # accesses a Windows device, alternate stream, UNC share or real file.
        names = ("../x", "..\\x", "sub/x", "sub\\x", "/absolute", "C:relative",
                 "C:\\absolute", "\\\\server\\share\\x", "", ".", "..", "NUL",
                 "name:stream", "trailing.", "trailing ", "bad\x00name")
        for name in names:
            with self.subTest(name=name), patch("builtins.open", side_effect=AssertionError("unsafe open")), \
                    patch("os.path.exists", return_value=True):
                response = await self.combos.save_example(self.request(body={"name": name, "example": "attack"}))
                self.assertEqual(response.status, 400)

    async def test_windows_device_aliases_are_rejected_without_io(self):
        for name in ("CONIN$", "CONOUT$", "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"):
            with self.subTest(name=name), self.assertRaises(self.info.paths.InvalidPath):
                self.info.paths.relative_name(name, nested=False)

    async def test_model_traversal_cannot_write_external_notes(self):
        outside_notes = self.external_model.with_suffix(".txt")
        outside_notes.write_text("original")
        response = await self.info.save_notes(self.request("../outside/model.safetensors"))
        self.assertEqual(response.status, 400)
        self.assertEqual(outside_notes.read_text(), "original")

    async def test_model_traversal_cannot_read_or_create_external_hash(self):
        response = await self.info.load_metadata(self.request("../outside/model.safetensors"))
        self.assertEqual(response.status, 400)
        self.assertFalse(self.external_model.with_suffix(".sha256").exists())

    async def test_invalid_model_names_rejected_before_lookup(self):
        names = ("../x", "..\\x", "/absolute", "C:relative", "C:\\absolute",
                 "\\\\server\\share\\x", "", "x/../y", "x\\..\\y", "name:stream")
        for name in names:
            for handler in (self.info.save_notes, self.info.load_metadata, self.combos.get_examples):
                with self.subTest(name=name, handler=handler.__name__), patch.object(
                        self.folder_paths, "get_full_path", side_effect=AssertionError("unsafe lookup")):
                    response = await handler(self.request(name))
                    self.assertEqual(response.status, 400)

    async def test_malformed_model_route_returns_bad_request(self):
        for name in ("checkpoints", "/model.safetensors", "checkpoints/", "unknown/model.safetensors"):
            with self.subTest(name=name):
                response = await self.info.save_notes(Request(name))
                self.assertEqual(response.status, 400)

    async def test_missing_model_returns_not_found(self):
        self.assertEqual((await self.info.save_notes(self.request("missing.safetensors"))).status, 404)

    async def test_untrusted_model_lookup_result_is_rejected(self):
        with patch.object(self.folder_paths, "get_full_path", return_value=str(self.external_model)):
            response = await self.info.load_metadata(self.request())
        self.assertEqual(response.status, 400)
        self.assertFalse(self.external_model.with_suffix(".sha256").exists())

    async def test_model_directory_junction_cannot_escape(self):
        self.link_directory(self.outside, self.models / "linked")
        for handler in (self.info.save_notes, self.info.load_metadata, self.combos.get_examples):
            with self.subTest(handler=handler.__name__):
                response = await handler(self.request("linked/model.safetensors"))
                self.assertEqual(response.status, 400)
        self.assertFalse(self.external_model.with_suffix(".txt").exists())
        self.assertFalse(self.external_model.with_suffix(".sha256").exists())

    async def test_example_directory_junction_cannot_escape(self):
        self.link_directory(self.outside, self.models / "model")
        response = await self.combos.save_example(self.request(body={"name": "sentinel", "example": "attack"}))
        self.assertEqual(response.status, 400)

    async def test_example_junction_cannot_redirect_to_another_model(self):
        other = self.models / "other-model"
        other.mkdir()
        sentinel = other / "sentinel.txt"
        sentinel.write_text("other model")
        self.link_directory(other, self.models / "model")
        response = await self.combos.save_example(self.request(body={"name": "sentinel", "example": "attack"}))
        self.assertEqual(response.status, 400)
        self.assertEqual(sentinel.read_text(), "other model")
        self.assertEqual(self.sentinel.read_text(), "DO NOT CHANGE")
        response = await self.combos.get_examples(self.request())
        self.assertEqual(response.status, 400)

    async def test_model_leaf_symlink_keeps_local_notes_and_hash(self):
        linked = self.models / "linked.safetensors"
        self.link_file(self.external_model, linked)
        self.assertEqual((await self.info.save_notes(self.request(linked.name))).status, 200)
        self.assertEqual((await self.info.load_metadata(self.request(linked.name))).status, 200)
        self.assertTrue(linked.with_suffix(".txt").is_file())
        self.assertTrue(linked.with_suffix(".sha256").is_file())
        self.assertFalse(self.external_model.with_suffix(".txt").exists())
        self.assertFalse(self.external_model.with_suffix(".sha256").exists())

    async def test_notes_symlink_cannot_read_or_overwrite_external_file(self):
        self.link_file(self.sentinel, self.model.with_suffix(".txt"))
        self.assertEqual((await self.info.save_notes(self.request())).status, 400)
        self.assertEqual((await self.info.load_metadata(self.request())).status, 400)
        self.assertEqual(self.sentinel.read_text(), "DO NOT CHANGE")

    async def test_hash_symlink_cannot_read_external_file(self):
        self.link_file(self.sentinel, self.model.with_suffix(".sha256"))
        self.assertEqual((await self.info.load_metadata(self.request())).status, 400)
        self.assertEqual(self.sentinel.read_text(), "DO NOT CHANGE")

    async def test_dangling_hash_symlink_cannot_create_external_file(self):
        target = self.outside / "new.sha256"
        self.link_file(target, self.model.with_suffix(".sha256"))
        self.assertEqual((await self.info.load_metadata(self.request())).status, 400)
        self.assertFalse(target.exists())

    async def test_example_symlink_cannot_overwrite_external_file(self):
        example_dir = self.models / "model"
        example_dir.mkdir()
        self.link_file(self.sentinel, example_dir / "sample.txt")
        self.assertEqual((await self.combos.save_example(self.request(body={
            "name": "sample", "example": "attack"}))).status, 400)
        self.assertEqual(self.sentinel.read_text(), "DO NOT CHANGE")

    async def test_notes_hardlink_does_not_overwrite_other_file(self):
        os.link(self.sentinel, self.model.with_suffix(".txt"))
        self.assertEqual((await self.info.save_notes(self.request())).status, 200)
        self.assertEqual(self.sentinel.read_text(), "DO NOT CHANGE")
        self.assertEqual(self.model.with_suffix(".txt").read_text(), "new notes")

    async def test_registered_root_junction_is_supported(self):
        alias = self.base / "registered"
        self.link_directory(self.models, alias)
        with patch.object(self.folder_paths, "get_folder_paths", return_value=[str(alias)]), \
                patch.object(self.folder_paths, "get_full_path", return_value=str(alias / self.model.name)):
            self.assertEqual((await self.info.save_notes(self.request())).status, 200)
        self.assertEqual(self.model.with_suffix(".txt").read_text(), "new notes")

    async def test_real_plugin_entrypoint_loads_handlers_and_nodes(self):
        package_name = "h3_custom_scripts_fixture"
        spec = importlib.util.spec_from_file_location(package_name, PLUGIN / "__init__.py",
                                                      submodule_search_locations=[str(PLUGIN)])
        module = importlib.util.module_from_spec(spec)
        config = types.ModuleType(package_name + ".pysssss")
        config.init = lambda: True
        config.get_ext_dir = lambda sub: str(PLUGIN / sub)
        scripts = [str(PLUGIN / "py" / (name + ".py")) for name in ("model_info", "better_combos")]
        # Exercise the unmodified entrypoint, stubbing only unrelated modules
        # and initialization that otherwise writes the user's plugin config.
        with patch.dict(sys.modules, {package_name: module, package_name + ".pysssss": config}), \
                patch("glob.glob", return_value=scripts):
            spec.loader.exec_module(module)
            self.assertIn("LoraLoader|pysssss", module.NODE_CLASS_MAPPINGS)
            self.assertIn("CheckpointLoader|pysssss", module.NODE_CLASS_MAPPINGS)
        for path in scripts:
            sys.modules.pop(os.path.splitext(path)[0], None)

    async def test_normal_preview_save(self):
        source = self.output / "image.png"
        source.write_bytes(b"fixture image")
        response = await self.combos.save_preview(self.request(body={"filename": "image.png"}))
        self.assertEqual(response.status, 200)
        self.assertEqual(self.model.with_suffix(".png").read_bytes(), b"fixture image")

    async def test_images_skip_linked_preview(self):
        self.link_file(self.sentinel, self.model.with_suffix(".png"))
        request = Request("")
        request.match_info = {"type": "checkpoints"}
        response = await self.combos.get_images(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), {})

    async def test_images_and_view_preserve_normal_preview(self):
        self.model.with_suffix(".png").write_bytes(b"fixture image")
        request = Request("")
        request.match_info = {"type": "checkpoints"}
        response = await self.combos.get_images(request)
        self.assertEqual(json.loads(response.text), {"model.safetensors": "checkpoints/model.png"})
        response = await self.combos.view(self.request("model.png"))
        self.assertEqual(response.status, 200)

    async def test_preview_source_directory_junction_cannot_escape(self):
        self.link_directory(self.outside, self.output / "linked")
        response = await self.combos.save_preview(self.request(body={"filename": "sentinel.txt", "subfolder": "linked"}))
        self.assertEqual(response.status, 400)
        self.assertFalse(self.model.with_suffix(".txt").exists())

    async def test_preview_destination_symlink_cannot_overwrite(self):
        source = self.output / "image.png"
        source.write_bytes(b"fixture image")
        self.link_file(self.sentinel, self.model.with_suffix(".png"))
        self.assertEqual((await self.combos.save_preview(self.request(body={"filename": "image.png"}))).status, 400)
        self.assertEqual(self.sentinel.read_text(), "DO NOT CHANGE")

    async def test_preview_cannot_overwrite_model_with_non_image(self):
        source = self.output / "replacement.safetensors"
        source.write_bytes(b"not an image")
        original = self.model.read_bytes()
        response = await self.combos.save_preview(self.request(body={"filename": source.name}))
        self.assertEqual(response.status, 400)
        self.assertEqual(self.model.read_bytes(), original)

    async def test_http_contract_for_encoded_names_and_rejections(self):
        app = web.Application()
        app.add_routes(self.routes)
        async with TestClient(TestServer(app)) as client:
            name = quote("checkpoints/model.safetensors", safe="")
            response = await client.post("/pysssss/metadata/notes/" + name, data="http notes")
            self.assertEqual(response.status, 200)
            response = await client.post("/pysssss/examples/" + name, json={"name": "http", "example": "content"})
            self.assertEqual(response.status, 201)
            response = await client.get("/pysssss/metadata/" + name)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["pysssss.notes"], "http notes")
            response = await client.post("/pysssss/examples/" + name, json={"name": "../model", "example": "attack"})
            self.assertEqual(response.status, 400)
            self.assertEqual(self.model.with_suffix(".txt").read_text(), "http notes")
            traversal = quote("checkpoints/../outside/model.safetensors", safe="")
            response = await client.post("/pysssss/metadata/notes/" + traversal, data="attack")
            self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
