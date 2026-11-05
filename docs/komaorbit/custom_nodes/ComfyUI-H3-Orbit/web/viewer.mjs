export function validManifest(m) {
    const common = m?.status === "complete" && m.type === "output"
        && /^H3_ORB360\/[A-Za-z0-9_-]+$/.test(m.subfolder)
        && [m.width, m.height, m.source_width, m.source_height].every(v => Number.isSafeInteger(v) && v > 0)
        && m.width * m.source_height === m.height * m.source_width;
    if (!common) return false;
    if (![.5, 1].includes(m.frame_basis_zoom ?? 1)) return false;
    if (m.schema_version === 1) return m.frame_count === 124 && m.fps === 24;
    if (m.schema_version !== 2 || !Array.isArray(m.elevations)) return false;
    const elevations = JSON.stringify(m.elevations);
    const rigidHeights = m.generation_profile === 'fixed3d_camera_grid' && m.pose_status === 'fixed_geometry'
        && m.elevations.length === 3 && Number.isFinite(m.elevations[0]) && m.elevations[0] >= -30 && m.elevations[0] < 0
        && m.elevations[1] === 0 && m.elevations[2] === 30;
    return (rigidHeights || elevations === "[-45,-30,-15,0,15,30,45]" || elevations === "[-30,0,30]" || elevations === "[0]")
        && m.row_count === m.elevations.length
        && Number.isInteger(m.default_row) && m.default_row === m.elevations.indexOf(0)
        && ((m.kind === "references" && m.frame_count === 4 && m.fps === 0)
            || (m.kind === "orbit_grid" && [73, 124].includes(m.frame_count) && m.fps === 24));
}

export function zoomFactor(value) {
    return Math.max(0.5, Math.min(4, Number.isFinite(value) ? value : 1));
}

export function imageScale(manifest, zoom, original = false) {
    return original ? 1 : zoom / (manifest?.frame_basis_zoom ?? 1);
}

export function frameIndex(value, count, loop = false) {
    const n = Number.isFinite(value) ? Math.round(value) : 0;
    return loop ? ((n % count) + count) % count : Math.max(0, Math.min(count - 1, n));
}

export function dragPosition(start, dx, dy, width, height, manifest, loop = true) {
    const rows = manifest.row_count || 1;
    const horizontal = loop && manifest.kind === "references" ? manifest.frame_count : manifest.frame_count - 1;
    const pixelsPerRow = Math.max(18, Math.min(56, height / Math.max(1, rows - 1)));
    return {
        index: frameIndex(start.frame + dx / Math.max(1, width) * horizontal, manifest.frame_count, loop),
        row: frameIndex(start.row - dy / pixelsPerRow, rows),
    };
}

export function frameLocation(manifest, index, row = 0) {
    if (!validManifest(manifest)) throw new Error("Invalid orbit manifest");
    if (index === "original") return { filename: "original.png", subfolder: manifest.subfolder };
    const filename = `frame_${String(frameIndex(index, manifest.frame_count)).padStart(3, "0")}.png`;
    const subfolder = manifest.schema_version === 1 ? manifest.subfolder
        : `${manifest.subfolder}/row_${String(frameIndex(row, manifest.row_count)).padStart(2, "0")}`;
    return { filename, subfolder };
}

