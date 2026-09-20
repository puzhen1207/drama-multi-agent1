<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ApiClient } from "./api";
import AuthGate from "./components/AuthGate.vue";
import BaseModal from "./components/BaseModal.vue";
import BriefPanel from "./components/BriefPanel.vue";
import EventLog from "./components/EventLog.vue";
import MemoryEditModal from "./components/MemoryEditModal.vue";
import MemoryModal from "./components/MemoryModal.vue";
import ResultPanel from "./components/ResultPanel.vue";
import SettingsModal from "./components/SettingsModal.vue";
import StatusHeader from "./components/StatusHeader.vue";
import WorkflowPanel from "./components/WorkflowPanel.vue";
import type {
  AgentNode,
  GenerationResult,
  HealthState,
  MemoryItem,
  NodeStatus,
  ReviewScores,
  ReferenceSource,
  RunMode,
  TimelineEvent,
} from "./types";

const nodes: AgentNode[] = [
  { key: "parse_node", name: "理解需求", role: "梳理题材、风格和篇幅", mark: "" },
  { key: "retrieve_node", name: "查找参考", role: "查找可用素材和历史内容", mark: "" },
  { key: "polish_node", name: "生成内容", role: "根据你的要求完成创作", mark: "" },
  { key: "audit_node", name: "内容检查", role: "检查风险并给出建议", mark: "" },
];

const aliases: Record<string, string> = {
  script_node: "polish_node",
  copywriting_node: "polish_node",
  organize_node: "polish_node",
  qa_node: "polish_node",
  rewrite_node: "polish_node",
  audit_input_node: "polish_node",
};

const input = ref("");
const runMode = ref<RunMode>(
  localStorage.getItem("drama_run_mode") === "quality" ? "quality" : "fast",
);
const running = ref(false);
const elapsed = ref(0);
const result = ref<GenerationResult | null>(null);
const lastQuestion = ref("");
const memorySaved = ref(false);
const reviewSubmitted = ref(false);
const runSerial = ref(0);
const previousUserId = localStorage.getItem("drama_user_id") || "";
const suggestedUserId = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(previousUserId) ? previousUserId : "";
const userId = ref("");
const sessionId = ref(localStorage.getItem("drama_session_id") || "");
const apiToken = ref(localStorage.getItem("drama_api_token") || "");
const authChecking = ref(true);
const authBusy = ref(false);
const authError = ref("");

const health = reactive<HealthState>({
  online: false,
  checking: true,
  llmConfigured: false,
  model: "",
  embeddingDegraded: false,
  embeddingHint: "",
});

const nodeStates = reactive<Record<string, NodeStatus>>({});
const nodeDurations = reactive<Record<string, number>>({});
const nodeDetails = reactive<Record<string, string>>({});
const referenceSources = ref<ReferenceSource[]>([]);
const events = ref<TimelineEvent[]>([]);
const toasts = ref<Array<{ id: number; message: string; type: string }>>([]);
const memories = ref<MemoryItem[]>([]);
const memoryCount = ref(0);
const memoryLoading = ref(false);
const showMemories = ref(false);
const showSettings = ref(false);
const showSaveConfirm = ref(false);
const showEdit = ref(false);
const editItem = ref<MemoryItem | null>(null);

const api = new ApiClient(() => apiToken.value);
let activeController: AbortController | null = null;
let elapsedTimer: number | undefined;
let eventId = 0;
let toastId = 0;

const progress = computed(() => {
  if (!running.value && result.value?.content) return 100;
  const complete = nodes.filter((node) => ["done", "skipped"].includes(nodeStates[node.key] || "pending")).length;
  const active = nodes.some((node) => nodeStates[node.key] === "running") ? 0.5 : 0;
  return Math.min(96, Math.round(((complete + active) / nodes.length) * 100));
});

const modeHint = computed(() => {
  if (!health.online) return "服务暂不可用，请稍后刷新页面";
  if (!health.llmConfigured) return "当前为体验模式，生成内容仅用于功能演示";
  return runMode.value === "fast"
    ? "快速生成：更快获得初稿"
    : "精细生成：发现问题时自动优化";
});

watch(runMode, (value) => localStorage.setItem("drama_run_mode", value));

function toast(message: string, type = "") {
  const id = ++toastId;
  toasts.value.push({ id, message, type });
  window.setTimeout(() => {
    toasts.value = toasts.value.filter((item) => item.id !== id);
  }, 3200);
}

