import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
    name: "h3_long_video.resume_saved_run",
    setup() {
        api.addEventListener("h3_long_video.resume_ready", ({ detail }) => {
            const node = app.graph.getNodeById(detail.node_id);
            if (node?.comfyClass !== "MiniMaxH3LongReferenceSampler") return;
            node.properties ||= {};
            // A replay may fail before writing its first checkpoint.  Keep the
            // last known source project instead of replacing it with that
            // unusable empty folder.
            if (!detail.resume || !node.properties.h3_resume_project) {
                node.properties.h3_resume_project = detail.project;
            }
        });
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "MiniMaxH3LongReferenceSampler") return;
        const previous = nodeType.prototype.getExtraMenuOptions;
        nodeType.prototype.getExtraMenuOptions = function (_, options) {
            previous?.apply(this, arguments);
            const project = this.properties?.h3_resume_project;
            options.push({
                content: "保存済みの素材・台詞・種で途中から再開",
                disabled: !project,
                callback: async () => {
                    try {
                        const response = await api.fetchApi("/h3_long_video/resume_inputs", {
                            method: "POST", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ project }),
                        });
                        const saved = await response.json();
                        if (!response.ok) throw new Error(saved.error);
                        const currentGraph = await app.graphToPrompt();
                        const savedNode = saved.prompt?.[String(this.id)] ?? Object.values(saved.prompt || {})
                            .find((item) => item?.class_type === "MiniMaxH3LongReferenceSampler");
                        if (!savedNode?.inputs) throw new Error("保存済みのH3生成ノードを確認できません");
                        // The recorded graph owns immutable run identity (folder,
                        // sources and seeds).  Only copy the explicit recovery
                        // controls the user has just set on the visible node.
                        // This lets a normal context-menu resume reroll a bad
                        // segment without accidentally expanding a fresh date path.
                        const currentInputs = currentGraph.output?.[String(this.id)]?.inputs || {};
                        const ensureCredentials = app.__nanoBananaH3EnsureCredentialsForRun;
                        const rerollsCloudSegment = Number(currentInputs.reroll_from_segment) >= 0;
                        if ((saved.credential_required || rerollsCloudSegment) && ensureCredentials
                                && !(await ensureCredentials())) return;
                        for (const name of ["reroll_from_segment", "reroll_feedback", "stop_after_segment"]) {
                            if (Object.hasOwn(currentInputs, name)) savedNode.inputs[name] = currentInputs[name];
                        }
                        if (saved.project) this.properties.h3_resume_project = saved.project;
                        // Queue the recorded API graph directly. Building a new
                        // graph here would advance randomize seeds and date paths.
                        await api.queuePrompt(0, { output: saved.prompt, workflow: saved.workflow });
                        app.extensionManager?.toast?.add({ severity: "info", summary: "途中再開を登録しました",
                            detail: "保存済み設定を使用します。保存先: " + (saved.project || project), life: 7000 });
                    } catch (error) {
                        app.extensionManager?.toast?.add({ severity: "error", summary: "途中再開できませんでした",
                            detail: String(error.message || error), life: 15000 });
                    }
                },
            });
        };
    },
});
