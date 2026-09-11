// SPDX-License-Identifier: MIT
// Copyright (c) 2026 FURUYAN1234
import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";

const LM_STUDIO_NODE_TYPES = new Set(["H3StandardPrompt"]);

const STATE_LABELS = {
  queued: "✓ 実行受付済み",
  running: "⏳ LM Studio処理中",
  done: "✓ LM Studio完了",
  error: "✕ LM Studio失敗",
};

let activeLmNodeId = null;
let executionStartedAfterClick = false;
let clickWarningTimer = null;
let hideStatusTimer = null;
let statusElement = null;
let lastExecuteIntentAt = 0;
let lmStatusEnabledForRun = false;

function installStatusUi() {
  if (statusElement) return statusElement;

  const style = document.createElement("style");
  style.textContent = `
    #qwen-rapid-jp-lm-status {
      position: fixed;
      z-index: 100000;
      top: 78px;
      left: 50%;
      transform: translateX(-50%);
      min-width: 360px;
      max-width: min(720px, calc(100vw - 40px));
      box-sizing: border-box;
      padding: 11px 18px;
      border: 2px solid #5caeff;
      border-radius: 9px;
      background: rgba(8, 24, 38, 0.97);
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
      color: #eaf6ff;
      font: 700 14px/20px system-ui, sans-serif;
      text-align: center;
      pointer-events: none;
      opacity: 0;
      visibility: hidden;
      transition: opacity 120ms ease;
    }
    #qwen-rapid-jp-lm-status[data-visible="true"] {
      opacity: 1;
      visibility: visible;
    }
    #qwen-rapid-jp-lm-status[data-state="running"] {
      border-color: #ffc14d;
      background: rgba(49, 34, 8, 0.97);
      color: #fff4cf;
    }
    #qwen-rapid-jp-lm-status[data-state="done"] {
      border-color: #55d98b;
      background: rgba(7, 43, 27, 0.97);
      color: #e1ffed;
    }
    #qwen-rapid-jp-lm-status[data-state="error"] {
      border-color: #ff6b73;
      background: rgba(55, 10, 14, 0.97);
      color: #ffe5e7;
    }
  `;
  document.head.appendChild(style);

  statusElement = document.createElement("div");
  statusElement.id = "qwen-rapid-jp-lm-status";
  statusElement.setAttribute("role", "status");
  statusElement.setAttribute("aria-live", "polite");
  document.body.appendChild(statusElement);
  return statusElement;
}

function showStatus(state, message, autoHideMs = 0) {
  const element = installStatusUi();
  element.dataset.state = state;
  element.dataset.visible = "true";
  element.textContent = message;
  if (hideStatusTimer) window.clearTimeout(hideStatusTimer);
  hideStatusTimer = null;
  if (autoHideMs > 0) {
    hideStatusTimer = window.setTimeout(() => {
      element.dataset.visible = "false";
    }, autoHideMs);
  }
}

function hideStatus() {
  if (hideStatusTimer) window.clearTimeout(hideStatusTimer);
  hideStatusTimer = null;
  if (statusElement) statusElement.dataset.visible = "false";
}

function isExecuteButton(button) {
  if (!button) return false;
  const label = `${button.getAttribute("aria-label") || ""} ${button.textContent || ""}`
    .replace(/\s+/g, " ")
    .trim();
  return /(?:^|\s)実行する(?:\s|$)/.test(label);
}

function executeButtonFromEvent(event) {
  const path = event.composedPath?.() || [];
  for (const candidate of path) {
    if (candidate?.matches?.("button, [role='button']") && isExecuteButton(candidate)) {
      return candidate;
    }
  }
  const candidate = event.target?.closest?.("button, [role='button']");
  return isExecuteButton(candidate) ? candidate : null;
}

function hasLmStudioWorkflow() {
  return lmStudioNodes().length > 0;
}

function promptUsesLmStudio(promptPayload) {
  const prompt = promptPayload?.output || promptPayload?.prompt || {};
  if (!prompt || typeof prompt !== "object") return false;

  return Object.values(prompt).some((node) => {
    if (!node || typeof node !== "object") return false;
    const classType = String(node.class_type || "");
    const inputs = node.inputs && typeof node.inputs === "object" ? node.inputs : {};

    if (classType === "H3StandardPrompt") {
      const brief = inputs.japanese_instruction;
      return Array.isArray(brief) || Boolean(String(brief ?? "").trim());
    }

    if (LM_STUDIO_NODE_TYPES.has(classType) || classType.toLowerCase().includes("lmstudio")) {
      return true;
    }

    if (classType === "H3PromptProviderRouter") {
      const provider = String(inputs.provider || "").toLowerCase();
      const bypassed = Boolean(inputs.prefer_existing_prompt) && Boolean(inputs.existing_h3_prompt);
      return provider.startsWith("lm studio") && !bypassed;
    }

    return false;
  });
}

