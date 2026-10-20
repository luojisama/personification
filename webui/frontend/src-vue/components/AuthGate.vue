<template>
  <main class="auth-stage">
    <div class="auth-topbar"><a href="/personification/frontend/" class="auth-wordmark"><span>P/F</span> PERSONIFICATION</a><details class="auth-appearance"><summary>外观设置</summary><ThemeSwitcher /></details></div>
    <div class="auth-layout">
    <aside class="auth-intro" aria-label="管理台介绍">
      <span class="auth-tag">你的 Bot 工作空间</span>
      <h2>让每一次对话，<br />都有迹可循。</h2>
      <p>从人格与记忆，到模型与运行状态。<br />在一个安静、有序的空间里管理你的机器人。</p>
      <div class="auth-illustration" aria-hidden="true"><div class="orbit-line"></div><div class="orbit-line second"></div><span class="orbit-dot"></span><div class="orbit-center">P/F<small>PERSONIFICATION</small></div><span class="orbit-caption">对话 · 记忆 · 连接</span></div>
      <div class="auth-intro-footer"><span>01 / 连接你的账号</span><span>02 / 进入工作空间</span></div>
    </aside>
    <section class="auth-card" aria-labelledby="auth-title">
      <p class="eyebrow">欢迎回来</p>
      <h1 id="auth-title">{{ title }}</h1>
      <p class="auth-lead">{{ lead }}</p>

      <div v-if="auth.phase === 'checking'" role="status">正在确认当前设备的登录状态…</div>

      <form v-else-if="auth.phase === 'anonymous'" class="auth-form" @submit.prevent="requestCode">
        <SelectField v-model="selectedIdentity" label="管理员账号" :options="adminOptions" placeholder="请选择接收验证码的管理员" :disabled="busy" required />
        <SelectField v-model="delivery" label="验证码接收方式" :options="[{value:'direct',label:'原平台私聊（QQ / Telegram）'},{value:'official',label:'官方 QQ 私聊领取（需已绑定）'}]" :disabled="busy" />
        <p v-if="!auth.admins.length" class="auth-note">暂未读取到可登录管理员。请确认 Bot 配置后刷新。</p>
        <p v-if="selectedIdentity" class="auth-note">{{ deliveryNote }}</p>
        <button type="submit" :disabled="busy || !selectedIdentity">{{ busy ? "正在处理…" : delivery === "official" ? "获取私聊登录指令" : "发送私聊验证码" }}<span aria-hidden="true"> ↗</span></button>
        <button class="secondary" type="button" :disabled="busy" @click="refreshAdmins">刷新管理员列表</button>
      </form>

      <form v-else-if="auth.phase === 'verifying'" class="auth-form" @submit.prevent="confirmCode">
        <p v-if="selectedIdentity" class="auth-note">登录账号：{{ selectedAccountLabel }}。{{ deliveryNote }}</p>
        <TextField v-model="code" label="六位验证码" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required />
        <TextField v-model="deviceLabel" label="设备名称" autocomplete="off" maxlength="64" placeholder="例如：我的电脑" />
        <label class="trust-device-option"><input v-model="trustDevice" type="checkbox" :disabled="!trustAvailable" />信任此设备</label>
        <p class="auth-note">{{ trustAvailable ? "个人设备可保持勾选，以后自动登录；共享设备请取消勾选。信任仅保存在当前浏览器。" : "信任设备需通过 HTTPS 或本机地址访问；当前仍可使用验证码登录。" }}</p>
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
    </div>
    <footer class="auth-bottom">PERSONIFICATION <span>属于你的对话与记忆</span></footer>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ApiError } from "@/api/client";
import { adminAccountLabel, adminPlatform } from "@/lib/labels";
import ThemeSwitcher from "@vue-app/components/ThemeSwitcher.vue";
import SelectField from "@vue-app/components/forms/SelectField.vue";
import TextField from "@vue-app/components/forms/TextField.vue";
import { useAuthStore } from "@vue-app/stores/auth";

