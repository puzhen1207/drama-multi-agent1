<script setup lang="ts">
import { reactive, watch } from "vue";
import BaseModal from "./BaseModal.vue";
import type { MemoryItem } from "../types";

const props = defineProps<{ open: boolean; item: MemoryItem | null }>();
const emit = defineEmits<{ close: []; save: [item: MemoryItem] }>();
const draft = reactive<MemoryItem>({ memory_id: "", title: "", question: "", answer: "" });

watch(() => props.item, (item) => {
  if (item) Object.assign(draft, { ...item, answer: item.answer || item.answer_preview || "" });
}, { immediate: true });
</script>

<template>
  <BaseModal :open="open" title="编辑素材" wide @close="emit('close')">
    <p class="modal-intro">修改后，系统会使用最新内容作为后续创作参考。</p>
    <form id="memory-edit-form" class="form-stack" @submit.prevent="emit('save', { ...draft })">
      <label><span>标题</span><input v-model.trim="draft.title" maxlength="200" placeholder="简短标题" /></label>
      <label><span>历史提问</span><textarea v-model.trim="draft.question" rows="4" required minlength="2"></textarea></label>
      <label><span>历史回答</span><textarea v-model.trim="draft.answer" rows="12" required minlength="20"></textarea></label>
    </form>
    <template #footer>
      <button class="button button--quiet" type="button" @click="emit('close')">取消</button>
      <button class="button button--primary" type="submit" form="memory-edit-form">保存修改</button>
    </template>
  </BaseModal>
</template>
