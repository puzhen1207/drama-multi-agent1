<script setup lang="ts">
import BaseModal from "./BaseModal.vue";
import type { MemoryItem } from "../types";

defineProps<{ open: boolean; loading: boolean; memories: MemoryItem[] }>();
const emit = defineEmits<{ close: []; edit: [item: MemoryItem]; delete: [item: MemoryItem] }>();

function dateText(timestamp?: number) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString("zh-CN") : "时间未知";
}
</script>

<template>
  <BaseModal :open="open" title="我的素材" wide @close="emit('close')">
    <p class="modal-intro">这里保存你确认过的历史内容，之后创作相似内容时可作为参考。</p>
    <div v-if="loading" class="modal-empty">正在读取素材……</div>
    <div v-else-if="!memories.length" class="modal-empty">暂时没有素材。生成满意内容后，可以在结果区保存。</div>
    <div v-else class="memory-list">
      <article v-for="item in memories" :key="item.memory_id" class="memory-entry">
        <header>
          <div><h3>{{ item.title || "未命名素材" }}</h3></div>
          <time>{{ dateText(item.updated_ts || item.created_ts) }}</time>
        </header>
        <dl>
          <div><dt>提问</dt><dd>{{ item.question }}</dd></div>
          <div><dt>回答</dt><dd>{{ item.answer || item.answer_preview }}</dd></div>
        </dl>
        <footer>
          <button class="text-button" type="button" @click="emit('edit', item)">编辑</button>
          <button class="text-button text-button--danger" type="button" @click="emit('delete', item)">删除</button>
        </footer>
      </article>
    </div>
  </BaseModal>
</template>
