"""Geometry and output contracts for a single-image H3 orbit."""
import math

FRAME_COUNT = 124
FPS = 24
SCHEMA_VERSION = 1


def image_geometry(width, height, long_edge):
    if any(type(v) is not int or v <= 0 for v in (width, height, long_edge)):
        raise ValueError("画像の幅・高さ・生成長辺は正の整数が必要です。")
    scale = min(1.0, long_edge / max(width, height))
    content_w = max(1, round(width * scale))
    content_h = max(1, round(height * scale))
    canvas_w = math.ceil(content_w / 32) * 32
    canvas_h = math.ceil(content_h / 32) * 32
    left = (canvas_w - content_w) // 2
    top = (canvas_h - content_h) // 2
    # An integer multiple of the reduced source ratio has exactly the same DAR.
    divisor = math.gcd(width, height)
    unit_w, unit_h = width // divisor, height // divisor
    multiple = min(divisor, max(1, math.floor(max(content_w, content_h) / max(unit_w, unit_h))))
    return {
        "schema_version": SCHEMA_VERSION,
        "source_width": width, "source_height": height,
        "canvas_width": canvas_w, "canvas_height": canvas_h,
        "content_width": content_w, "content_height": content_h,
        "left": left, "top": top,
        "export_width": unit_w * multiple, "export_height": unit_h * multiple,
    }


def validate_geometry(geometry, frame_width, frame_height, source_width, source_height):
    keys = ("source_width", "source_height", "canvas_width", "canvas_height",
            "content_width", "content_height", "left", "top", "export_width", "export_height")
    if geometry.get("schema_version") != SCHEMA_VERSION or any(type(geometry.get(k)) is not int for k in keys):
        raise ValueError("画像準備ノードのサイズ情報が無効です。接続を確認してください。")
    g = geometry
    if type(g.get("frame_basis_zoom", 1)) not in (int, float) or g.get("frame_basis_zoom", 1) not in (.5, 1):
        raise ValueError("画像外の背景の倍率情報が無効です。")
    if (frame_width, frame_height) != (g["canvas_width"], g["canvas_height"]):
        raise ValueError("生成画像のサイズが入力キャンバスと異なります。幅・高さの接続を確認してください。")
    if (source_width, source_height) != (g["source_width"], g["source_height"]):
        raise ValueError("元画像とサイズ情報が一致しません。同じLoad Imageから接続してください。")
    if min(g[k] for k in keys if k not in ("left", "top")) <= 0:
        raise ValueError("サイズ情報にゼロ以下の寸法があります。")
    if g["left"] < 0 or g["top"] < 0 or g["left"] + g["content_width"] > frame_width or g["top"] + g["content_height"] > frame_height:
        raise ValueError("余白の範囲がキャンバス外です。")
    if g["export_width"] * source_height != g["export_height"] * source_width:
        raise ValueError("保存サイズが元画像の縦横比と一致しません。")
