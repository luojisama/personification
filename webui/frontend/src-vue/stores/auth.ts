import { defineStore } from "pinia";
import { getCurrentScope, onScopeDispose, ref } from "vue";

import { ApiError, authRequest, onAuthenticationInvalid } from "@/api/client";

export interface AdminIdentity { qq: string; device_id: string; label: string; identity_source: "SUPERUSER" | "plugin_admin" }
export interface EligibleAdmin { qq: string; source: string }
export type AuthPhase = "checking" | "anonymous" | "verifying" | "pending" | "authenticated" | "unavailable";

function detailText(error: unknown): string {
  if (!(error instanceof ApiError)) return "暂时无法连接管理台服务，请检查网络后重试。";
  const payload = error.detail as { detail?: unknown } | undefined;
  return typeof payload?.detail === "string" ? payload.detail : "请求未完成，请稍后重试。";
}

export const useAuthStore = defineStore("auth", () => {
  const phase = ref<AuthPhase>("checking");
  const identity = ref<AdminIdentity | null>(null);
  const admins = ref<EligibleAdmin[]>([]);
  const message = ref("");
  const logoutUnconfirmed = ref(false);
  let generation = 0;
  let pollTimer: number | null = null;

  function cancelPending(): void {
    generation += 1;
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
  }

  function invalidate(): void {
    cancelPending();
    identity.value = null;
    phase.value = "anonymous";
    message.value = "登录状态已失效，请重新验证。";
  }

  const removeAuthenticationInvalidListener = onAuthenticationInvalid(() => {
    if (phase.value === "authenticated") invalidate();
  });
  if (getCurrentScope()) onScopeDispose(() => {
    cancelPending();
    removeAuthenticationInvalidListener();
  });

  async function loadAdmins(signal?: AbortSignal): Promise<void> {
    const response = await authRequest<{ admins: EligibleAdmin[] }>("/eligible-admins", { signal });
    admins.value = Array.isArray(response.admins) ? response.admins : [];
  }

  async function bootstrap(): Promise<void> {
    const current = ++generation;
    phase.value = "checking";
    message.value = "";
    try {
      const me = await authRequest<AdminIdentity>("/me");
      if (current !== generation) return;
      identity.value = me;
      phase.value = "authenticated";
    } catch (error) {
      if (current !== generation) return;
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        identity.value = null;
        phase.value = error.status === 403 && detailText(error).includes("DEVICE_PENDING") ? "pending" : "anonymous";
        try { await loadAdmins(); } catch { /* 登录页仍可显示明确的刷新操作 */ }
        if (current !== generation) return;
        if (phase.value === "pending") pollPending();
      } else {
        phase.value = "unavailable";
        message.value = detailText(error);
      }
    }
  }

  async function sendCode(qq: string): Promise<void> {
    const current = ++generation;
    message.value = "";
    const response = await authRequest<{ message?: string }>("/login", { method: "POST", body: { qq } });
    if (current !== generation) return;
    phase.value = "verifying";
    message.value = response.message ?? "验证码已发送。";
  }

  async function verify(qq: string, code: string, deviceLabel: string): Promise<void> {
    const current = ++generation;
    message.value = "";
    const response = await authRequest<{ success: boolean; pending?: boolean; message?: string }>("/verify", {
      method: "POST", body: { qq, code, device_label: deviceLabel },
    });
    if (current !== generation) return;
    if (response.success !== true) {
      phase.value = "verifying";
      message.value = response.message ?? "服务器未确认登录成功，请重试。";
      return;
    }
    message.value = response.message ?? "验证完成。";
    if (response.pending) {
      phase.value = "pending";
      pollPending();
    } else {
      await bootstrap();
    }
  }

  function pollPending(): void {
    cancelPending();
    const current = generation;
    phase.value = "pending";
    const poll = async () => {
      if (current !== generation || phase.value !== "pending") return;
      try {
        const me = await authRequest<AdminIdentity>("/me");
        if (current !== generation) return;
        identity.value = me;
        phase.value = "authenticated";
      } catch (error) {
        if (current !== generation) return;
        if (error instanceof ApiError && error.status === 403 && detailText(error).includes("DEVICE_PENDING")) {
          pollTimer = window.setTimeout(poll, 3_000);
        } else if (error instanceof ApiError && error.status === 401) {
          phase.value = "anonymous";
          message.value = "设备审批已取消或登录已过期。";
        } else {
          message.value = "暂时无法查询审批状态，正在等待手动重试。";
        }
      }
    };
    void poll();
  }

  async function logout(): Promise<void> {
    cancelPending();
    const current = generation;
    identity.value = null;
    phase.value = "anonymous";
    logoutUnconfirmed.value = false;
    message.value = "正在退出并清理当前页面数据…";
    try {
      await authRequest("/logout", { method: "POST" });
      if (current !== generation) return;
      message.value = "已退出管理台。";
    } catch {
      if (current !== generation) return;
      logoutUnconfirmed.value = true;
      message.value = "当前页面已锁定，但服务器未确认注销。请重试注销或关闭浏览器。";
    }
    if (current !== generation) return;
    try { await loadAdmins(); } catch { admins.value = []; }
  }

  async function returnToLogin(): Promise<void> {
    cancelPending();
    identity.value = null;
    phase.value = "anonymous";
    message.value = "已在当前页面停止等待审批；服务器上的待审批记录可能仍然存在。";
    try { await loadAdmins(); } catch { admins.value = []; }
  }

  return { phase, identity, admins, message, logoutUnconfirmed, bootstrap, loadAdmins, sendCode, verify, pollPending, logout, returnToLogin, invalidate, cancelPending };
});
