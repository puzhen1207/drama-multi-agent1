<script setup lang="ts">
import BaseModal from "./BaseModal.vue";

defineProps<{ open: boolean; userId: string }>();
const emit = defineEmits<{
  close: [];
  logout: [];
  export: [];
  import: [file: File];
}>();

function onFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (file) emit("import", file);
}
</script>

<template>
  <BaseModal :open="open" title="账号与素材" @close="emit('close')">
    <p class="modal-intro">当前账号的素材、会话与创作偏好保存在服务端，换浏览器后登录同一账号即可继续使用。</p>
    <div class="account-card">
      <span class="account-avatar" aria-hidden="true">{{ userId.slice(0, 1).toUpperCase() }}</span>
      <div><small>当前账号</small><b>{{ userId }}</b></div>
    </div>
    <template #footer>
      <button class="button button--quiet" type="button" @click="emit('export')">导出素材</button>
      <label class="button button--quiet file-button">导入素材<input type="file" accept="application/json,.json" @change="onFile" /></label>
      <button class="button button--danger account-logout" type="button" @click="emit('logout')">退出登录</button>
    </template>
  </BaseModal>
</template>
