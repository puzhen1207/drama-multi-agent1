<script setup lang="ts">
import { reactive } from "vue";
import type { AuditIssue, GenerationResult, ReviewScores } from "../types";

defineProps<{
  result: GenerationResult | null;
  running: boolean;
  canSave: boolean;
  reviewSubmitted: boolean;
}>();

const emit = defineEmits<{
  copy: [];
  download: [];
  save: [];
  review: [scores: ReviewScores];
}>();

const review = reactive<ReviewScores>({
  hook: null,
  pacing: null,
  character_consistency: null,
  shootability: null,
  compliance: null,
  notes: "",
});

const scoreFields: Array<[keyof Omit<ReviewScores, "notes">, string]> = [
  ["hook", "开头钩子"],
  ["pacing", "剧情节奏"],
  ["character_consistency", "人物一致性"],
  ["shootability", "可拍摄性"],
  ["compliance", "内容合规"],
];

function issueText(issue: AuditIssue | string) {
  if (typeof issue === "string") return issue;
  return issue.suggestion || issue.category || "待人工复核";
}

function issueLevel(issue: AuditIssue | string) {
  return typeof issue === "string" ? "warning" : issue.level || "warning";
}

function issueLevelText(issue: AuditIssue | string) {
  const level = issueLevel(issue);
  if (level === "info") return "提示";
  if (level === "warning") return "注意";
  return "风险";
}

function submitReview() {
  emit("review", { ...review });
}
</script>

<template>
  <section class="panel result-panel">
    <header class="panel-head result-head">
      <div class="panel-title">
        <span class="step-number" aria-hidden="true">4</span>
        <div>
        <h2>查看生成结果</h2>
          <p>阅读正文、检查建议并保存需要的内容</p>
        </div>
      </div>
      <span class="result-state" :class="{ 'result-state--ready': result?.content, 'result-state--working': running }">
        {{ running ? "生成中" : result?.content ? `${result.content.length} 字` : result?.error ? "生成失败" : "尚无结果" }}
      </span>
    </header>

    <div v-if="!result?.content" class="result-empty">
      <div class="paper-stack" aria-hidden="true"><span></span><span></span><span></span></div>
      <h3>{{ result?.error ? "本次没有生成成功" : "内容会显示在这里" }}</h3>
      <p>{{ result?.error || "填写需求并开始生成，完成后可查看正文和检查建议。" }}</p>
    </div>

    <template v-else>
      <div class="result-toolbar">
        <button class="button button--quiet" type="button" @click="emit('copy')">复制全文</button>
        <button class="button button--quiet" type="button" @click="emit('download')">下载 TXT</button>
        <button v-if="canSave" class="button button--memory" type="button" @click="emit('save')">保存到我的素材</button>
      </div>

      <article class="script-paper">
        <div class="paper-ruler"><span>正文</span><span>{{ new Date().toLocaleDateString("zh-CN") }}</span></div>
        <div class="script-copy">{{ result.content }}</div>
      </article>

      <article v-if="result.audit_result" class="audit-card" :class="result.audit_result.passed ? 'audit-card--pass' : 'audit-card--warn'">
        <header>
          <div>
            <span class="eyebrow">内容检查</span>
            <h3>{{ result.audit_result.passed ? "检查通过" : "建议修改" }}</h3>
          </div>
          <strong>{{ result.audit_result.score?.toFixed(2) ?? "—" }}</strong>
        </header>
        <p class="audit-summary">
          {{ result.audit_result.summary || (result.audit_result.passed ? "未发现需要阻止发布的问题。" : "请根据下列问题调整内容。") }}
          <span> · {{ result.audit_result.degrade_mode ? "已完成基础检查" : "已完成完整检查" }}</span>
        </p>
        <ul v-if="result.audit_result.issues?.length" class="issue-list">
          <li v-for="(issue, index) in result.audit_result.issues.slice(0, 12)" :key="index">
            <span :class="`issue-level issue-level--${issueLevel(issue)}`">{{ issueLevelText(issue) }}</span>
            <p>{{ issueText(issue) }}<small v-if="typeof issue !== 'string' && issue.position">{{ issue.position }}</small></p>
          </li>
        </ul>
      </article>

      <details v-if="result.revisions?.length" class="evidence-card">
        <summary><span>自动修改记录</span><small>{{ result.revisions.length }} 次</small></summary>
        <div class="revision-list">
          <details v-for="(revision, index) in result.revisions.slice(0, 3)" :key="index" :open="index === 0">
            <summary>第 {{ revision.iteration || index + 1 }} 轮 · {{ Number(revision.before_score || 0).toFixed(2) }} → {{ revision.after_score == null ? "—" : Number(revision.after_score).toFixed(2) }}</summary>
            <p v-if="revision.issues?.length">{{ revision.issues.join(" · ") }}</p>
            <pre>{{ revision.unified_diff || "本轮没有可显示的行级变化" }}</pre>
          </details>
        </div>
      </details>

      <details class="evidence-card review-card">
        <summary><span>给这次结果评分</span><small>1—5分</small></summary>
        <form @submit.prevent="submitReview">
          <div class="rating-grid">
            <label v-for="([key, label]) in scoreFields" :key="key">
              <span>{{ label }}</span>
              <select v-model="review[key]" required :disabled="reviewSubmitted">
                <option :value="null">请选择</option>
                <option v-for="score in [1, 2, 3, 4, 5]" :key="score" :value="score">{{ score }} 分</option>
              </select>
            </label>
          </div>
          <label class="notes-field">
            <span>补充说明（可选）</span>
            <textarea v-model="review.notes" maxlength="2000" :disabled="reviewSubmitted" placeholder="写下你对本次结果的具体感受"></textarea>
          </label>
          <button class="button button--primary" type="submit" :disabled="reviewSubmitted">
            {{ reviewSubmitted ? "评分已保存" : "保存评分" }}
          </button>
        </form>
      </details>
    </template>
  </section>
</template>