function resetRun() {
  for (const key of Object.keys(nodeStates)) delete nodeStates[key];
  for (const key of Object.keys(nodeDurations)) delete nodeDurations[key];
  for (const key of Object.keys(nodeDetails)) delete nodeDetails[key];
  events.value = [];
  referenceSources.value = [];
  result.value = null;
  memorySaved.value = false;
  reviewSubmitted.value = false;
  runSerial.value += 1;
}

function pushEvent(type: string, detail: string) {
  const labels: Record<string, string> = {
    start: "开始生成",
    workflow_start: "开始生成",
    node_start: "开始处理",
    node_done: "处理完成",
    node_error: "处理遇到问题",
    workflow_done: "生成结束",
    workflow_complete: "生成结束",
    final: "内容已生成",
    error: "生成遇到问题",
  };
  events.value.unshift({
    id: ++eventId,
    type,
    label: labels[type] || type,
    detail,
    time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
  });
  if (events.value.length > 80) events.value.length = 80;
}

function updateNode(rawKey: string, status: NodeStatus, duration?: number) {
  const key = aliases[rawKey] || rawKey;
  if (!nodes.some((node) => node.key === key)) return;
  nodeStates[key] = status;
  if (status === "running") delete nodeDurations[key];
  if (duration) nodeDurations[key] = duration;
}

function nodeDisplayName(rawKey: string) {
  const key = aliases[rawKey] || rawKey;
  return nodes.find((node) => node.key === key)?.name || "当前步骤";
}

const runningDetails: Record<string, string> = {
  parse_node: "正在识别内容类型、主题、风格和篇幅……",
  retrieve_node: "正在匹配素材库、历史内容和个人素材……",
  polish_node: "正在结合需求与参考资料撰写正文……",
  audit_node: "正在检查内容质量、风险和修改建议……",
};

const taskTypeNames: Record<string, string> = {
  script_generation: "剧本正文",
  content_organize: "内容整理",
  content_organization: "内容整理",
  copywriting: "文案创作",
  qa: "资料答疑",
  content_generation: "内容创作",
  knowledge_qa: "资料答疑",
  compliance_audit: "内容审核",
  audit: "内容审核",
};

function nodeKey(rawKey: string) {
  return aliases[rawKey] || rawKey;
}

function setNodeDetail(rawKey: string, detail: string) {
  const key = nodeKey(rawKey);
  if (nodes.some((node) => node.key === key)) nodeDetails[key] = detail;
}

function readableNodeSummary(rawKey: string, rawSummary: unknown) {
  const key = nodeKey(rawKey);
  const summary = String(rawSummary || "").trim();
  if (!summary) return "该步骤已处理完成";

  if (key === "parse_node") {
    const match = summary.match(/^类型=([^,]+),\s*主题=(.*)$/);
    if (match) {
      const type = taskTypeNames[match[1]] || "内容创作";
      return match[2] ? `已识别为${type}，主题：${match[2]}` : `已识别为${type}`;
    }
  }
  if (key === "retrieve_node") {
    const count = summary.match(/(\d+)\s*条/);
    if (count) return `已找到 ${count[1]} 条可用参考`;
  }
  if (key === "polish_node") {
    const count = summary.match(/(\d+)\s*字/);
    if (count) return `已形成 ${count[1]} 字内容初稿`;
  }
  if (key === "audit_node") {
    const match = summary.match(/passed=(true|false),\s*score=([^,]+),\s*issues=(\d+)/i);
    if (match) {
      const outcome = match[1].toLowerCase() === "true" ? "检查通过" : "建议继续修改";
      return `${outcome}，得分 ${match[2]}，发现 ${match[3]} 项建议`;
    }
  }
  return summary;
}

function storeSession(id?: string) {
  if (!id) return;
  sessionId.value = id;
  localStorage.setItem("drama_session_id", id);
}

function acceptResult(data: GenerationResult) {
  result.value = data;
  if (data.reference_sources?.length) referenceSources.value = data.reference_sources;
  storeSession(data.session_id);
}

