<template>
  <main class="auth-stage">
    <section class="auth-card" aria-labelledby="auth-title">
      <p class="eyebrow">P/F 管理台</p>
      <h1 id="auth-title">{{ title }}</h1>
      <p class="auth-lead">{{ lead }}</p>

      <div v-if="auth.phase === 'checking'" role="status">正在确认当前设备的登录状态…</div>

      <form v-else-if="auth.phase === 'anonymous'" class="auth-form" @submit.prevent="requestCode">
        <SelectField v-model="qq" label="管理员账号" :options="adminOptions" placeholder="请选择接收验证码的管理员" required />
        <p v-if="!auth.admins.length" class="auth-note">暂未读取到可登录管理员。请确认 Bot 配置后刷新。</p>
        <button type="submit" :disabled="busy || !qq">发送私聊验证码</button>
        <button class="secondary" type="button" :disabled="busy" @click="refreshAdmins">刷新管理员列表</button>
      </form>

      <form v-else-if="auth.phase === 'verifying'" class="auth-form" @submit.prevent="confirmCode">
        <TextField v-model="code" label="六位验证码" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required />
        <TextField v-model="deviceLabel" label="设备名称" autocomplete="off" maxlength="64" placeholder="例如：我的电脑" />
        <button type="submit" :disabled="busy || !/^[0-9]{6}$/.test(code)">验证并登录</button>
        <button class="secondary" type="button" :disabled="busy" @click="back">重新选择管理员</button>
      </form>

      <div v-else-if="auth.phase === 'pending'" class="auth-form" role="status">
        <p>这台设备正在等待已登录管理员批准。批准后页面会自动进入管理台。</p>
        <button type="button" :disabled="busy" @click="auth.pollPending()">重新查询审批状态</button>
        <button class="secondary" type="button" :disabled="busy" @click="returnToLogin">停止等待并返回登录</button>
      </div>

      <div v-else class="auth-form" role="alert">
        <p>管理台服务当前不可用。网络故障不会被当作已登录状态。</p>
        <button type="button" :disabled="busy" @click="retry">重试连接</button>
      </div>

      <p v-if="auth.message" class="auth-message" role="status">{{ auth.message }}</p>
      <button v-if="auth.phase === 'anonymous' && auth.logoutUnconfirmed" class="logout-retry" type="button" :disabled="busy" @click="logout">重试注销</button>
      <p v-if="errorMessage" class="auth-error" role="alert">{{ errorMessage }}</p>
      <p class="auth-footnote">验证码只会由 Bot 私聊发送；本页面不会要求或保存管理 Token。</p>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ApiError } from "@/api/client";
import SelectField from "@vue-app/components/forms/SelectField.vue";
import TextField from "@vue-app/components/forms/TextField.vue";
import { useAuthStore } from "@vue-app/stores/auth";

const auth = useAuthStore();
const qq = ref("");
const code = ref("");
const deviceLabel = ref("");
const busy = ref(false);
const errorMessage = ref("");
const title = computed(() => auth.phase === "pending" ? "等待设备审批" : auth.phase === "unavailable" ? "暂时无法连接" : "管理员登录");
const lead = computed(() => auth.phase === "pending" ? "当前浏览器尚未获得管理权限。" : "选择已有管理员，由 Bot 私聊发送一次性验证码。");
const adminOptions = computed(() => auth.admins.map((admin) => ({ value: admin.qq, label: `QQ ${admin.qq} · ${sourceLabel(admin.source)}` })));

onMounted(async () => {
  if (auth.phase === "checking") await auth.bootstrap();
});

function sourceLabel(source: string): string { return source.startsWith("SUPERUSERS") ? "超级用户" : "插件管理员"; }
function explain(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = (error.detail as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string" && !/^[A-Z_]+$/.test(detail)) return detail;
  }
  return "操作未完成，请稍后重试。";
}
async function requestCode(): Promise<void> { busy.value = true; errorMessage.value = ""; try { await auth.sendCode(qq.value); } catch (error) { errorMessage.value = explain(error); } finally { busy.value = false; } }
async function confirmCode(): Promise<void> { busy.value = true; errorMessage.value = ""; try { await auth.verify(qq.value, code.value, deviceLabel.value); } catch (error) { errorMessage.value = explain(error); } finally { busy.value = false; } }
async function retry(): Promise<void> { busy.value = true; errorMessage.value = ""; try { await auth.bootstrap(); } finally { busy.value = false; } }
async function refreshAdmins(): Promise<void> { busy.value = true; errorMessage.value = ""; try { await auth.loadAdmins(); } catch (error) { errorMessage.value = explain(error); } finally { busy.value = false; } }
async function logout(): Promise<void> { busy.value = true; errorMessage.value = ""; try { await auth.logout(); } finally { busy.value = false; } }
async function returnToLogin(): Promise<void> { busy.value = true; await auth.returnToLogin(); busy.value = false; }
async function back(): Promise<void> { auth.invalidate(); code.value = ""; errorMessage.value = ""; await auth.loadAdmins().catch(() => undefined); }
</script>

<style scoped>
.auth-stage { min-height: 100vh; display: grid; place-items: center; padding: 24px; background: radial-gradient(circle at top, var(--color-surface-raised), var(--color-canvas)); }
.auth-card { width: min(100%, 480px); padding: clamp(24px, 5vw, 42px); border: 1px solid var(--color-line); border-radius: 20px; color: var(--color-ink); background: var(--color-surface); box-shadow: var(--shadow-lg); }
.eyebrow { color: var(--color-signal); letter-spacing: .14em; font-weight: 700; }
h1 { margin: 8px 0; font-size: clamp(28px, 6vw, 42px); }.auth-lead,.auth-note,.auth-footnote { color: var(--color-ink-muted); }.auth-form { display: grid; gap: 14px; margin-top: 24px; }
button { min-height: 44px; border-radius: var(--radius-md); border: 1px solid transparent; padding: 0 12px; cursor: pointer; color: var(--color-signal-ink); background: var(--color-signal); font: inherit; font-weight: 700; } button.secondary { color: var(--color-ink); background: transparent; border-color: var(--color-line-strong); } button:disabled { cursor: wait; opacity: .55; }.auth-message { color: var(--color-signal); }.auth-error { color: var(--color-danger); }.auth-footnote { margin-top: 24px; font-size: .86rem; }.logout-retry { width: 100%; margin-top: 10px; }
</style>
