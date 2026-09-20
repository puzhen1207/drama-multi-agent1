<script setup lang="ts">
import type { RunMode } from "../types";

const props = defineProps<{
  modelValue: string;
  runMode: RunMode;
  running: boolean;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: string];
  "update:runMode": [value: RunMode];
  stream: [];
  cancel: [];
  clear: [];
}>();

const prompts = [
  { label: "追妻大纲", detail: "3集 · 爽点反转", text: "给我整理一段关于「霸总追妻」的短剧大纲，分 3 集，突出爽点和反转" },
  { label: "双版文案", detail: "都市新剧推广", text: "写 2 版不同风格的推广文案，推广都市新剧《错位人生》" },
  { label: "爆款开场", detail: "穿越 · 500字", text: "生成一个关于「高考状元穿越古代」的爆款剧本开头，500 字" },
  { label: "合规答疑", detail: "平台规则梳理", text: "短剧内容中不允许出现哪些违规内容？列出主要合规要求" },
];

function onKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && props.modelValue.trim() && !props.running) {
    emit("stream");
  }
}
</script>

<template>
  <section class="panel brief-panel">
    <header class="panel-head">
      <div class="panel-title">
        <span class="step-number" aria-hidden="true">1</span>
        <div>
        <h2>写下你的需求</h2>
          <p>说明题材、篇幅、风格和希望达成的效果</p>
        </div>
      </div>
      <span class="panel-note">{{ modelValue.length }} / 10000</span>
    </header>

    <div class="brief-body">
      <label class="brief-input">
        <span class="sr-only">短剧创作需求</span>
        <textarea
          :value="modelValue"
          maxlength="10000"
          :readonly="running"
          placeholder="例如：写一个古代侠客题材的三集短剧，节奏紧凑，结尾有反转……"
          @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
          @keydown="onKeydown"
        ></textarea>
      </label>

      <div class="brief-controls">
        <div class="prompt-section">
          <p class="micro-label">常用示例</p>
          <div class="prompt-grid">
            <button
              v-for="prompt in prompts"
              :key="prompt.label"
              type="button"
              class="prompt-card"
              :disabled="running"
              @click="emit('update:modelValue', prompt.text)"
            >
              <b>{{ prompt.label }}</b><span>{{ prompt.detail }}</span>
            </button>
          </div>
        </div>

        <div class="mode-picker" aria-label="生成方式">
          <p class="micro-label">选择生成方式</p>
          <div class="mode-options">
            <button
              type="button"
              :class="{ 'mode-option--active': runMode === 'fast' }"
              :disabled="running"
              aria-label="快速模式"
              @click="emit('update:runMode', 'fast')"
            >
              <span>快速</span>
              <small>更快返回，适合先看初稿</small>
            </button>
            <button
              type="button"
              :class="{ 'mode-option--active': runMode === 'quality' }"
              :disabled="running"
              aria-label="精细模式"
              @click="emit('update:runMode', 'quality')"
            >
              <span>精细</span>
              <small>自动检查并优化，适合定稿</small>
            </button>
          </div>
        </div>

        <div class="brief-actions">
          <button v-if="!running" class="button button--primary" type="button" :disabled="!modelValue.trim()" @click="emit('stream')">
            <span class="play-mark">▶</span> 开始生成
          </button>
          <button v-else class="button button--danger" type="button" @click="emit('cancel')">停止生成</button>
          <button class="text-button" type="button" :disabled="running || !modelValue" @click="emit('clear')">清空</button>
        </div>
      </div>
    </div>
  </section>
</template>
