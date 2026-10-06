import { $el, ComfyDialog } from "../../../../scripts/ui.js";
import { api } from "../../../../scripts/api.js";
import { app } from "../../../../scripts/app.js";
import { addStylesheet } from "./utils.js";

addStylesheet(import.meta.url);

function httpUrl(value) {
	if (typeof value !== "string" || !/^https?:\/\//i.test(value)) return null;
	try {
		const url = new URL(value);
		return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
	} catch {
		return null;
	}
}

// Model metadata, local notes, and remote descriptions are text, never HTML.
export function modelInfoText(value) {
	const text = String(value ?? "");
	const nodes = [];
	let end = 0;
	for (const match of text.matchAll(/\bhttps?:\/\/[^\s<>"']+/gi)) {
		nodes.push($el("span", { textContent: text.slice(end, match.index) }));
		const href = httpUrl(match[0]);
		nodes.push(href
			? $el("a", { href, textContent: match[0], target: "_blank", rel: "noopener noreferrer" })
			: $el("span", { textContent: match[0] }));
		end = match.index + match[0].length;
	}
	nodes.push($el("span", { textContent: text.slice(end) }));
	return $el("span", { style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere" } }, nodes);
}

class MetadataDialog extends ComfyDialog {
	constructor() {
		super();

		this.element.classList.add("pysssss-model-metadata");
	}
	show(metadata) {
		super.show(
			$el(
				"div",
				Object.keys(metadata).map((k) =>
					$el("div", [
						$el("label", { textContent: k }),
						$el("span", { textContent: typeof metadata[k] === "object" ? JSON.stringify(metadata[k]) : metadata[k] }),
					])
				)
			)
		);
	}
}

export class ModelInfoDialog extends ComfyDialog {
	constructor(name, node) {
		super();
		this.name = name;
		this.node = node;
		this.element.classList.add("pysssss-model-info");
	}

	get customNotes() {
		return this.metadata["pysssss.notes"];
	}

	set customNotes(v) {
		this.metadata["pysssss.notes"] = v;
	}

	get hash() {
		return this.metadata["pysssss.sha256"];
	}

	async show(type, value) {
		this.type = type;

		const req = api.fetchApi("/pysssss/metadata/" + encodeURIComponent(`${type}/${value}`));
		this.info = $el("div", { style: { flex: "auto" } });
		this.img = $el("img", { style: { display: "none" } });
		this.imgWrapper = $el("div.pysssss-preview", [this.img]);
		this.main = $el("main", { style: { display: "flex" } }, [this.info, this.imgWrapper]);
		this.content = $el("div.pysssss-model-content", [$el("h2", { textContent: this.name }), this.main]);

		const loading = $el("div", { textContent: "ℹ️ Loading...", parent: this.content });

		super.show(this.content);

		this.metadata = await (await req).json();
		this.viewMetadata.style.cursor = this.viewMetadata.style.opacity = "";
		this.viewMetadata.removeAttribute("disabled");

		loading.remove();
		this.addInfo();
	}

	createButtons() {
		const btns = super.createButtons();
		this.viewMetadata = $el("button", {
			type: "button",
			textContent: "View raw metadata",
			disabled: "disabled",
			style: {
				opacity: 0.5,
				cursor: "not-allowed",
			},
			onclick: (e) => {
				if (this.metadata) {
					new MetadataDialog().show(this.metadata);
				}
			},
		});

		btns.unshift(this.viewMetadata);
		return btns;
	}

	getNoteInfo() {
		function parseNote() {
			if (!this.customNotes) return [];
			return [modelInfoText(this.customNotes)];
		}

		let textarea;
		let notesContainer;
		const editText = "✏️ Edit";
		const edit = $el("a", {
			textContent: editText,
			href: "#",
			style: {
				float: "right",
				color: "greenyellow",
				textDecoration: "none",
			},
			onclick: async (e) => {
				e.preventDefault();

				if (textarea) {
					const notes = textarea.value;
					try {
						const resp = await api.fetchApi("/pysssss/metadata/notes/" + encodeURIComponent(`${this.type}/${this.name}`), {
							method: "POST",
							body: notes,
						});
						if (resp.status !== 200) throw new Error(`(${resp.status}) ${resp.statusText}`);
					} catch (error) {
						console.error(error);
						alert(`Error saving notes ${error.message}`);
						return;
					}
					this.customNotes = notes;

					e.target.textContent = editText;
					textarea.remove();
					textarea = null;

					notesContainer.replaceChildren(...parseNote.call(this));
					this.node?.["pysssss.updateExamples"]?.();
				} else {
					e.target.textContent = "💾 Save";
					textarea = $el("textarea", {
						style: {
							width: "100%",
							minWidth: "200px",
							minHeight: "50px",
						},
						textContent: this.customNotes,
					});
					e.target.after(textarea);
					notesContainer.replaceChildren();
					textarea.style.height = Math.min(textarea.scrollHeight, 300) + "px";
				}
			},
		});

		notesContainer = $el("div.pysssss-model-notes", parseNote.call(this));
		return $el(
			"div",
			{
				style: { display: "contents" },
			},
			[edit, notesContainer]
		);
	}

	addInfo() {
		const usageHint = this.metadata["modelspec.usage_hint"];
		if (usageHint) {
			this.addInfoEntry("Usage Hint", usageHint);
		}
		this.addInfoEntry("Notes", this.getNoteInfo());
	}

	addInfoEntry(name, value) {
		return $el(
			"p",
			{
				parent: this.info,
			},
			[
				typeof name === "string" ? $el("label", { textContent: name + ": " }) : name,
				typeof value === "string" ? modelInfoText(value) : value,
			]
		);
	}

	async getCivitaiDetails() {
		const req = await fetch("https://civitai.com/api/v1/model-versions/by-hash/" + encodeURIComponent(this.hash));
		if (req.status === 200) {
			return await req.json();
		} else if (req.status === 404) {
			throw new Error("Model not found");
		} else {
			throw new Error(`Error loading info (${req.status}) ${req.statusText}`);
		}
	}

	addCivitaiInfo() {
		const promise = this.getCivitaiDetails();
		const content = $el("span", { textContent: "ℹ️ Loading..." });

		this.addInfoEntry(
			$el("label", [
				$el("img", {
					style: {
						width: "18px",
						position: "relative",
						top: "3px",
						margin: "0 5px 0 0",
					},
					src: "https://civitai.com/favicon.ico",
				}),
				$el("span", { textContent: "Civitai: " }),
			]),
			content
		);

		return promise
			.then((info) => {
				content.replaceChildren(
					$el("a", {
						href: "https://civitai.com/models/" + encodeURIComponent(info.modelId),
						textContent: "View " + info.model.name,
						target: "_blank",
						rel: "noopener noreferrer",
					})
				);

				const allPreviews = info.images?.filter((i) => i.type === "image");
				const previews = allPreviews?.filter((i) => i.nsfwLevel <= ModelInfoDialog.nsfwLevel && httpUrl(i.url));
				if (previews?.length) {
					let previewIndex = 0;
					let preview;
					const updatePreview = () => {
						preview = previews[previewIndex];
						this.img.src = httpUrl(preview.url);
					};

					updatePreview();
					this.img.style.display = "";

					this.img.title = `${previews.length} previews.`;
					if (allPreviews.length !== previews.length) {
						this.img.title += ` ${allPreviews.length - previews.length} images hidden due to NSFW level or invalid URL.`;
					}

					this.imgSave = $el("button", {
						textContent: "Use as preview",
						parent: this.imgWrapper,
						onclick: async () => {
							// Convert the preview to a blob
							const blob = await (await fetch(this.img.src)).blob();

							// Store it in temp
							const name = "temp_preview." + new URL(this.img.src).pathname.split(".")[1];
							const body = new FormData();
							body.append("image", new File([blob], name));
							body.append("overwrite", "true");
							body.append("type", "temp");

							const resp = await api.fetchApi("/upload/image", {
								method: "POST",
								body,
							});

							if (resp.status !== 200) {
								console.error(resp);
								alert(`Error saving preview (${resp.status}) ${resp.statusText}`);
								return;
							}

							// Use as preview
							const saved = await api.fetchApi("/pysssss/save/" + encodeURIComponent(`${this.type}/${this.name}`), {
								method: "POST",
								body: JSON.stringify({
									filename: name,
									type: "temp",
								}),
								headers: {
									"content-type": "application/json",
								},
							});
							if (saved.status !== 200) {
								console.error(saved);
								alert(`Error saving preview (${saved.status}) ${saved.statusText}`);
								return;
							}
							app.refreshComboInNodes();
						},
					});

					$el("button", {
						textContent: "Show metadata",
						parent: this.imgWrapper,
						onclick: async () => {
							if (preview.meta && Object.keys(preview.meta).length) {
								new MetadataDialog().show(preview.meta);
							} else {
								alert("No image metadata found");
							}
						},
					});

					const addNavButton = (icon, direction) => {
						$el("button.pysssss-preview-nav", {
							textContent: icon,
							parent: this.imgWrapper,
							onclick: async () => {
								previewIndex += direction;
								if (previewIndex < 0) {
									previewIndex = previews.length - 1;
								} else if (previewIndex >= previews.length) {
									previewIndex = 0;
								}
								updatePreview();
							},
						});
					};

					if (previews.length > 1) {
						addNavButton("‹", -1);
						addNavButton("›", 1);
					}
				} else if (info.images?.length) {
					$el("span", { style: { opacity: 0.6 }, textContent: "⚠️ All images hidden due to NSFW level setting or invalid URL.", parent: this.imgWrapper });
				}

				return info;
			})
			.catch((err) => {
				content.textContent = "⚠️ " + err.message;
			});
	}
}
