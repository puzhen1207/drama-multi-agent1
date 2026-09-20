<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";

defineProps<{ open: boolean; title: string; eyebrow?: string; wide?: boolean }>();
const emit = defineEmits<{ close: [] }>();

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") emit("close");
}

onMounted(() => document.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => document.removeEventListener("keydown", onKeydown));
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="open" class="modal-backdrop" role="presentation" @mousedown.self="emit('close')">
        <section
          class="modal-sheet"
          :class="{ 'modal-sheet--wide': wide }"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
        >
          <header class="modal-head">
            <div>
              <span v-if="eyebrow" class="eyebrow">{{ eyebrow }}</span>
              <h2>{{ title }}</h2>
            </div>
            <button class="icon-button" type="button" aria-label="关闭" @click="emit('close')">×</button>
          </header>
          <div class="modal-body"><slot /></div>
          <footer v-if="$slots.footer" class="modal-foot"><slot name="footer" /></footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
