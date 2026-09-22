import { app } from "../../scripts/app.js";

function showPrompt(payload) {
    document.querySelector('[data-h3-cloud-prompt]')?.remove();
    const overlay = document.createElement('div');
    overlay.dataset.h3CloudPrompt = 'true';
    overlay.style.cssText = 'position:fixed;inset:0;background:#0009;z-index:10000;display:flex;align-items:center;justify-content:center';
    const panel = document.createElement('section');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Cloud prompt / クラウド用プロンプト');
    panel.style.cssText = 'width:880px;max-width:92vw;max-height:90vh;overflow:auto;background:#252525;color:#eee;padding:20px;border-radius:10px';
    const title = document.createElement('h2');
    title.textContent = 'Cloud prompt / クラウド用プロンプト';
    const info = document.createElement('pre');
    info.style.cssText = 'white-space:pre-wrap;font:13px sans-serif';
    info.textContent = payload.info + '\n\nExport folder / 保存フォルダー: ' + payload.folder;
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Prompt scope / コピーする範囲');
    for (const [index, section] of payload.sections.entries()) {
        const option = document.createElement('option');
        option.value = index;
        option.textContent = section.label;
        select.append(option);
    }
    const text = document.createElement('textarea');
    text.readOnly = true;
    text.setAttribute('aria-label', 'Cloud prompt text / コピーするプロンプト');
    text.style.cssText = 'display:block;width:100%;height:34vh;box-sizing:border-box;margin:10px 0';
    text.value = payload.sections[0].text;
    const status = document.createElement('span');
    status.setAttribute('role', 'status');
    const copy = document.createElement('button');
    copy.textContent = 'Copy prompt / プロンプトをコピー';
    copy.onclick = async () => {
        try {
            await navigator.clipboard.writeText(text.value);
            status.textContent = ' Copied / コピーしました';
        } catch {
            text.focus(); text.select();
            status.textContent = document.execCommand('copy') ? ' Copied / コピーしました' : ' Select text and press Ctrl+C / 選択した文章をCtrl+Cでコピーしてください';
        }
    };
    select.onchange = () => { text.value = payload.sections[Number(select.value)].text; status.textContent = ''; };
    const close = document.createElement('button');
    close.textContent = 'Close / 閉じる';
    close.style.marginLeft = '16px';
    close.onclick = () => overlay.remove();
    panel.append(title, info, select, text, copy, status, close);
    overlay.append(panel);
    document.body.append(overlay);
}

