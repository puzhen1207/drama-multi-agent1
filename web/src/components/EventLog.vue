<script setup lang="ts">
import type { TimelineEvent } from "../types";
defineProps<{ events: TimelineEvent[]; running: boolean }>();
</script>

<template>
  <section class="panel event-panel">
    <header class="panel-head panel-head--compact">
      <div class="panel-title">
        <span class="step-number" aria-hidden="true">3</span>
        <div>
          <h2>查看生成动态</h2>
          <p>按时间了解每一步的处理情况</p>
        </div>
      </div>
      <span class="event-count">{{ events.length }} 条</span>
    </header>
    <div class="event-feed" role="log" aria-live="polite">
      <div v-if="!events.length" class="event-empty">
        <span class="signal-icon">⌁</span>
        <p>开始生成后，这里会显示每一步的进展。</p>
      </div>
      <article v-for="event in events" :key="event.id" class="event-row" :class="`event-row--${event.type}`">
        <span class="event-signal"></span>
        <div><b>{{ event.label }}</b><p>{{ event.detail }}</p></div>
        <time>{{ event.time }}</time>
      </article>
    </div>
  </section>
</template>
