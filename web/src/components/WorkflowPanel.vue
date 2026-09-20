<script setup lang="ts">
import type { AgentNode, GenerationResult, NodeStatus, ReferenceSource, RunMode } from "../types";

defineProps<{
  nodes: AgentNode[];
  states: Record<string, NodeStatus>;
  durations: Record<string, number>;
  details: Record<string, string>;
  references: ReferenceSource[];
  userId: string;
  running: boolean;
  elapsed: number;
  result: GenerationResult | null;
  runMode: RunMode;
}>();

function sourceKind(item: ReferenceSource) {
  return item.source === "user_memory" ? "个人素材" : "公共素材";
}

function sourceOwner(item: ReferenceSource, currentUserId: string) {
  if (item.source === "user_memory") return `用户 ${item.owner_user_id || currentUserId}`;
  return "系统公共素材库";
}

function scoreLabel(score: number) {
  return `${Math.round(Math.max(0, Math.min(1, Number(score) || 0)) * 100)}%`;
}

const labels: Record<NodeStatus, string> = {
  pending: "等待",
  running: "处理中",
  done: "完成",
  error: "异常",
  skipped: "跳过",
};

const taskLabels: Record<string, string> = {
  script_generation: "剧本正文",
  content_organize: "内容整理",
  content_organization: "内容整理",
  copywriting: "文案创作",
  qa: "资料答疑",
  content_generation: "内容创作",
  knowledge_qa: "资料答疑",
  compliance_audit: "内容审核",
};
</script>

<template>
  <section class="panel workflow-panel">
    <header class="panel-head">
      <div class="panel-title">
        <span class="step-number" aria-hidden="true">2</span>
        <div>
        <h2>查看制作进度</h2>
          <p>系统会依次理解需求、查找参考、生成并检查内容</p>
        </div>
      </div>
      <span class="live-time" :class="{ 'live-time--on': running }">
        {{ running ? `进行中 · ${elapsed}秒` : result ? "已完成" : "等待开始" }}
      </span>
    </header>

    <div class="agent-track" aria-live="polite" aria-atomic="false">
      <article v-for="(node, index) in nodes" :key="node.key" class="agent-cue" :class="`agent-cue--${states[node.key] || 'pending'}`">
        <div class="cue-line" aria-hidden="true"><span></span></div>
        <div class="cue-index">{{ String(index + 1).padStart(2, "0") }}</div>
        <div class="cue-copy">
          <div class="cue-title"><b>{{ node.name }}</b></div>
          <p class="cue-role">{{ node.role }}</p>
          <p v-if="details[node.key]" class="cue-detail">{{ details[node.key] }}</p>
        </div>
        <div class="cue-state">
          <span>{{ labels[states[node.key] || "pending"] }}</span>
          <small v-if="durations[node.key]">{{ (durations[node.key] / 1000).toFixed(1) }} 秒</small>
        </div>
      </article>
    </div>

    <details v-if="references.length" class="reference-ledger" open>
      <summary>
        <span>参考来源明细</span>
        <small>{{ references.length }} 条 · 点击收起</small>
      </summary>
      <p class="reference-privacy">个人素材仅从当前账号“{{ userId }}”中检索，不会读取其他用户的记忆。</p>
      <ol class="reference-list">
        <li v-for="(item, index) in references" :key="item.material_id || `${item.title}-${index}`">
          <header>
            <span class="reference-order">{{ String(index + 1).padStart(2, "0") }}</span>
            <strong>{{ item.title || "未命名参考" }}</strong>
            <em :class="{ 'reference-type--personal': item.source === 'user_memory' }">{{ sourceKind(item) }}</em>
          </header>
          <dl>
            <div><dt>归属</dt><dd>{{ sourceOwner(item, userId) }}</dd></div>
            <div><dt>具体出处</dt><dd>{{ item.source_path || "系统公共素材库（旧索引未记录源文件）" }}</dd></div>
            <div><dt>分类</dt><dd>{{ item.category || "未分类" }}</dd></div>
            <div><dt>素材编号</dt><dd>{{ item.material_id || "未记录" }}</dd></div>
            <div><dt>匹配度</dt><dd>{{ scoreLabel(item.score) }}</dd></div>
          </dl>
        </li>
      </ol>
    </details>

    <div class="reflection-loop" :class="{ 'reflection-loop--active': runMode === 'quality' && (result?.iteration_count || 0) > 1 }">
      <span>{{ runMode === "fast" ? "快速生成" : "自动优化" }}</span>
      <p v-if="runMode === 'fast'">生成初稿后完成一次内容检查。</p>
      <p v-else>发现问题时会自动修改，最多优化 3 次。</p>
      <b>{{ result?.iteration_count || 0 }} / {{ runMode === "fast" ? 1 : 3 }} 次</b>
    </div>

    <dl class="run-stats">
      <div><dt>内容类型</dt><dd>{{ result?.task_type ? taskLabels[result.task_type] || "内容创作" : "—" }}</dd></div>
      <div><dt>用时</dt><dd>{{ result ? `${((result.elapsed_ms || 0) / 1000).toFixed(1)}秒` : "—" }}</dd></div>
      <div><dt>字数</dt><dd>{{ result?.content ? `${result.content.length}字` : "—" }}</dd></div>
      <div><dt>检查结果</dt><dd :class="result?.audit_result?.passed ? 'stat-good' : result?.audit_result ? 'stat-warn' : ''">{{ result?.audit_result ? (result.audit_result.passed ? "通过" : "建议修改") : "—" }}</dd></div>
    </dl>
  </section>
</template>
