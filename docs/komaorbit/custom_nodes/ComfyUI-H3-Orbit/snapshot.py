"""Save a viewer snapshot inside the ComfyUI output tree, never from client pixels."""
import datetime
import json
import math
from pathlib import Path
import re
import uuid

from PIL import Image, PngImagePlugin


def read_source(output_root, request):
    root = Path(output_root).resolve()
    subfolder = request.get("subfolder", "")
    if not isinstance(subfolder, str) or not re.fullmatch(r"H3_ORB360/[A-Za-z0-9_-]+", subfolder):
        raise ValueError("保存元の回転データが不正です。")
    source = (root / subfolder).resolve()
    if not source.is_relative_to(root):
        raise ValueError("保存元がoutput外を指しています。")
    manifest_path = (source / "manifest.json").resolve()
    if not manifest_path.is_relative_to(source):
        raise ValueError("保存元の参照先が不正です。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete" or manifest.get("subfolder") != subfolder:
        raise ValueError("完成した回転データが必要です。")
    row, index, zoom = request.get("row"), request.get("index"), request.get("zoom")
    if type(row) is not int or not 0 <= row < manifest.get("row_count", 1):
        raise ValueError("高さの指定が範囲外です。")
    if type(index) is not int or not 0 <= index < manifest["frame_count"]:
        raise ValueError("フレームの指定が範囲外です。")
    if type(zoom) not in (int, float) or not math.isfinite(zoom) or not 0.5 <= zoom <= 4:
        raise ValueError("倍率の指定が範囲外です。")
    basis = manifest.get("frame_basis_zoom", 1)
    if type(basis) not in (int, float) or basis not in (.5, 1):
        raise ValueError("広角画像の倍率情報が不正です。")
    minimum = 1 if request.get("original", False) else basis
    if zoom < minimum:
        raise ValueError("この結果には画像外の背景がありません。背景補完付きで周回を再生成してください。")
    return root, source, manifest


def render_snapshot(source, manifest, request):
    row, index, zoom = request["row"], request["index"], request["zoom"]
    original = request.get("original", False)
    if type(original) is not bool:
        raise ValueError("元画像の指定が不正です。")
    name = "original.png" if original else f"frame_{index:03d}.png"
    path = (source / name if original or manifest["schema_version"] == 1
            else source / f"row_{row:02d}" / name).resolve()
    if not path.is_relative_to(source):
        raise ValueError("画像の参照先が不正です。")
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = manifest["width"], manifest["height"]
        if im.width * h != im.height * w:
            raise ValueError("画像の縦横比が保存データと一致しません。")
        # Centered zoom matches the bounded aspect-ratio frame in viewer.mjs.
        scale = zoom / (1 if original else manifest.get("frame_basis_zoom", 1))
        sx, sy = im.width / (w * scale), im.height / (h * scale)
        result = im.transform((w, h), Image.Transform.AFFINE,
                              (sx, 0, (im.width - w * sx) / 2, 0, sy, (im.height - h * sy) / 2),
                              Image.Resampling.BICUBIC, fillcolor=(16, 18, 23))
    return result


def view_name(manifest, request):
    if request.get("original"):
        angle = "元画像"
    else:
        count = manifest["frame_count"]
        denominator = count if manifest.get("kind") == "references" else max(1, count - 1)
        yaw = 360 * request["index"] / denominator
        elevations = manifest.get("elevations", [0] * manifest.get("row_count", 1))
        elevation = elevations[request["row"]]
        height_name = f"{elevation:+03.0f}" if float(elevation).is_integer() else f"{elevation:+05.1f}"
        angle = f"方位{yaw:05.1f}deg_高さ{height_name}deg_f{request['index']:03d}"
    return f"{angle}_倍率{request['zoom']:.3f}x"


def write_png(path, image, metadata):
    info = PngImagePlugin.PngInfo()
    info.add_text("h3_orbit_view", json.dumps(metadata, ensure_ascii=False))
    temporary = path.with_suffix(".png.tmp")
    image.save(temporary, format="PNG", pnginfo=info)
    temporary.replace(path)


def save_snapshot(output_root, request):
    root, source, manifest = read_source(output_root, request)
    result = render_snapshot(source, manifest, request)
    target = (root / "H3_ORB360" / "snapshots").resolve()
    if not target.is_relative_to(root):
        raise ValueError("保存先がoutput外を指しています。")
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{view_name(manifest, request)}_{stamp}_{uuid.uuid4().hex[:6]}.png"
    write_png(target / name, result, dict(request))
    return {"path": f"H3_ORB360/snapshots/{name}", "width": manifest["width"], "height": manifest["height"]}


def export_folder(output_root, request, progress=None):
    root, source, manifest = read_source(output_root, request)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    subfolder = f"H3_ORB360/exports/周回_{stamp}_{uuid.uuid4().hex[:6]}_倍率{request['zoom']:.3f}x"
    target = (root / subfolder).resolve()
    if not target.is_relative_to(root):
        raise ValueError("保存先がoutput外を指しています。")
    target.mkdir(parents=True, exist_ok=False)
    index = {"status": "writing", "source": request["subfolder"], "zoom": request["zoom"],
             "width": manifest["width"], "height": manifest["height"],
             "frame_basis_zoom": manifest.get("frame_basis_zoom", 1),
             "angle_basis": manifest.get('angle_basis', "prompted_elevation_and_uniform_orbit_position_not_calibrated_3d"),
             "files": []}
    index_path = target / "manifest.json"
    def update_index():
        temporary = target / "manifest.json.tmp"
        temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(index_path)
    update_index()
    total = manifest.get("row_count", 1) * manifest["frame_count"]
    if progress:
        progress({"path": subfolder, "count": 0, "total": total})
    try:
        for row in range(manifest.get("row_count", 1)):
            for frame in range(manifest["frame_count"]):
                view = dict(request, row=row, index=frame, original=False)
                result = render_snapshot(source, manifest, view)
                name = view_name(manifest, view) + ".png"
                write_png(target / name, result, view)
                index["files"].append({"filename": name, "row": row, "index": frame})
                if progress:
                    progress({"path": subfolder, "count": len(index["files"]), "total": total})
            update_index()
        index["status"] = "complete"
        update_index()
        (target / "画像の読み方.txt").write_text(
            "全画像は入力と同じ縦横比・出力サイズです。倍率は画像の切り取り倍率です。\n" +
            ("方位・高さは同じ固定3Dシーンに対するカメラ角度です。\n" if manifest.get('pose_status') == 'fixed_geometry' else
             "方位は周回中の位置を等間隔に換算した値、高さは生成時の指定値です。実測した3D角度ではありません。\n") +
            "f番号は周回の順番です。終端360度は開始0度への復帰フレームとして別に残しています。\n"
            "元の生成画像は変更せず、別フォルダーへ書き出しています。\n", encoding="utf-8")
    except Exception as exc:
        index.update(status="failed", error=str(exc))
        update_index()
        raise
    return {"path": subfolder, "count": len(index["files"]), "width": manifest["width"], "height": manifest["height"]}


def register_routes():
    from server import PromptServer
    if not hasattr(PromptServer, "instance"):
        return  # Unit tests import the node package without a running server.
    import asyncio
    import folder_paths
    from aiohttp import web
    export_jobs = {}

    @PromptServer.instance.routes.get("/h3-orbit/scene-layers")
    async def scene_layers(request):
        try:
            import re
            scene_id = request.query.get('scene_id', '')
            if not re.fullmatch(r'H3_ORB360/[A-Za-z0-9_-]+', scene_id):
                raise ValueError('固定3DのIDが不正です。')
            root = Path(folder_paths.get_output_directory()).resolve()
            directory = (root/scene_id).resolve()
            if not directory.is_relative_to(root):
                raise ValueError('固定3Dの保存先が不正です。')
            layers = json.loads((directory/'viewer_layers.json').read_text())
            manifest = json.loads((directory/'manifest.json').read_text())
            from .scene_layers import minimum_elevation
            if layers.get('scene_sha256') != manifest.get('scene_sha256'):
                raise ValueError('背景の再構成が必要です。')
            # Resolve the installed frontend module, rather than pinning a hashed filename.
            candidates = list((Path(PromptServer.instance.web_root)/'assets').glob('load3dService-*.js'))
            if len(candidates) != 1:
                raise ValueError('このComfyUI版の3D表示サービスを特定できません。')
            source = candidates[0].read_text()
            match = re.search(r'useLoad3dService\s+as\s+(\w+)',source[source.rfind('export{'):])
            if not match:
                raise ValueError('このComfyUI版の3D表示APIに対応していません。')
            extra={}
            if manifest.get('projection')=='unified_textured_scene':
                vendor=list((Path(PromptServer.instance.web_root)/'assets').glob('vendor-three-*.js'))
                if len(vendor)!=1:raise ValueError('3D描画ライブラリを特定できません。')
                export_source=vendor[0].read_text();export_source=export_source[export_source.rfind('export{'):]
                exports={}
                for name in ['Scene','Mesh','ShaderMaterial','TextureLoader','Float32BufferAttribute']:
                    found=re.search(r'(?:\{|,)'+name+r'\s+as\s+(\w+)',export_source)
                    if not found:raise ValueError('3D背景描画の機能が不足しています。')
                    exports[name]=found.group(1)
                extra={'three_module':'/assets/'+vendor[0].name,'three_exports':exports,'base_camera':manifest['camera']}
            return web.json_response({**layers,'width':manifest.get('snapshot_width',manifest['width']),'height':manifest.get('snapshot_height',manifest['height']),'contact_anchor':(manifest.get('alignment',{}).get('ground') or {}).get('anchor'),'minimum_elevation':-30 if extra else minimum_elevation(manifest),'subfolder':scene_id,'module':'/assets/'+candidates[0].name,'export':match.group(1),**extra})
        except (ValueError,KeyError,TypeError,OSError) as exc:
            return web.json_response({'error':str(exc)},status=400)

    @PromptServer.instance.routes.post("/h3-orbit/snapshot")
    async def snapshot(request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("保存する視点の形式が不正です。")
            result = await asyncio.to_thread(save_snapshot, folder_paths.get_output_directory(), payload)
            return web.json_response(result)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @PromptServer.instance.routes.post("/h3-orbit/export")
    async def export(request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("保存する視点の形式が不正です。")
            root = folder_paths.get_output_directory()
            read_source(root, payload)
            if sum(j["status"] == "running" for j in export_jobs.values()) >= 2:
                return web.json_response({"error": "進行中のフォルダー保存が終わるまでお待ちください。"}, status=409)
            job_id = uuid.uuid4().hex
            export_jobs[job_id] = {"status": "running", "count": 0}
            async def run():
                try:
                    result = await asyncio.to_thread(export_folder, root, payload, export_jobs[job_id].update)
                    export_jobs[job_id].update(result, status="complete")
                except Exception as exc:
                    export_jobs[job_id].update(status="failed", error=str(exc))
            task = asyncio.create_task(run())
            # Keep the task alive without putting it in the JSON-serializable status.
            pending_tasks.add(task)
            task.add_done_callback(pending_tasks.discard)
            return web.json_response({"job_id": job_id}, status=202)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    pending_tasks = set()

    @PromptServer.instance.routes.get("/h3-orbit/export/{job_id}")
    async def export_status(request):
        job = export_jobs.get(request.match_info["job_id"])
        if job is None:
            return web.json_response({"error": "保存処理を追跡できません。サーバー再起動の有無と保存フォルダーを確認してください。"}, status=404)
        return web.json_response(dict(job))