function handleStreamEvent(event: Record<string, any>) {
  const type = String(event.type || "message");
  if (type === "start" || type === "workflow_start") {
    storeSession(event.session_id);
    pushEvent(type, `输入：${String(event.input || lastQuestion.value).slice(0, 100)}`);
  } else if (type === "node_start") {
    const rawKey = String(event.node || "");
    updateNode(rawKey, "running");
    const detail = runningDetails[nodeKey(rawKey)] || "正在处理当前内容……";
    setNodeDetail(rawKey, detail);
    pushEvent(type, `${nodeDisplayName(rawKey)}：${detail.replace(/……$/, "")}`);
  } else if (type === "node_done") {
    const rawKey = String(event.node || "");
    const detail = readableNodeSummary(rawKey, event.summary);
    updateNode(rawKey, "done", Number(event.duration_ms || 0));
    setNodeDetail(rawKey, detail);
    if (nodeKey(rawKey) === "retrieve_node" && Array.isArray(event.references)) {
      referenceSources.value = event.references as ReferenceSource[];
    }
    pushEvent(type, `${nodeDisplayName(rawKey)}：${detail} · ${((event.duration_ms || 0) / 1000).toFixed(1)} 秒`);
  } else if (type === "node_error") {
    const rawKey = String(event.node || "");
    const detail = String(event.error || "当前步骤处理失败，请稍后重试");
    updateNode(rawKey, "error");
    setNodeDetail(rawKey, detail);
    pushEvent(type, `${nodeDisplayName(rawKey)}：${detail}`);
  } else if (type === "workflow_done" || type === "workflow_complete") {
    pushEvent(type, event.elapsed_ms ? `总耗时 ${(event.elapsed_ms / 1000).toFixed(1)} 秒` : "所有步骤已处理完毕");
  } else if (type === "final") {
    acceptResult((event.data || event) as GenerationResult);
    pushEvent(type, "最终内容已输出");
  } else if (type === "error") {
    pushEvent(type, String(event.message || event.error || "未知错误"));
  }
}

function payload() {
  const value: Record<string, unknown> = {
    raw_input: input.value.trim(),
    user_id: userId.value,
    run_mode: runMode.value,
  };
  if (sessionId.value) value.session_id = sessionId.value;
  return value;
}

function startRun() {
  resetRun();
  running.value = true;
  elapsed.value = 0;
  lastQuestion.value = input.value.trim();
  activeController = new AbortController();
  elapsedTimer = window.setInterval(() => { elapsed.value += 1; }, 1000);
}

async function finishRun() {
  running.value = false;
  activeController = null;
  if (elapsedTimer) window.clearInterval(elapsedTimer);
  elapsedTimer = undefined;
  await checkHealth();
}

async function runStream() {
  if (running.value || !input.value.trim()) return;
  startRun();
  let sawRetrieve = false;
  let streamError = "";
  try {
    await api.stream(payload(), (event) => {
      if (event.node === "retrieve_node") sawRetrieve = true;
      if (event.type === "error") streamError = String(event.message || event.error || "生成失败");
      handleStreamEvent(event);
    }, activeController?.signal);
    if (!sawRetrieve) updateNode("retrieve_node", "skipped");
    if (!result.value?.content) {
      const message = streamError || "本次没有生成出可用内容，请稍后重试";
      if (message.includes("会话不属于当前用户")) {
        sessionId.value = "";
        localStorage.removeItem("drama_session_id");
      }
      acceptResult({ error: message });
      toast(message, "error");
    } else {
      toast("生成完成，可在结果区查看", "success");
    }
  } catch (error) {
    const cancelled = error instanceof DOMException && error.name === "AbortError";
    const message = cancelled ? "生成已停止" : error instanceof Error ? error.message : "生成失败";
    pushEvent(cancelled ? "workflow_done" : "error", message);
    acceptResult({ error: message });
    toast(message, cancelled ? "" : "error");
  } finally {
    await finishRun();
  }
}

function cancelRun() {
  activeController?.abort();
}

function clearAll() {
  input.value = "";
  lastQuestion.value = "";
  resetRun();
}

async function checkHealth() {
  health.checking = true;
  try {
    const data = await api.health();
    health.online = true;
    health.llmConfigured = Boolean(data.llm?.configured);
    health.model = String(data.llm?.model || "");
    health.embeddingDegraded = Boolean(data.embedding?.degraded);
    health.embeddingHint = String(data.embedding?.hint || "");
  } catch {
    health.online = false;
    health.llmConfigured = false;
    health.model = "";
  } finally {
    health.checking = false;
  }
}

async function loadMemories(open = false) {
  if (open) showMemories.value = true;
  memoryLoading.value = true;
  try {
    const data = await api.listMemories(userId.value, true);
    memoryCount.value = data.total || 0;
    memories.value = data.memories || [];
  } catch (error) {
    if (open) toast(error instanceof Error ? error.message : "素材读取失败", "error");
  } finally {
    memoryLoading.value = false;
  }
}

