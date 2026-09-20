<script setup lang="ts">
import { computed, ref, watch } from "vue";

const props = defineProps<{ busy: boolean; error: string; suggestedUsername?: string }>();
const emit = defineEmits<{
  authenticate: [mode: "login" | "register", username: string, password: string];
}>();

const mode = ref<"login" | "register">("login");
const username = ref(props.suggestedUsername || "");
const password = ref("");
const confirmPassword = ref("");
const localError = ref("");

const action = computed(() => mode.value === "login" ? "登录" : "注册");

watch(mode, () => {
  localError.value = "";
  confirmPassword.value = "";
});

function submit() {
  localError.value = "";
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(username.value)) {
    localError.value = "账号只能使用字母、数字、下划线或连字符";
    return;
  }
  if (password.value.length < 8) {
    localError.value = "密码至少需要 8 个字符";
    return;
  }
  if (mode.value === "register" && password.value !== confirmPassword.value) {
    localError.value = "两次输入的密码不一致";
    return;
  }
  emit("authenticate", mode.value, username.value.trim(), password.value);
}
</script>

<template>
  <main class="auth-stage">
    <section class="auth-card" aria-label="账号登录与注册">
      <div class="auth-brand">
        <div class="clapper-mark auth-clapper" aria-hidden="true"><span></span><span></span><span></span></div>
        <h1>短剧创作台</h1>
      </div>
      <div class="auth-tabs" role="tablist" aria-label="账号入口">
        <button type="button" :class="{ active: mode === 'login' }" @click="mode = 'login'">登录</button>
        <button type="button" :class="{ active: mode === 'register' }" @click="mode = 'register'">注册</button>
      </div>
      <form class="auth-form" @submit.prevent="submit">
        <label>
          <span>账号</span>
          <input v-model.trim="username" name="username" autocomplete="username" maxlength="128" placeholder="请输入账号" autofocus />
        </label>
        <label>
          <span>密码</span>
          <input v-model="password" name="password" type="password" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" maxlength="128" placeholder="请输入密码" />
        </label>
        <label v-if="mode === 'register'">
          <span>确认密码</span>
          <input v-model="confirmPassword" name="confirm-password" type="password" autocomplete="new-password" maxlength="128" placeholder="再次输入密码" />
        </label>
        <p v-if="localError || props.error" class="auth-error" role="alert">{{ localError || props.error }}</p>
        <button class="button button--primary auth-submit" type="submit" :disabled="busy">
          {{ busy ? "正在验证…" : action }}
        </button>
      </form>
    </section>
  </main>
</template>