const auth = useAuthStore();
const selectedIdentity = ref("");
const delivery = ref('direct');
const code = ref("");
const deviceLabel = ref("");
const trustAvailable = window.location.protocol === "https:" || ["localhost", "127.0.0.1", "::1", "[::1]"].includes(window.location.hostname);
const trustDevice = ref(trustAvailable);
const busy = ref(false);
const errorMessage = ref("");
const title = computed(() => auth.phase === "pending" ? "等待设备审批" : auth.phase === "unavailable" ? "暂时无法连接" : "管理员登录");
const lead = computed(() => auth.phase === "pending" ? "当前浏览器尚未获得管理权限。" : "选择管理员账号，使用 Bot 私聊验证码登录。");
const selectedAdmin = computed(() => auth.admins.find(admin => admin.qq === selectedIdentity.value));
const selectedAccountLabel = computed(() => selectedAdmin.value?.label || adminAccountLabel(selectedIdentity.value, selectedAdmin.value?.platform));
const selectedPlatform = computed(() => adminPlatform(selectedIdentity.value, selectedAdmin.value?.platform));
const deliveryNote = computed(() => {
  if (delivery.value === 'official') return '请发送页面提供的 /登录 命令到官方 Bot 私聊；只向已绑定的管理员发放验证码。';
  if (auth.phase === "verifying") return `请在 ${selectedPlatform.value === "telegram" ? "Telegram" : "QQ"} 与 Bot 的私聊中查看验证码。`;
  return selectedPlatform.value === "telegram"
    ? "验证码通过 Telegram 私聊发送；首次使用请先在 Telegram 私聊 Bot 发送 /start。"
    : "验证码通过 QQ 私聊发送，请确认 QQ Bot 在线且可向你发送消息。";
});
const adminOptions = computed(() => auth.admins.map((admin) => ({ value: admin.qq, label: `${admin.label || adminAccountLabel(admin.qq, admin.platform)} · ${sourceLabel(admin.source)}` })));

onMounted(async () => {
  if (auth.phase === "checking" && !auth.bootstrapInProgress) await auth.bootstrap();
});

function sourceLabel(source: string): string { return source.startsWith("SUPERUSERS") ? "超级用户" : "插件管理员"; }
function explain(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = (error.detail as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string" && /[\u4e00-\u9fff]/.test(detail)) return detail;
  }
  return "操作未完成，请稍后重试。";
}
async function requestCode(): Promise<void> { busy.value = true; errorMessage.value = ""; try { await auth.sendCode(selectedIdentity.value, delivery.value); } catch (error) { errorMessage.value = explain(error); } finally { busy.value = false; } }
async function confirmCode(): Promise<void> { busy.value = true; errorMessage.value = ""; try { await auth.verify(selectedIdentity.value, code.value, deviceLabel.value, trustDevice.value); } catch (error) { errorMessage.value = explain(error); } finally { busy.value = false; } }
async function retry(): Promise<void> { busy.value = true; errorMessage.value = ""; try { await auth.bootstrap(); } finally { busy.value = false; } }
async function refreshAdmins(): Promise<void> { busy.value = true; errorMessage.value = ""; try { await auth.loadAdmins(); } catch (error) { errorMessage.value = explain(error); } finally { busy.value = false; } }
async function logout(): Promise<void> { busy.value = true; errorMessage.value = ""; try { await auth.logout(); } finally { busy.value = false; } }
async function returnToLogin(): Promise<void> { busy.value = true; await auth.returnToLogin(); busy.value = false; }
async function back(): Promise<void> { auth.invalidate(); code.value = ""; errorMessage.value = ""; await auth.loadAdmins().catch(() => undefined); }
</script>