export class OrbitViewer {
    constructor(makeUrl, persist, saveSnapshot, exportFolder) {
        this.makeUrl = makeUrl;
        this.persist = persist;
        this.saveSnapshot = saveSnapshot;
        this.exportFolder = exportFolder;
        this.index = 0;
        this.row = 0;
        this.zoom = 1;
        this.epoch = 0;
        this.request = 0;
        this.cache = new Map();
        this.abort = new AbortController();
        this.root = document.createElement("div");
        this.root.className = "h3-orbit-preview";
        this.root.style.cssText = "display:flex;flex-direction:column;gap:8px;padding:10px;box-sizing:border-box;width:100%;height:100%;min-height:300px;background:#191c22;color:#eee;font:13px sans-serif;overflow:hidden;";
        this.root.innerHTML = `<div data-help style="font-size:12px;color:#b8c0cf">生成済みデータを操作します。入力変更後は再実行してください。</div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <button type="button" data-action="reset">先頭へ</button>
          <button type="button" data-action="save">この構図で画像保存</button>
          <button type="button" data-action="export">全視点をフォルダー保存</button>
          <span data-zoom-label style="font-variant-numeric:tabular-nums">100%</span>
          <button type="button" data-action="original" aria-pressed="false">元画像と比較</button>
        </div>
        <div data-view tabindex="0" role="group" aria-label="周回画像。左右ドラッグ、または左右矢印キーで操作"
          style="position:relative;flex:1;min-height:120px;overflow:hidden;display:flex;align-items:center;justify-content:center;background:#101217;touch-action:none;cursor:grab;outline-offset:-3px">
          <div data-frame style="position:relative;flex-shrink:0;overflow:hidden;background:#101217"><img draggable="false" alt="生成した周回画像" style="display:none;max-width:100%;max-height:100%;width:100%;height:100%;object-fit:contain;pointer-events:none;user-select:none"></div>
        </div>
        <label data-checkpoint-control style="display:none;font-size:11px">参照画像の再利用ID
          <input data-checkpoint readonly aria-label="参照画像の再利用ID" style="width:100%;box-sizing:border-box;font-size:11px">
        </label>
        <div data-status role="status" aria-live="polite" style="min-height:32px;overflow-wrap:anywhere">画像を選んでワークフローを実行すると、ここで回転できます。</div>
        <div data-save-status role="status" aria-live="polite" style="font-size:11px;overflow-wrap:anywhere"></div>`;
        this.stage = this.root.querySelector("[data-view]");
        this.img = this.root.querySelector("img");
        this.frame = this.root.querySelector("[data-frame]");
        this.view = this.frame;
        this.stage.removeAttribute("role");
        this.stage.removeAttribute("aria-label");
        this.stage.removeAttribute("tabindex");
        this.stage.style.cursor = "default";
        this.view.tabIndex = 0;
        this.view.setAttribute("role", "group");
        this.view.style.cursor = "grab";
        this.view.style.touchAction = "none";
        this.saveButton = this.root.querySelector('[data-action="save"]');
        this.exportButton = this.root.querySelector('[data-action="export"]');
        this.saveStatus = this.root.querySelector('[data-save-status]');
        this.resizeObserver = new ResizeObserver(() => this.fitFrame());
        this.resizeObserver.observe(this.stage);
        this.status = this.root.querySelector("[data-status]");
        this.zoomLabel = this.root.querySelector("[data-zoom-label]");
        this.help = this.root.querySelector("[data-help]");
        this.checkpointControl = this.root.querySelector("[data-checkpoint-control]");
        this.checkpoint = this.root.querySelector("[data-checkpoint]");
        this.originalButton = this.root.querySelector('[data-action="original"]');
        const listen = (el, event, fn, options = {}) => el.addEventListener(event, fn, { ...options, signal: this.abort.signal });
        // Prevent a drag or keyboard action here from moving/deleting the graph node.
        for (const event of ["pointerdown", "pointermove", "pointerup", "dblclick", "keydown", "wheel"])
            listen(this.root, event, e => e.stopPropagation());
        listen(this.root.querySelector('[data-action="reset"]'), "click", () => {
            this.setZoom(1); this.showOriginal = false; this.show(0, this.manifest?.default_row || 0);
        });
        listen(this.originalButton, "click", () => {
            this.showOriginal = !this.showOriginal; this.show(this.index);
        });
        listen(this.checkpoint, "click", () => this.checkpoint.select());
        listen(this.saveButton, "click", () => this.save());
        listen(this.exportButton, "click", () => this.export());
        listen(this.view, "wheel", e => {
            if (!this.manifest) return;
            e.preventDefault();
            const pixels = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? this.view.clientHeight : 1);
            const base = this.showOriginal ? 1 : this.zoom;
            if (this.showOriginal) { this.showOriginal = false; this.show(this.index, this.row); }
            this.setZoom(base * Math.exp(-Math.max(-500, Math.min(500, pixels)) * 0.0015));
        }, { passive: false });
        listen(this.view, "keydown", e => {
            if (!this.manifest) return;
            const keys = { ArrowLeft: this.index - 1, ArrowRight: this.index + 1, Home: 0, End: this.manifest.frame_count - 1 };
            if (!(e.key in keys) && e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
            e.preventDefault(); this.showOriginal = false;
            if (e.key === "ArrowUp" || e.key === "ArrowDown")
                this.show(this.index, this.row + (e.key === "ArrowUp" ? 1 : -1));
            else this.show(frameIndex(keys[e.key], this.manifest.frame_count, true));
        });
        listen(this.view, "pointerdown", e => {
            if (!this.manifest || e.button !== 0) return;
            e.preventDefault(); this.view.focus();
            this.view.setPointerCapture(e.pointerId);
            this.drag = { id: e.pointerId, x: e.clientX, y: e.clientY, frame: this.index, row: this.row };
            this.view.style.cursor = "grabbing";
            this.showOriginal = false;
        });
        listen(this.view, "pointermove", e => {
            if (!this.drag || this.drag.id !== e.pointerId) return;
            const rect = this.frame.getBoundingClientRect();
            const next = dragPosition(this.drag, e.clientX - this.drag.x, e.clientY - this.drag.y,
                                      rect.width, rect.height, this.manifest, true);
            this.show(next.index, next.row);
        });
        const endDrag = e => {
            if (this.drag?.id !== e.pointerId) return;
            this.drag = null; this.view.style.cursor = "grab";
            if (this.view.hasPointerCapture(e.pointerId)) this.view.releasePointerCapture(e.pointerId);
        };
        for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) listen(this.view, event, endDrag);
        this.enable(false);
    }

    enable(enabled) {
        for (const el of this.root.querySelectorAll("button,input")) el.disabled = !enabled;
    }

    load(manifest, savedIndex = 0, savedRow = undefined, savedZoom = 1) {
        this.epoch++; this.request++; this.cache.clear(); this.drag = null;
        this.img.removeAttribute("src"); this.img.style.display = "none";
        this.manifest = null;
        if (!validManifest(manifest)) {
            this.enable(false);
            this.status.textContent = "回転データの形式が一致しません。ワークフローを再実行してください。";
            return;
        }
        this.manifest = manifest; this.enable(true); this.showOriginal = false; this.fitFrame();
        this.setZoom(savedZoom, false);
        const rows = manifest.row_count || 1;
        this.checkpointControl.style.display = manifest.kind === "references" ? "block" : "none";
        this.checkpoint.value = manifest.kind === "references" ? `${manifest.subfolder}/manifest.json` : "";
        this.help.textContent = rows > 1
            ? `左右ドラッグ＝周回、上下ドラッグ＝${rows}段階の高さ、ホイール＝拡大縮小。角度は指定値です。`
            : "左右ドラッグ＝周回、ホイール＝拡大縮小。入力変更後は再実行してください。";
        if (manifest.pose_status === "fixed_geometry")
            this.help.textContent = "体形固定：左右ドラッグで一周、上下で3段階の高さ、ホイールで構図を拡大縮小。";
        this.view.setAttribute("aria-label", rows > 1
            ? "周回画像。上下左右ドラッグ、または矢印キーで操作"
            : "周回画像。左右ドラッグ、または左右矢印キーで操作");
        const row = Number.isFinite(savedRow) ? savedRow : manifest.default_row || 0;
        this.show(frameIndex(savedIndex, manifest.frame_count), frameIndex(row, rows));
    }

    setZoom(value, persist = true) {
        this.zoom = Math.max(this.manifest?.frame_basis_zoom ?? 1, zoomFactor(value));
        this.renderZoom();
        if (persist && this.manifest) this.persist(this.manifest, this.index, this.row, this.zoom);
    }

    renderZoom() {
        this.img.style.transform = `scale(${imageScale(this.manifest, this.zoom, this.showOriginal)})`;
        this.zoomLabel.textContent = this.showOriginal ? "元画像 100%" : `${Math.round(this.zoom * 100)}%`;
    }

    sourceUrl(index) {
        const parts = index === "original" ? ["original", 0] : index.split(":").map(Number).reverse();
        const { filename, subfolder } = frameLocation(this.manifest, parts[0], parts[1]);
        return this.makeUrl(filename, subfolder);
    }

    loadImage(key) {
        if (this.cache.has(key)) {
            const entry = this.cache.get(key);
            this.cache.delete(key); this.cache.set(key, entry);
            return entry;
        }
        const image = new Image();
        const entry = { image, loaded: null };
        entry.loaded = new Promise((resolve, reject) => {
            image.onload = () => resolve(image);
            image.onerror = () => reject(new Error("画像を読み込めません"));
        });
        image.src = this.sourceUrl(key);
        this.cache.set(key, entry);
        while (this.cache.size > 6) this.cache.delete(this.cache.keys().next().value);
        return entry;
    }

    show(value, rowValue = this.row) {
        if (!this.manifest || this.disposed) return;
        const index = frameIndex(value, this.manifest.frame_count);
        const row = frameIndex(rowValue, this.manifest.row_count || 1);
        const key = this.showOriginal ? "original" : `${row}:${index}`;
        this.renderZoom();
        if (this.currentKey === key && this.currentEpoch === this.epoch) return;
        this.currentKey = key; this.currentEpoch = this.epoch;
        this.index = index; this.row = row; this.saveButton.disabled = true;
        this.originalButton.setAttribute("aria-pressed", String(!!this.showOriginal));
        ++this.request;
        this.status.textContent = "読み込み中…";
        this.drain();
    }

    async drain() {
        if (this.loading) return;
        this.loading = true;
        // At most one outstanding image decode, even during a fast pointer drag.
        while (this.manifest && !this.disposed) {
            const request = this.request, epoch = this.epoch, key = this.currentKey, index = this.index, row = this.row;
            try {
                const { image, loaded } = this.loadImage(key);
                await loaded;
                if (this.disposed) break;
                if (request !== this.request || epoch !== this.epoch) continue;
                this.img.src = image.src; this.img.style.display = "block";
                const m = this.manifest;
                this.status.textContent = key === "original"
                    ? `元画像 ${m.source_width} × ${m.source_height} px`
                    : m.kind === "references"
                        ? `参照 ${index + 1} / 4 · 方位指定 ${index * 90}° · ${this.rowLabel(row)} · ${m.width} × ${m.height} px`
                        : `フレーム ${index + 1} / ${m.frame_count} · 周回位置 ${Math.round(index / (m.frame_count - 1) * 100)}%${m.elevations ? " · " + this.rowLabel(row) : ""} · ${m.width} × ${m.height} px`;
                this.img.alt = key === "original" ? "入力した元画像" : this.status.textContent;
                this.persist(m, index, row, this.zoom);
                this.saveButton.disabled = false;
            } catch {
                if (this.disposed) break;
                if (request !== this.request || epoch !== this.epoch) continue;
                this.cache.delete(key); this.currentKey = null;
                this.img.style.display = "none";
                this.status.textContent = "画像を読み込めません。保存先の移動・削除とComfyUIの接続を確認してください。";
            }
            break;
        }
        this.loading = false;
    }

    rowLabel(row) {
        const value = this.manifest.elevations?.[row];
        return value === undefined ? "入力高さ" : value === 0 ? "水平 0°指定" : `${value < 0 ? "見上げ" : "見下ろし"} ${value > 0 ? "+" : ""}${Number(value.toFixed(1))}°指定`;
    }

    fitFrame() {
        if (!this.manifest) return;
        const ratio = this.manifest.width / this.manifest.height;
        const width = Math.min(this.stage.clientWidth, this.stage.clientHeight * ratio);
        this.frame.style.width = `${width}px`;
        this.frame.style.height = `${width / ratio}px`;
    }

    async save() {
        if (!this.manifest || this.saveButton.disabled || !this.saveSnapshot) return;
        const epoch = this.epoch, request = this.request;
        this.saveButton.disabled = true;
        const payload = { subfolder: this.manifest.subfolder, index: this.index, row: this.row,
                          zoom: this.showOriginal ? 1 : this.zoom, original: !!this.showOriginal };
        this.saveStatus.textContent = "この構図をPNGに保存中…";
        try {
            const result = await this.saveSnapshot(payload);
            if (!this.disposed && epoch === this.epoch) {
                this.saveStatus.replaceChildren();
                const link = document.createElement("a");
                const slash = result.path.lastIndexOf("/");
                link.href = this.makeUrl(result.path.slice(slash + 1), result.path.slice(0, slash));
                link.target = "_blank"; link.rel = "noopener";
                link.textContent = "保存したPNGを開く"; link.style.color = "#a8ceff";
                this.saveStatus.append(link, ` · ${result.path}`);
            }
        } catch (error) {
            if (!this.disposed && epoch === this.epoch) this.saveStatus.textContent = `保存できません: ${error.message}`;
        } finally {
            if (!this.disposed && epoch === this.epoch && request === this.request) this.saveButton.disabled = false;
        }
    }

    async export() {
        if (!this.manifest || this.exportButton.disabled || !this.exportFolder) return;
        const epoch = this.epoch;
        const payload = { subfolder: this.manifest.subfolder, index: this.index, row: this.row,
                          zoom: this.zoom, original: false };
        this.exportButton.disabled = true;
        this.saveStatus.textContent = `全${(this.manifest.row_count || 1) * this.manifest.frame_count}枚を倍率${this.zoom.toFixed(3)}xで保存中…`;
        try {
            const result = await this.exportFolder(payload, progress => {
                if (!this.disposed && epoch === this.epoch)
                    this.saveStatus.textContent = `フォルダーへ保存中 ${progress.count || 0} / ${progress.total || "…"}枚`;
            });
            if (!this.disposed && epoch === this.epoch)
                this.saveStatus.textContent = `${result.count}枚を保存しました: ${result.path}`;
        } catch (error) {
            if (!this.disposed && epoch === this.epoch) this.saveStatus.textContent = `保存できません: ${error.message}`;
        } finally {
            if (!this.disposed && epoch === this.epoch) this.exportButton.disabled = false;
        }
    }

    dispose() {
        this.disposed = true; this.request++; this.epoch++;
        this.abort.abort(); this.resizeObserver.disconnect(); this.cache.clear(); this.img.removeAttribute("src"); this.root.remove();
    }
}