async function saveMemory() {
  if (!result.value?.content || !lastQuestion.value) return;
  try {
    const data = await api.saveMemory({
      user_id: userId.value,
      question: lastQuestion.value,
      answer: result.value.content,
      session_id: sessionId.value || null,
      title: lastQuestion.value.slice(0, 40),
    });
    memoryCount.value = data.total || memoryCount.value + 1;
    memorySaved.value = true;
    showSaveConfirm.value = false;
    toast("已保存到我的素材", "success");
  } catch (error) {
    toast(error instanceof Error ? error.message : "保存失败", "error");
  }
}

function beginEdit(item: MemoryItem) {
  editItem.value = item;
  showEdit.value = true;
}

async function updateMemory(item: MemoryItem) {
  if (item.question.trim().length < 2 || (item.answer || "").trim().length < 20) {
    toast("提问至少2字，回答至少20字", "error");
    return;
  }
  try {
    await api.updateMemory(item.memory_id, {
      user_id: userId.value,
      title: item.title || null,
      question: item.question,
      answer: item.answer,
    });
    showEdit.value = false;
    await loadMemories();
    toast("素材已更新", "success");
  } catch (error) {
    toast(error instanceof Error ? error.message : "更新失败", "error");
  }
}

async function deleteMemory(item: MemoryItem) {
  if (!window.confirm(`确定删除“${item.title || item.question.slice(0, 20)}”？`)) return;
  try {
    const data = await api.deleteMemory(item.memory_id, userId.value);
    memoryCount.value = data.total;
    await loadMemories();
    toast("素材已删除", "success");
  } catch (error) {
    toast(error instanceof Error ? error.message : "删除失败", "error");
  }
}

function acceptAuth(nextUserId: string, token: string) {
  userId.value = nextUserId;
  apiToken.value = token;
  sessionId.value = "";
  localStorage.setItem("drama_user_id", nextUserId);
  localStorage.setItem("drama_api_token", token);
  localStorage.removeItem("drama_session_id");
  resetRun();
}

function clearAuth() {
  userId.value = "";
  apiToken.value = "";
  sessionId.value = "";
  memoryCount.value = 0;
  memories.value = [];
  showSettings.value = false;
  showMemories.value = false;
  localStorage.removeItem("drama_user_id");
  localStorage.removeItem("drama_api_token");
  localStorage.removeItem("drama_session_id");
  resetRun();
}

async function authenticate(mode: "login" | "register", username: string, password: string) {
  authBusy.value = true;
  authError.value = "";
  try {
    const data = mode === "login"
      ? await api.login(username, password)
      : await api.register(username, password);
    acceptAuth(data.user_id, data.token);
    await Promise.all([checkHealth(), loadMemories()]);
    toast(mode === "login" ? "登录成功" : "账号已创建", "success");
  } catch (error) {
    authError.value = error instanceof Error ? error.message : "认证失败，请稍后重试";
  } finally {
    authBusy.value = false;
  }
}

async function restoreAuth() {
  if (!apiToken.value) {
    authChecking.value = false;
    return;
  }
  try {
    const data = await api.me();
    userId.value = data.user_id;
    localStorage.setItem("drama_user_id", data.user_id);
    await Promise.all([checkHealth(), loadMemories()]);
  } catch {
    clearAuth();
  } finally {
    authChecking.value = false;
  }
}

async function logout() {
  if (running.value) activeController?.abort();
  try {
    await api.logout();
  } catch {
    // Local logout must still complete when the session already expired.
  } finally {
    clearAuth();
  }
}

async function exportMemories() {
  try {
    const data = await api.exportMemories(userId.value);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(blob);
    anchor.download = `drama_memories_${userId.value}_${new Date().toISOString().slice(0, 10)}.json`;
    anchor.click();
    URL.revokeObjectURL(anchor.href);
    toast("素材已导出", "success");
  } catch (error) {
    toast(error instanceof Error ? error.message : "导出失败", "error");
  }
}

async function importMemories(file: File) {
  try {
    const parsed = JSON.parse(await file.text());
    const items = parsed.memories || parsed;
    if (!Array.isArray(items)) throw new Error("文件中没有有效的素材列表");
    const data = await api.importMemories(userId.value, items);
    memoryCount.value = data.total;
    await loadMemories();
    toast(`导入完成：新增${data.imported || 0}条，跳过${data.skipped || 0}条`, "success");
  } catch (error) {
    toast(error instanceof Error ? error.message : "导入失败", "error");
  }
}