<style scoped>
.auth-stage { min-height:100svh; padding:32px clamp(24px,5vw,80px); background:var(--color-canvas); color:var(--color-ink); display:flex; flex-direction:column; }
.auth-topbar,.auth-bottom { display:flex; justify-content:space-between; align-items:center; gap:20px; }
.auth-wordmark { display:flex; gap:12px; align-items:center; font-size:11px; font-weight:700; letter-spacing:2px; color:var(--color-ink); text-decoration:none; }
.auth-wordmark>span { font-size:20px; letter-spacing:-1px; padding:8px; border:1px solid var(--color-line-strong); border-radius:10px; }
.auth-appearance { position:relative; font-size:13px; z-index:2; }.auth-appearance summary { cursor:pointer; padding:10px; }.auth-appearance .theme-switcher { position:absolute; right:0; top:42px; width:260px; padding:16px; background:var(--color-surface); border:1px solid var(--color-line); border-radius:14px; box-shadow:var(--shadow-lg); }
.auth-layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(340px,440px); gap:clamp(48px,8vw,140px); align-items:center; width:min(100%,1060px); margin:auto; padding:56px 0; }
.auth-intro { min-width:0; }.auth-tag,.eyebrow { color:var(--color-signal); font-size:12px; font-weight:650; letter-spacing:2px; }.auth-intro h2 { margin:22px 0; font-size:clamp(30px,3.4vw,48px); line-height:1.4; letter-spacing:-1.5px; font-weight:600; }.auth-intro>p { font-size:14px; color:var(--color-ink-muted); line-height:1.9; }
.auth-illustration { position:relative; height:230px; width:300px; margin:26px 0; overflow:hidden; }.orbit-line { position:absolute; inset:10px 35px; border:1px solid var(--color-line-strong); border-radius:50%; transform:rotate(-25deg) scaleX(1.35); }.orbit-line.second { inset:25px 50px; transform:rotate(30deg) scaleX(.85); border-color:var(--color-signal); opacity:.3; }.orbit-center { position:absolute; left:88px; top:70px; font-size:48px; letter-spacing:-4px; font-weight:600; }.orbit-center small { display:block; letter-spacing:3px; font-size:8px; }.orbit-dot { position:absolute; top:40px; left:35px; width:14px; height:14px; background:var(--color-signal); border:4px solid var(--color-canvas); border-radius:50%; }.orbit-caption { position:absolute; right:0; bottom:16px; font-size:11px; color:var(--color-ink-muted); background:var(--color-canvas); padding:8px; }.auth-intro-footer { display:flex; gap:28px; font-size:11px; color:var(--color-ink-muted); }
.auth-card { min-width:0; padding:36px; background:var(--color-surface); border:1px solid var(--color-line); border-radius:20px; box-shadow:0 16px 56px #00000005; }.auth-card h1 { margin:10px 0; font-size:27px; letter-spacing:-.6px; }.auth-lead,.auth-note,.auth-footnote { color:var(--color-ink-muted); font-size:12px; line-height:1.75; }.auth-form { display:grid; gap:18px; margin-top:28px; }.auth-form p { margin:0; }.auth-footnote { border-top:1px solid var(--color-line); padding-top:20px; margin:24px 0 0; font-size:11px; }
button { min-height:44px; border-radius:9px; border:1px solid transparent; padding:0 14px; color:var(--color-signal-ink); background:var(--color-signal); font:inherit; font-size:13px; font-weight:600; cursor:pointer; }button.secondary { min-height:32px; color:var(--color-ink-muted); background:transparent; font-size:12px; }button:disabled { opacity:.5; cursor:not-allowed; }button:focus-visible { outline:3px solid var(--color-focus); outline-offset:3px; }.auth-message,.auth-error { font-size:12px; padding:12px 14px; border-radius:8px; overflow-wrap:anywhere; line-height:1.7; }.auth-message { background:var(--color-signal-soft); color:var(--color-ink); }.auth-error { background:var(--color-danger-soft); color:var(--color-danger); }.trust-device-option { display:flex; align-items:center; gap:8px; font-size:13px; }.trust-device-option input { width:16px; height:16px; }.auth-bottom { font-size:10px; letter-spacing:2px; color:var(--color-ink-muted); }.auth-bottom span { letter-spacing:0; }
:root[data-theme="prts"] .auth-card { border-radius:0; border-top:3px solid var(--color-signal); }:root[data-theme="prts"] .orbit-line { border-radius:0; }:root[data-theme="schale"] .auth-card { box-shadow:10px 10px 0 var(--color-signal-soft); }
@media(max-width:800px) { .auth-layout { grid-template-columns:1fr; max-width:440px; gap:24px; padding:32px 0; }.auth-intro { display:none; }.auth-stage { padding:20px; }.auth-card { padding:28px; }.auth-wordmark { font-size:9px; letter-spacing:1px; } }
</style>
