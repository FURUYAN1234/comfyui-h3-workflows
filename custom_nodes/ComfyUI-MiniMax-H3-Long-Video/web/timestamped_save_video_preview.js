// Distribution change 2026-09-08: use a neutral extension identifier.
import { app } from "../../scripts/app.js";

const TARGET_NODE = "TimestampedSaveVideo";

function makeViewUrl(item) {
  const query = new URLSearchParams();
  query.set("filename", item.filename);
  query.set("subfolder", item.subfolder || "");
  query.set("type", item.type || "output");
  query.set("preview_refresh", String(Date.now()) + "-" + Math.random().toString(36).slice(2));
  return "/view?" + query.toString();
}

function forceLatestVideoPreview(node, item) {
  const url = makeViewUrl(item);
  node.properties ||= {};
  node.properties.latest_saved_video_url = url;
  for (const widget of node.widgets || []) {
    if (widget.name !== "video-preview") continue;
    widget.value = url;
    const player = widget.element?.querySelector?.("video");
    if (!player) continue;
    player.pause?.();
    player.src = url;
    player.load?.();
  }
  node.setDirtyCanvas?.(true, true);
}

app.registerExtension({
  name: "h3_portable.timestamped_save_video_preview_refresh",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== TARGET_NODE) return;
    const originalOnExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      originalOnExecuted?.apply(this, arguments);
      const saved = message?.images?.[0];
      if (!saved?.filename) return;
      requestAnimationFrame(() => forceLatestVideoPreview(this, saved));
    };
  },
});