async function submitReview(scores: ReviewScores) {
  if (Object.entries(scores).some(([key, value]) => key !== "notes" && !value)) {
    toast("请完成五项评分后再保存", "error");
    return;
  }
  try {
    await api.submitReview(userId.value, sessionId.value, scores);
    reviewSubmitted.value = true;
    toast("评分已保存", "success");
  } catch (error) {
    toast(error instanceof Error ? error.message : "评分提交失败", "error");
  }
}

async function copyResult() {
  if (!result.value?.content) return;
  try {
    await navigator.clipboard.writeText(result.value.content);
    toast("全文已复制", "success");
  } catch {
    toast("浏览器未允许访问剪贴板", "error");
  }
}

function downloadResult() {
  if (!result.value?.content) return;
  const blob = new Blob([result.value.content], { type: "text/plain;charset=utf-8" });
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(blob);
  anchor.download = `drama_output_${new Date().toISOString().slice(0, 10)}.txt`;
  anchor.click();
  URL.revokeObjectURL(anchor.href);
  toast("下载已开始", "success");
}

onMounted(restoreAuth);

onBeforeUnmount(() => {
  activeController?.abort();
  if (elapsedTimer) window.clearInterval(elapsedTimer);
});
</script>

<template>
  <div v-if="authChecking" class="auth-loading" role="status">
    <div class="clapper-mark" aria-hidden="true"><span></span><span></span><span></span></div>
    <p>正在确认登录状态…</p>
  </div>

  <AuthGate v-else-if="!userId" :busy="authBusy" :error="authError" :suggested-username="suggestedUserId" @authenticate="authenticate" />

  <template v-else>
    <div class="app-shell" :aria-busy="running">
    <StatusHeader
      :health="health"
      :memory-count="memoryCount"
      :user-id="userId"
      :running="running"
      @memory="loadMemories(true)"
      @settings="showSettings = true"
    />

    <div class="run-progress" :class="{ 'run-progress--active': running }" aria-hidden="true">
      <span :style="{ width: `${progress}%` }"></span>
    </div>

    <main class="studio-grid">
      <BriefPanel v-model="input" v-model:run-mode="runMode" :running="running" @stream="runStream" @cancel="cancelRun" @clear="clearAll" />
      <WorkflowPanel :nodes="nodes" :states="nodeStates" :durations="nodeDurations" :details="nodeDetails" :references="referenceSources" :user-id="userId" :running="running" :elapsed="elapsed" :result="result" :run-mode="runMode" />
      <EventLog :events="events" :running="running" />
      <ResultPanel
        :key="runSerial"
        :result="result"
        :running="running"
        :can-save="Boolean(result?.content && lastQuestion && !memorySaved)"
        :review-submitted="reviewSubmitted"
        @copy="copyResult"
        @download="downloadResult"
        @save="showSaveConfirm = true"
        @review="submitReview"
      />
    </main>

    <footer class="studio-footer">
      <p>{{ modeHint }}</p>
      <p>生成内容请在发布前人工确认</p>
    </footer>
  </div>

  <MemoryModal :open="showMemories" :loading="memoryLoading" :memories="memories" @close="showMemories = false" @edit="beginEdit" @delete="deleteMemory" />
  <MemoryEditModal :open="showEdit" :item="editItem" @close="showEdit = false" @save="updateMemory" />
  <SettingsModal :open="showSettings" :user-id="userId" @close="showSettings = false" @logout="logout" @export="exportMemories" @import="importMemories" />

  <BaseModal :open="showSaveConfirm" title="保存到我的素材" @close="showSaveConfirm = false">
    <p class="modal-intro">确认后，这组内容会保存下来，之后创作相似内容时可作为参考。</p>
    <div class="save-preview"><b>提问</b><p>{{ lastQuestion }}</p><b>回答预览</b><p>{{ result?.content?.slice(0, 420) }}{{ (result?.content?.length || 0) > 420 ? "……" : "" }}</p></div>
    <template #footer>
      <button class="button button--quiet" type="button" @click="showSaveConfirm = false">暂不保存</button>
      <button class="button button--primary" type="button" @click="saveMemory">确认保存</button>
    </template>
  </BaseModal>

  <div class="toast-stack" role="status" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="item in toasts" :key="item.id" class="toast" :class="`toast--${item.type}`">{{ item.message }}</div>
    </TransitionGroup>
  </div>
  </template>
</template>