function attachPreview(node) {
    const box = document.createElement('section');
    box.setAttribute('aria-label', 'Cloud prompt preview / クラウド用プロンプト表示');
    box.style.cssText = 'display:flex;flex-direction:column;gap:8px;height:100%;padding:10px;box-sizing:border-box;background:#20262c;color:#eee;font:14px sans-serif;overflow:hidden';
    const status = document.createElement('div');
    status.setAttribute('role', 'status');
    const scope = document.createElement('select');
    scope.setAttribute('aria-label', 'Preview scope / 表示・コピーする範囲');
    const text = document.createElement('textarea');
    text.readOnly = true;
    text.setAttribute('aria-label', 'Prompt preview / プロンプト本文');
    text.style.cssText = 'flex:1;min-height:140px;width:100%;resize:none;box-sizing:border-box;color:#eee;background:#161b20;font:14px/1.5 sans-serif';
    const actions = document.createElement('div');
    actions.style.cssText = 'display:flex;gap:10px;flex-wrap:wrap';
    const copy = document.createElement('button');
    copy.textContent = 'Copy prompt / プロンプトをコピー';
    const expand = document.createElement('button');
    expand.textContent = 'Expand / 大きく表示';
    for (const button of [copy, expand]) button.style.cssText = 'padding:8px 12px;border:1px solid #789;background:#304858;color:white;border-radius:5px;cursor:pointer';
    const hint = document.createElement('small');
    hint.textContent = 'Last completed result. After changing inputs, run again. / 直近の実行結果です。入力を変更したら再実行してください。';
    actions.append(copy, expand);
    box.append(status, scope, text, actions, hint);
    const widget = node.addDOMWidget('cloud_prompt_preview', 'h3_cloud_preview', box, {serialize:false, hideOnZoom:false});
    widget.computeSize = () => [490, 380];
    widget.options.getMinHeight = () => 380;
    const selectText = () => {
        text.value = node.cloudPrompt?.sections[Number(scope.value)]?.text || '';
        status.textContent = 'Ready to copy / コピーできます';
    };
    scope.onchange = selectText;
    copy.onclick = async () => {
        if (!node.cloudPrompt) return;
        try {
            await navigator.clipboard.writeText(text.value);
            status.textContent = 'Copied / コピーしました';
        } catch {
            text.focus(); text.select();
            status.textContent = document.execCommand('copy') ? 'Copied / コピーしました' : 'Press Ctrl+C / Ctrl+Cでコピーしてください';
        }
    };
    expand.onclick = () => { if (node.cloudPrompt) showPrompt(node.cloudPrompt); };
    node.updateCloudPreview = (payload) => {
        node.cloudPrompt = payload?.sections?.length ? payload : null;
        scope.replaceChildren();
        const ready = Boolean(node.cloudPrompt);
        scope.hidden = !ready;
        actions.style.display = ready ? 'flex' : 'none';
        hint.hidden = !ready;
        hint.textContent = payload?.audit_warning || 'Last completed result. After changing inputs, run again. / 直近の実行結果です。入力を変更したら再実行してください。';
        hint.style.color = payload?.audit_warning ? '#ffd08a' : '';
        if (ready) {
            for (const [index, section] of payload.sections.entries()) {
                const option = document.createElement('option');
                option.value = index;
                option.textContent = section.label;
                scope.append(option);
            }
            selectText();
        } else {
            status.textContent = 'Waiting for result / プロンプト生成待ち';
            text.value = '';
            text.placeholder = 'Run the workflow. The final cloud prompt and Copy button will appear here. / ワークフローを実行すると、確定したクラウド用プロンプトとコピーボタンがここに表示されます。';
        }
        node.setDirtyCanvas(true, true);
    };
    node.updateCloudPreview(null);
}

app.registerExtension({
    name: 'MiniMaxH3.CloudPrompt',
    async afterConfigureGraph() {
        const graph = app.graph;
        const nodes = (graph?._nodes || []).filter(node => node.updateCloudPreview);
        if (!nodes.length || !graph.id) return;
        // Saved workflows and the new history-tab UI do not replay onExecuted.
        // Recover only the latest result with this exact workflow UUID and node ID.
        try {
            const response = await fetch('./history?max_items=64');
            if (!response.ok) return;
            const rows = Object.values(await response.json()).sort((a, b) => b.prompt[0] - a.prompt[0]);
            if (app.graph !== graph) return;
            for (const node of nodes) {
                if (node.cloudPrompt) continue;
                const row = rows.find(row => row.prompt?.[3]?.extra_pnginfo?.workflow?.id === graph.id && row.outputs?.[String(node.id)]?.cloud_prompt?.[0]);
                const payload = row?.outputs?.[String(node.id)]?.cloud_prompt?.[0];
                if (payload) node.updateCloudPreview(payload);
            }
        } catch (error) {
            console.warn('Cloud prompt history unavailable; run the workflow to create a result.', error);
        }
    },
    onNodeOutputsUpdated(outputs) {
        for (const node of app.graph?._nodes || []) {
            const payload = outputs?.[String(node.id)]?.cloud_prompt?.[0];
            if (payload && node.updateCloudPreview) node.updateCloudPreview(payload);
        }
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== 'MiniMaxH3CloudPrompt') return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            created?.apply(this, arguments);
            attachPreview(this);
            this.setSize([Math.max(this.size[0], 540), Math.max(this.size[1], 500)]);
        };
        // History/tab restoration updates ComfyUI's output cache without onExecuted.
        const drawn = nodeType.prototype.onDrawBackground;
        nodeType.prototype.onDrawBackground = function () {
            drawn?.apply(this, arguments);
            const key = this.graph?.isRootGraph === false ? `${this.graph.id}:${this.id}` : String(this.id);
            const payload = app.nodeOutputs?.[key]?.cloud_prompt?.[0];
            if (payload && payload !== this.cloudPrompt) this.updateCloudPreview(payload);
        };
        const executed = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            executed?.apply(this, arguments);
            if (message.cloud_prompt) this.updateCloudPreview(message.cloud_prompt[0]);
        };
    }
});