function handleExecuteIntent(event) {
  if (!executeButtonFromEvent(event) || !hasLmStudioWorkflow()) return;
  const now = Date.now();
  if (now - lastExecuteIntentAt < 500) return;
  lastExecuteIntentAt = now;

  executionStartedAfterClick = false;
  setAllNodeStates("queued");
  showStatus(
    "queued",
    "✓ 実行ボタンを受け付けました。ワークフローを検証して送信しています…",
  );
  if (clickWarningTimer) window.clearTimeout(clickWarningTimer);
  clickWarningTimer = window.setTimeout(() => {
    if (!executionStartedAfterClick) {
      showStatus(
        "queued",
        "● 送信確認待ちです。もう一度押さず、そのままお待ちください。入力エラーがある場合はComfyUIが表示します。",
      );
    }
  }, 15000);
}

function isLmStudioNode(node) {
  const type = node?.comfyClass || node?.type;
  if (type === "H3StandardPrompt") {
    const input = node.inputs?.find((input) => input.name === "japanese_instruction");
    if (input?.link != null) return true;
    const brief = node.widgets?.find((widget) => widget.name === "japanese_instruction")?.value;
    return Boolean(String(brief ?? "").trim());
  }
  return LM_STUDIO_NODE_TYPES.has(type);
}

function lmStudioNodes() {
  return (app.graph?._nodes || []).filter(isLmStudioNode);
}

function setNodeState(node, state) {
  if (!node || !isLmStudioNode(node)) return;
  node.__qwenRapidJpLmState = state;
  node.__qwenRapidJpLmStartedAt = state === "running" ? Date.now() : null;
  node.setDirtyCanvas?.(true, true);
}

function setAllNodeStates(state) {
  for (const node of lmStudioNodes()) setNodeState(node, state);
}

function finishQueuedAndRunningNodes() {
  for (const node of lmStudioNodes()) {
    if (
      node.__qwenRapidJpLmState === "queued" ||
      node.__qwenRapidJpLmState === "running"
    ) {
      setNodeState(node, "done");
    }
  }
}

function nodeIdFromEvent(detail) {
  const raw = detail?.node_id ?? detail?.node ?? detail;
  if (raw === null || raw === undefined || typeof raw === "object") return null;
  return String(raw).split(":")[0];
}

function currentStatusPrefix(node) {
  const state = node.__qwenRapidJpLmState;
  if (!state) return "";
  if (state === "running" && node.__qwenRapidJpLmStartedAt) {
    const seconds = Math.max(
      0,
      Math.floor((Date.now() - node.__qwenRapidJpLmStartedAt) / 1000),
    );
    return `${STATE_LABELS.running} ${seconds}秒｜`;
  }
  return `${STATE_LABELS[state] || ""}｜`;
}

