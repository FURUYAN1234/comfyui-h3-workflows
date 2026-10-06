// Run: node --experimental-vm-modules --test test_model_info_security.mjs
// The real modules run against isolated DOM/API fixtures; no model or server is required.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import vm from "node:vm";

class Element {
	constructor(tag) {
		this.tagName = tag.split(/[.#]/)[0].toUpperCase();
		this.children = [];
		this.style = {};
		this.dataset = {};
		this.classes = new Set(tag.split(".").slice(1));
		this.classList = {
			add: (...values) => values.forEach((value) => this.classes.add(value)),
			toggle: (value) => this.classes.has(value) ? this.classes.delete(value) : this.classes.add(value),
		};
	}
	set innerHTML(_) { throw new Error("Untrusted HTML parsing sink reached"); }
	set textContent(value) {
		this._text = String(value ?? "");
		this.children = [];
		if (this.tagName === "TEXTAREA") this.value = this._text;
	}
	get textContent() { return (this._text ?? "") + this.children.map((node) => node.textContent ?? String(node)).join(""); }
	append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
	replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
	remove() { if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); }
	after(node) { node.parentNode = this.parentNode; this.parentNode.children.splice(this.parentNode.children.indexOf(this) + 1, 0, node); }
	removeAttribute(name) { delete this[name]; }
	select() {}
}

const descendants = (element) => [element, ...element.children.flatMap(descendants)];
const find = (element, predicate) => descendants(element).find(predicate);

async function fixture() {
	const alerts = [], calls = [], shown = [], registered = [];
	const state = { response: { status: 200, statusText: "OK" }, prompt: "example.txt", refreshes: 0 };
	const api = { fetchApi: async (...args) => { calls.push(args); if (state.response instanceof Error) throw state.response; return typeof state.response === "function" ? state.response(...args) : state.response; } };
	const app = { registerExtension: (extension) => registered.push(extension), refreshComboInNodes: () => state.refreshes++ };
	function $el(tag, props = {}, children = []) {
		if (Array.isArray(props)) { children = props; props = {}; }
		const element = new Element(tag);
		for (const [key, value] of Object.entries(props)) {
			if (key === "parent") value.append(element);
			else if (key === "$") value(element);
			else element[key] = value;
		}
		element.append(...children);
		return element;
	}
	class ComfyDialog {
		constructor() { this.element = $el("div"); this.buttons = this.createButtons(); }
		createButtons() { return []; }
		show(content) { shown.push(content); }
	}
	const context = vm.createContext({
		console: { error() {} }, URL, FormData, Blob,
		File: class File extends Blob { constructor(parts, name) { super(parts); this.name = name; } },
		alert: (message) => alerts.push(message), prompt: () => state.prompt,
		fetch: async () => ({ blob: async () => new Blob(["preview"]) }),
		document: { body: $el("body") }, setTimeout,
	});
	const dependencies = { "ui.js": { $el, ComfyDialog }, "api.js": { api }, "app.js": { app }, "utils.js": { addStylesheet() {} } };
	const cache = new Map();
	for (const [name, exports] of Object.entries(dependencies)) {
		cache.set(name, new vm.SyntheticModule(Object.keys(exports), function () {
			for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
		}, { context }));
	}
	const dir = new URL("custom_nodes/ComfyUI-Custom-Scripts/web/js/", import.meta.url);
	const common = new vm.SourceTextModule(await readFile(new URL("common/modelInfoDialog.js", dir), "utf8"), { context });
	const model = new vm.SourceTextModule((await readFile(new URL("modelInfo.js", dir), "utf8")) + "\nexport { CheckpointInfoDialog };", { context });
	cache.set("modelInfoDialog.js", common);
	const linker = (specifier) => cache.get(specifier.split("/").at(-1));
	await common.link(linker);
	await common.evaluate();
	await model.link(linker);
	await model.evaluate();
	function dialog(Class = common.namespace.ModelInfoDialog, metadata = {}) {
		const instance = new Class("folder/model.safetensors", { "pysssss.updateExamples": () => state.refreshes++ });
		instance.metadata = metadata;
		instance.type = "loras";
		instance.info = $el("div"); instance.content = $el("div");
		instance.img = $el("img"); instance.imgWrapper = $el("div", [instance.img]);
		return instance;
	}
	return { ...common.namespace, ...model.namespace, alerts, calls, shown, state, dialog, $el };
}

const attack = '<img src=x onerror="globalThis.__modelInfoExecuted=true"><svg onload="alert(1)">';

test("notes display HTML literally, retain line breaks, and link only HTTP(S)", async () => {
	const f = await fixture();
	const notes = attack + '\nplain & text https://example.com/a?q=%22\nHTTP://example.org/x javascript:alert(1) data:text/html,evil';
	const tree = f.dialog(undefined, { "pysssss.notes": notes }).getNoteInfo();
	const content = find(tree, (node) => node.classes.has("pysssss-model-notes"));
	assert.equal(content.textContent, notes);
	assert(descendants(content).some((node) => node.style.whiteSpace === "pre-wrap"));
	const links = descendants(content).filter((node) => node.tagName === "A");
	assert.equal(links.length, 2);
	for (const link of links) { assert.match(link.href, /^https?:\/\//); assert.match(link.rel, /noopener/); }
	assert(!descendants(content).some((node) => ["IMG", "SVG", "SCRIPT"].includes(node.tagName)));
});

test("LoRA metadata names, usage, descriptions, tags, and example text remain inert", async () => {
	const f = await fixture();
	const d = f.dialog(f.LoraInfoDialog, {
		ss_output_name: attack, ss_sd_model_name: attack, ss_clip_skip: attack,
		"modelspec.usage_hint": attack + "\nusage", "modelspec.description": attack + "\ndescription",
		"modelspec.tags": attack, "modelspec.trigger_phrase": attack,
	});
	d.addCivitaiInfo = async () => undefined;
	await d.addInfo();
	assert(d.info.textContent.includes(attack));
	assert(d.content.textContent.includes(attack + "\ndescription"));
	assert.equal(find(d.tags, (node) => node.tagName === "P").textContent, attack);
	assert.equal(find(d.content, (node) => node.tagName === "TEXTAREA" && node.value === attack).value, attack);
	assert(!descendants(d.content).some((node) => ["IMG", "SVG", "SCRIPT"].includes(node.tagName)));
});

for (const type of ["LoraInfoDialog", "CheckpointInfoDialog"]) {
	test(`${type} renders external descriptions literally`, async () => {
		const f = await fixture();
		const d = f.dialog(f[type]);
		d.addCivitaiInfo = async () => ({ description: attack + "\nhttps://example.com/model", baseModel: attack, trainedWords: [attack] });
		await d.addInfo();
		assert(d.content.textContent.includes(attack + "\nhttps://example.com/model"));
		assert(!descendants(d.content).some((node) => ["IMG", "SVG", "SCRIPT"].includes(node.tagName)));
	});
}

test("notes save succeeds, preserves exact text, and refreshes examples", async () => {
	const f = await fixture(); const d = f.dialog(undefined, { "pysssss.notes": "before" });
	const tree = d.getNoteInfo(), edit = tree.children[0];
	await edit.onclick({ preventDefault() {}, target: edit });
	find(tree, (node) => node.tagName === "TEXTAREA").value = attack + "\nnew notes";
	await edit.onclick({ preventDefault() {}, target: edit });
	assert.equal(d.customNotes, attack + "\nnew notes");
	assert.equal(f.calls[0][1].body, d.customNotes);
	assert.equal(f.state.refreshes, 1);
	assert(!find(tree, (node) => node.tagName === "TEXTAREA"));
});

for (const response of [{ status: 400, statusText: "Bad Request" }, new Error("Network unavailable")]) {
	test(`notes save failure keeps edit text and previous notes (${response.status ?? "network"})`, async () => {
		const f = await fixture(); const d = f.dialog(undefined, { "pysssss.notes": "before" });
		const tree = d.getNoteInfo(), edit = tree.children[0];
		await edit.onclick({ preventDefault() {}, target: edit });
		find(tree, (node) => node.tagName === "TEXTAREA").value = "unsaved";
		f.state.response = response;
		await edit.onclick({ preventDefault() {}, target: edit });
		assert.equal(d.customNotes, "before");
		assert.equal(find(tree, (node) => node.tagName === "TEXTAREA").value, "unsaved");
		assert.equal(f.state.refreshes, 0);
		assert.match(f.alerts[0], /Error saving notes/);
	});
}

test("example save checks HTTP failure before reporting success", async () => {
	const f = await fixture(); const d = f.dialog(f.LoraInfoDialog);
	f.state.response = { status: 400, statusText: "Bad Request" };
	await d.saveAsExample(attack);
	assert.equal(f.state.refreshes, 0); assert(!f.alerts.includes("Saved!")); assert.match(f.alerts[0], /400/);
	f.state.response = { status: 201, statusText: "Created" };
	await d.saveAsExample(attack);
	assert.equal(f.state.refreshes, 1); assert.equal(f.alerts.at(-1), "Saved!");
	assert.deepEqual(JSON.parse(f.calls.at(-1)[1].body), { name: "example.txt", example: attack });
	assert.match(f.calls.at(-1)[0], /loras%2Ffolder%2Fmodel.safetensors$/);
});

test("Civitai preview URLs reject active/non-HTTP schemes while safe previews remain usable", async () => {
	const f = await fixture(); const d = f.dialog(); f.ModelInfoDialog.nsfwLevel = 2;
	d.getCivitaiDetails = async () => ({ modelId: 123, model: { name: attack }, images: [
		...['javascript:alert(1)', 'data:image/svg+xml,<svg onload="alert(1)">', 'file:///x.png', '//example.com/x.png', 'not a URL'].map((url) => ({ type: "image", nsfwLevel: 1, url })),
		{ type: "image", nsfwLevel: 1, url: "https://example.com/preview.png" },
	] });
	await d.addCivitaiInfo();
	assert.equal(d.img.src, "https://example.com/preview.png");
	const link = find(d.info, (node) => node.tagName === "A");
	assert.equal(link.textContent, "View " + attack); assert.match(link.rel, /noopener/);
	assert(d.imgSave);
});

test("preview upload/save failure is not reported as successful refresh", async () => {
	const f = await fixture(); const d = f.dialog(); f.ModelInfoDialog.nsfwLevel = 2;
	d.getCivitaiDetails = async () => ({ modelId: 1, model: { name: "model" }, images: [{ type: "image", nsfwLevel: 1, url: "https://example.com/preview.png" }] });
	await d.addCivitaiInfo();
	f.state.response = { status: 400, statusText: "Bad Request" };
	await d.imgSave.onclick();
	assert.equal(f.state.refreshes, 0); assert.match(f.alerts[0], /Error saving preview.*400/);
});

test("preview success checks both responses and refreshes through imported app", async () => {
	const f = await fixture(); const d = f.dialog(); f.ModelInfoDialog.nsfwLevel = 2;
	d.getCivitaiDetails = async () => ({ modelId: 1, model: { name: "model" }, images: [{ type: "image", nsfwLevel: 1, url: "https://example.com/preview.png" }] });
	await d.addCivitaiInfo();
	await d.imgSave.onclick();
	assert.equal(f.state.refreshes, 1); assert.equal(f.calls.length, 2); assert.equal(f.alerts.length, 0);
});

test("final preview-save rejection does not refresh after successful upload", async () => {
	const f = await fixture(); const d = f.dialog(); f.ModelInfoDialog.nsfwLevel = 2;
	d.getCivitaiDetails = async () => ({ modelId: 1, model: { name: "model" }, images: [{ type: "image", nsfwLevel: 1, url: "https://example.com/preview.png" }] });
	await d.addCivitaiInfo();
	f.state.response = (url) => url === "/upload/image" ? { status: 200, statusText: "OK" } : { status: 400, statusText: "Bad Request" };
	await d.imgSave.onclick();
	assert.equal(f.state.refreshes, 0); assert.equal(f.calls.length, 2); assert.match(f.alerts.at(-1), /Error saving preview.*400/);
});

test("raw metadata keys, nested values, and resolution fields remain literal text", async () => {
	const f = await fixture();
	const d = f.dialog(f.LoraInfoDialog, { [attack]: { nested: attack }, "modelspec.resolution": attack });
	d.viewMetadata.onclick();
	assert(f.shown[0].textContent.includes(attack));
	assert(f.shown[0].textContent.includes(JSON.stringify({ nested: attack })));
	d.addCivitaiInfo = async () => undefined;
	await d.addInfo();
	assert(find(d.info, (node) => node.tagName === "OPTION").textContent.includes(attack));
});
