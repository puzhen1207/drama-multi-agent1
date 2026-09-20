<script setup lang="ts">
import type { HealthState } from "../types";

defineProps<{
  health: HealthState;
  memoryCount: number;
  userId: string;
  running: boolean;
}>();

const emit = defineEmits<{ memory: []; settings: [] }>();
</script>

<template>
  <header class="studio-header">
    <div class="brand-lockup">
      <div class="clapper-mark" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>
      <div>
        <p class="eyebrow">短剧创作工具</p>
        <h1>短剧创作台</h1>
        <p class="brand-subtitle">写下一个想法，生成内容并完成检查</p>
      </div>
    </div>

    <nav class="status-cluster" aria-label="系统状态">
      <span class="status-pill" :class="running ? 'status-pill--busy' : health.online ? 'status-pill--ok' : 'status-pill--off'">
        <i></i>{{ running ? "正在生成" : health.checking ? "正在连接" : health.online ? "服务正常" : "服务不可用" }}
      </span>
      <span class="status-pill" :class="health.llmConfigured ? 'status-pill--llm' : 'status-pill--stub'">
        <i></i>{{ health.llmConfigured ? "智能创作已就绪" : "体验模式" }}
      </span>
      <span v-if="health.embeddingDegraded" class="status-pill status-pill--warn" :title="health.embeddingHint">
        <i></i>参考资料暂不可用
      </span>
      <button class="status-action" type="button" @click="emit('memory')">
        <span>我的素材</span><b>{{ memoryCount }}</b>
      </button>
      <button class="status-action status-action--identity" type="button" @click="emit('settings')" :title="userId">
        <span>账号</span><b>{{ userId.slice(0, 8) }}</b>
      </button>
    </nav>
  </header>
</template>