app.registerExtension({
  name: "H3StandardPrompt.LMStudioExecutionStatus",
  nodeCreated(node) {
    if (!isLmStudioNode(node)) return;
    window.setTimeout(() => {
      if (!statusElement?.textContent) {
        showStatus(
          "queued",
          "● LM Studio表示：待機中 — 「実行する」を押すと、ここに進捗が表示されます。",
        );
      }
    }, 0);
  },
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (!LM_STUDIO_NODE_TYPES.has(nodeData?.name)) return;

    const originalGetTitle = nodeType.prototype.getTitle;
    nodeType.prototype.getTitle = function () {
      const title =
        originalGetTitle?.apply?.(this, arguments) ||
        this.title ||
        nodeData.display_name ||
        nodeData.name;
      return currentStatusPrefix(this) + title;
    };
  },
  setup() {
    installStatusUi();

    document.addEventListener("pointerdown", handleExecuteIntent, true);
    document.addEventListener("click", handleExecuteIntent, true);

    if (!api.__qwenRapidJpQueuePromptWrapped) {
      const originalQueuePrompt = api.queuePrompt;
      api.queuePrompt = async function () {
        lmStatusEnabledForRun = promptUsesLmStudio(arguments[1]);
        if (!lmStatusEnabledForRun) {
          executionStartedAfterClick = false;
          if (clickWarningTimer) window.clearTimeout(clickWarningTimer);
          clickWarningTimer = null;
          hideStatus();
          return await originalQueuePrompt.apply(this, arguments);
        }

        executionStartedAfterClick = true;
        if (clickWarningTimer) window.clearTimeout(clickWarningTimer);
        clickWarningTimer = null;
        setAllNodeStates("queued");
        showStatus(
          "queued",
          "✓ ワークフローをサーバーへ送信しています。もう一度押さず、そのままお待ちください。",
        );
        try {
          return await originalQueuePrompt.apply(this, arguments);
        } catch (error) {
          setAllNodeStates("error");
          showStatus(
            "error",
            "✕ サーバーへの送信に失敗しました。ComfyUIの入力エラーを確認してください。",
            15000,
          );
          throw error;
        }
      };
      api.__qwenRapidJpQueuePromptWrapped = true;
    }

    api.addEventListener("execution_start", () => {
      if (!lmStatusEnabledForRun) return;
      executionStartedAfterClick = true;
      if (clickWarningTimer) window.clearTimeout(clickWarningTimer);
      clickWarningTimer = null;
      showStatus(
        "queued",
        "✓ ComfyUIが実行を受け付けました。LM Studioノードの開始を待っています…",
      );
      for (const node of lmStudioNodes()) {
        if (!node.__qwenRapidJpLmState || node.__qwenRapidJpLmState === "done") {
          setNodeState(node, "queued");
        }
      }
    });

    api.addEventListener("executing", ({ detail }) => {
      if (!lmStatusEnabledForRun) return;
      const nodeId = nodeIdFromEvent(detail);
      if (!nodeId) {
        finishQueuedAndRunningNodes();
        activeLmNodeId = null;
        return;
      }

      const node = app.graph?.getNodeById(nodeId);
      if (!isLmStudioNode(node)) {
        if (activeLmNodeId) {
          const previousLmNode = app.graph?.getNodeById(activeLmNodeId);
          if (isLmStudioNode(previousLmNode)) setNodeState(previousLmNode, "done");
          activeLmNodeId = null;
          showStatus("done", "✓ LM Studioのプロンプト生成が完了しました。動画生成へ進みます。", 8000);
        }
        return;
      }
      activeLmNodeId = nodeId;
      setNodeState(node, "running");
      showStatus("running", "⏳ LM Studio処理中（接続・GPU切替・変換・CPU復帰を含む）… 0秒");
    });

    api.addEventListener("executed", ({ detail }) => {
      if (!lmStatusEnabledForRun) return;
      const nodeId = nodeIdFromEvent(detail);
      const node = nodeId ? app.graph?.getNodeById(nodeId) : null;
      if (!isLmStudioNode(node)) return;
      setNodeState(node, "done");
      showStatus("done", "✓ LM Studioのプロンプト生成が完了しました。", 8000);
      if (String(activeLmNodeId) === String(nodeId)) activeLmNodeId = null;
    });

    api.addEventListener("execution_error", ({ detail }) => {
      if (!lmStatusEnabledForRun) return;
      const nodeId = nodeIdFromEvent(detail) || activeLmNodeId;
      const node = nodeId ? app.graph?.getNodeById(nodeId) : null;
      if (isLmStudioNode(node)) {
        setNodeState(node, "error");
        showStatus(
          "error",
          "✕ LM Studioノードでエラーが発生しました。画面下のエラー詳細を確認してください。",
          15000,
        );
      }
      activeLmNodeId = null;
      lmStatusEnabledForRun = false;
    });

    api.addEventListener("execution_cached", ({ detail }) => {
      if (!lmStatusEnabledForRun) return;
      const cachedNodeIds = Array.isArray(detail?.nodes)
        ? detail.nodes
        : Array.isArray(detail)
          ? detail
          : [];
      for (const nodeId of cachedNodeIds) {
        const node = app.graph?.getNodeById(String(nodeId).split(":")[0]);
        if (isLmStudioNode(node)) setNodeState(node, "done");
      }
    });

    api.addEventListener("execution_success", () => {
      if (!lmStatusEnabledForRun) return;
      finishQueuedAndRunningNodes();
      activeLmNodeId = null;
      showStatus("done", "✓ ワークフローの実行が完了しました。", 8000);
      lmStatusEnabledForRun = false;
    });

    api.addEventListener("execution_interrupted", () => {
      if (!lmStatusEnabledForRun) return;
      activeLmNodeId = null;
      lmStatusEnabledForRun = false;
      hideStatus();
    });

    window.setInterval(() => {
      const nodes = lmStudioNodes();
      if (nodes.length === 0 && !lmStatusEnabledForRun) {
        activeLmNodeId = null;
        executionStartedAfterClick = false;
        if (clickWarningTimer) window.clearTimeout(clickWarningTimer);
        clickWarningTimer = null;
        hideStatus();
        return;
      }

      for (const node of nodes) {
        if (node.__qwenRapidJpLmState === "running") {
          node.setDirtyCanvas?.(true, false);
          const seconds = Math.max(
            0,
            Math.floor((Date.now() - node.__qwenRapidJpLmStartedAt) / 1000),
          );
          showStatus(
            "running",
            `⏳ LM Studio処理中（接続・GPU切替・変換・CPU復帰を含む）… ${seconds}秒`,
          );
        }
      }
    }, 1000);
  },
});
