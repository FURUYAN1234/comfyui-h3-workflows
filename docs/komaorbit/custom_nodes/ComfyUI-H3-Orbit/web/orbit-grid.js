import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { OrbitViewer } from "./viewer.mjs?rev=20261104-safe-angle";
import { normalizeWorkflowFields } from "./serialization.mjs";
import { attachSceneLayers } from "./scene-layers.mjs?rev=20261104-gray1";

import { installSceneRefresh, applySceneSize } from "./scene-refresh.mjs";

const viewers = new WeakMap();
const sceneButtons = new WeakSet();
const serializableGraphs = new WeakSet();


function createSceneSave(node) {
    if (node.type !== "SaveGaussianSplat" || !node.properties?.h3_rigid_preview || sceneButtons.has(node)) return;
    applySceneSize(node, node.properties.h3_scene_size);
    sceneButtons.add(node);
    const root = document.createElement("div");
    root.style.cssText = "display:flex;align-items:center;gap:10px;padding:8px;color:#ddd;font:12px sans-serif";
    const button = document.createElement("button");
    button.textContent = "この視点を保存";
    const status = document.createElement("span");
    status.setAttribute("role", "status");
    const instructions = document.createElement("div");
    instructions.className = "h3-scene-mouse-instructions";
    instructions.textContent = "左ボタンを押しながらドラッグ＝回転\n右ボタンを押しながらドラッグ＝平行移動\nホイール＝拡大縮小";
    instructions.style.cssText = "grid-column:1 / -1;white-space:pre-line;font-size:14px;line-height:1.5;font-weight:600;color:#fff";
    const reset=document.createElement("button");reset.textContent="中央に戻す";reset.onclick=async()=>{reset.disabled=true;try{await node._h3ResetCamera?.();}catch(error){status.textContent=`中央への復帰に失敗: ${error.message}`;console.error(error);}finally{reset.disabled=false;}};
    root.style.cssText="display:grid;grid-template-columns:max-content max-content 1fr;align-content:start;gap:6px;padding:8px;color:#ddd;font:12px sans-serif";status.style.gridColumn="1 / -1";
    root.append(instructions,button,reset,status);
    for (const event of ["pointerdown", "pointermove", "pointerup", "wheel", "keydown"])
        root.addEventListener(event, e => e.stopPropagation());
    const saveWidget = node.addDOMWidget("h3_scene_save", "H3_SCENE_SAVE", root, {
        serialize: false, hideOnZoom: false, getMinHeight: () => 110, getMaxHeight: () => 110, getHeight: () => 110,
    });
    saveWidget.serializeValue = () => undefined;
    installSceneRefresh(node, () => attachSceneLayers(node,api,status));
    const removed=node.onRemoved;
    node.onRemoved=function(){node._h3LayersDispose?.();return removed?.apply(this,arguments);};
    void attachSceneLayers(node,api,status);
    button.onclick = async () => {
        button.disabled = true;
        try {
            const modelFile = String(node.properties["Last Time Model File"] || "").replaceAll("\\", "/");
            const subfolder = modelFile.slice(0, modelFile.lastIndexOf("/"));
            if (!/^H3_ORB360\/[A-Za-z0-9_-]+$/.test(subfolder)) throw new Error("固定3Dを先に生成してください。");
            const widget = node.widgets?.find(w => w.name === "viewport_state");
            const viewport = await widget?.serializeValue?.();
            if (!viewport?.camera_info) throw new Error("3Dプレビューの読み込み完了を待ってください。");
            status.textContent = "現在の視点を保存待ち…";
            const submitted = await api.queuePrompt(0, { output: {
                "1": { class_type: "H3OrbitSceneSnapshot", inputs: {
                    scene_id: subfolder, camera_json: JSON.stringify(viewport.camera_info),
                    model_json: JSON.stringify(viewport.model_3d_info || []),
                } },
            }, workflow: { id: crypto.randomUUID(), nodes: [], links: [], version: 0.4 } });
            const id = submitted.prompt_id;
            if (!id) throw new Error("保存処理を開始できませんでした。");
            // Use ComfyUI's queue so saving cannot compete with generation on the single GPU.
            for (;;) {
                await new Promise(resolve => setTimeout(resolve, 1500));
                if (!root.isConnected) return;
                const response = await api.fetchApi(`/history/${encodeURIComponent(id)}`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const history = (await response.json())[id];
                if (!history) continue;
                const saved = history.outputs?.["1"]?.images?.[0];
                if (saved) { status.textContent = `保存しました: ${saved.subfolder}/${saved.filename}`; break; }
                if (history.status?.completed || history.status?.status_str === "error")
                    throw new Error("保存処理に失敗しました。ジョブ履歴を確認してください。");
            }
        } catch (error) { status.textContent = `保存できません: ${error.message}`; }
        finally { button.disabled = false; }
    };
}

function createPreview(node, kind) {
        if (viewers.has(node)) return;
        const labels = { profile: "視点構成", mode: "作成方法", scene_context: "背景の説明（任意）", stage: "実行段階", checkpoint: "参照画像の再利用ID" };
        if (kind === "H3OrbitPlan" || kind === "H3OrbitReferences")
            for (const widget of node.widgets || []) if (labels[widget.name]) widget.label = labels[widget.name];
        if (!["H3OrbitSave", "H3OrbitReferences", "H3OrbitGridSave", "H3OrbitRigidViews"].includes(kind)) return;
        const viewer = new OrbitViewer((filename, subfolder) =>
            api.apiURL(`/view?${new URLSearchParams({ filename, subfolder, type: "output" })}`),
        (manifest, index, row, zoom) => {
            node.properties.h3_orbit = manifest;
            node.properties.h3_orbit_index = index;
            node.properties.h3_orbit_row = row;
            node.properties.h3_orbit_zoom = zoom;
        }, async payload => {
            const response = await api.fetchApi("/h3-orbit/snapshot", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
            return result;
        }, async (payload, onProgress) => {
            const response = await api.fetchApi("/h3-orbit/export", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
            if (!result.job_id) return result; // Compatible with an export already started by the previous server.
            for (;;) {
                await new Promise(resolve => setTimeout(resolve, 1000));
                const response = await api.fetchApi(`/h3-orbit/export/${encodeURIComponent(result.job_id)}`);
                const progress = await response.json();
                if (!response.ok || progress.status === "failed")
                    throw new Error(progress.error || `HTTP ${response.status}`);
                onProgress?.(progress);
                if (progress.status === "complete") return progress;
            }
        });
        viewers.set(node, viewer);
        const widget = node.addDOMWidget("orbit_preview", "H3_ORBIT_PREVIEW", viewer.root, {
            serialize: false, hideOnZoom: false, getMinHeight: () => 380,
            getHeight: () => Math.max(380, node.size[1] - (kind === "H3OrbitReferences" ? 250 : 105)),
        });
        widget.serializeValue = () => undefined;
        const onExecuted = node.onExecuted;
        node.onExecuted = function(message) {
            const result = onExecuted?.apply(this, arguments);
            if (message?.h3_orbit?.[0]) viewer.load(message.h3_orbit[0]);
            return result;
        };
        const onRemoved = node.onRemoved;
        node.onRemoved = function() {
            viewer.dispose(); viewers.delete(this);
            return onRemoved?.apply(this, arguments);
        };
        if (node.size[0] < 580 || node.size[1] < 500)
            node.setSize([Math.max(580, node.size[0]), Math.max(500, node.size[1])]);
}

app.registerExtension({
    name: "local.H3OrbitPreview",
    afterConfigureGraph() {
        const graph = app.rootGraph;
        if (!graph || serializableGraphs.has(graph)) return;
        serializableGraphs.add(graph);
        const serialize = graph.serialize;
        graph.serialize = function() {
            const data = serialize.apply(this, arguments);
            if (data?.nodes?.some(n => n.type?.startsWith("H3Orbit"))) {
                normalizeWorkflowFields(data);
            }
            return data;
        };
    },
    nodeCreated(node) {
        createPreview(node, node.type);
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === "H3OrbitRigidScene") {
            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function(message) {
                const result = onExecuted?.apply(this, arguments);
                for (const target of this.getOutputNodes(4) || [])
                    if (target.type === "SaveGaussianSplat" && target.properties?.h3_rigid_preview)
                        applySceneSize(target, message?.h3_scene?.[0]);
                return result;
            };
        }
        if (!["H3OrbitPlan", "H3OrbitSave", "H3OrbitReferences", "H3OrbitGridSave", "H3OrbitRigidViews"].includes(nodeData.name)) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function() {
            const result = created?.apply(this, arguments);
            createPreview(this, nodeData.name);
            return result;
        };
    },
    loadedGraphNode(node) {
        createSceneSave(node);
        createPreview(node, node.type);
        const viewer = viewers.get(node);
        if (viewer && node.properties?.h3_orbit)
            viewer.load(node.properties.h3_orbit, node.properties.h3_orbit_index, node.properties.h3_orbit_row, node.properties.h3_orbit_zoom);
    },
});
