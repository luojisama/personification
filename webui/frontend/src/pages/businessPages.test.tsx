import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { resources } from "../api/resources";
import { BotProvider } from "../app/BotContext";
import { AppShell } from "../components/AppShell";
import { GroupSwitchesPage } from "./GroupSwitchesPage";
import { PluginManagementPage } from "./CapabilityBusinessPages";
import { ConfigCenterPage } from "./ConfigCenterPage";
import { ModelTestsPage } from "./ModelTestsPage";

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

function botResponse() {
  return {
    items: [{ bot_id: "2534316454", nickname: "Shiro", avatar_url: null, online: true, is_default: true, last_seen_at: null }],
    total: 1,
    diagnostic_code: "bot_identity_snapshot",
  };
}

describe("新版业务页面", () => {
  it("左侧只显示四个一级分类，二级和三级导航位于工作区顶部", async () => {
    vi.spyOn(resources, "bots").mockResolvedValue(botResponse());
    render(<QueryClientProvider client={client()}><MemoryRouter initialEntries={["/runtime/proactive/recent"]}><AppShell /></MemoryRouter></QueryClientProvider>);
    const primary = screen.getByRole("navigation", { name: "一级分类" });
    expect(within(primary).getAllByRole("button").map((button) => button.textContent?.trim())).toEqual(["运行", "拟人与记忆", "能力", "运维"]);
    expect(screen.getByRole("navigation", { name: "运行二级导航" })).toHaveTextContent("主动诊断");
    expect(screen.getByRole("navigation", { name: "主动诊断三级导航" })).toHaveTextContent("概览最近记录下一可用窗口");
    fireEvent.change(screen.getByRole("textbox", { name: "搜索页面或功能" }), { target: { value: "插件更新" } });
    const updateResult = screen.getByRole("option", { name: /更新与测速/ });
    expect(updateResult).toBeInTheDocument();
    fireEvent.click(updateResult);
    expect(screen.getByRole("navigation", { name: "插件管理三级导航" })).toHaveTextContent("更新与测速");
    fireEvent.click(screen.getByTitle("收起一级导航"));
    expect(window.localStorage.getItem("personification.nav.collapsed")).toBe("1");
    expect(await screen.findByText("Shiro")).toBeInTheDocument();
  });

  it("群开关展示头像语义字段并在明确确认后调用写接口", async () => {
    vi.spyOn(resources, "bots").mockResolvedValue(botResponse());
    vi.spyOn(resources, "groupSwitches").mockResolvedValue({
      items: [{ group_id: "1011870582", group_name: "斯密马赛", avatar_url: "https://p.qlogo.cn/gh/1011870582/1011870582/100", enabled: false, membership_state: "confirmed", bot_ids: ["2534316454"], sources: ["onebot"], bot_self_ids: ["2534316454"], member_count: 42, last_active_at: 1, freshness: 1, cache_only: true, source: "group_config", static_config_readonly: false }],
      page: 1, page_size: 20, total: 1, total_pages: 1, enabled_total: 0, disabled_total: 1, diagnostic_code: "group_switch_page_ready",
    });
    const update = vi.spyOn(resources, "updateGroupSwitch").mockResolvedValue({ ok: true, code: "group_switch_enabled", phase: "operation_complete", title: "群功能已启用", message: "已确认", retryable: false, partial: false, outcome_unknown: false, warnings: [], steps: [] });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<QueryClientProvider client={client()}><MemoryRouter initialEntries={["/persona/group-switches/list"]}><BotProvider><GroupSwitchesPage /></BotProvider></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText("斯密马赛")).toBeInTheDocument();
    expect(screen.getByText("1011870582")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "启用" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith("1011870582", true));
  });

  it("插件更新页展示镜像与官方排名，UPDATE 精确确认后才允许应用", async () => {
    vi.spyOn(resources, "pluginUpdateStatus").mockResolvedValue({ ok: true, available: true, update_supported: true, source_type: "git", dirty: false, dirty_count: 0, update_available: true, ahead: 0, behind: 1, source: { remote_name: "origin", remote_url: "https://github.com/example/repo.git", branch: "main", upstream: "origin/main" }, local: { hash: "a".repeat(40), short_hash: "aaaaaaa", branch: "main" }, remote: { hash: "b".repeat(40), short_hash: "bbbbbbb", upstream: "origin/main" }, pending_history: [] });
    const operation = { operation_id: "op1", state: "ready" as const, local_commit: "a".repeat(40), remote_commit: "b".repeat(40), dirty: false, probes: [{ source_id: "mirror_1", kind: "mirror" as const, display_name: "镜像 1", base_url: "https://ghproxy.com", state: "succeeded" as const, latency_ms: 120, rank: 1, checked_at: 1, expires_at: 61, diagnostic_code: "git_source_probe_succeeded" }, { source_id: "official", kind: "official" as const, display_name: "官方源", base_url: "https://github.com/example/repo.git", state: "succeeded" as const, latency_ms: 230, rank: 2, checked_at: 1, expires_at: 61, diagnostic_code: "git_source_probe_succeeded" }], selected_source_id: "mirror_1", attempts: [], diagnostic_code: "git_source_benchmark_ready", started_at: 1, finished_at: 2 };
    vi.spyOn(resources, "pluginUpdateBenchmark").mockResolvedValue({ operation, status: await resources.pluginUpdateStatus() });
    const apply = vi.spyOn(resources, "pluginUpdateApply").mockResolvedValue({ ok: true, updated: true, operation: { ...operation, state: "succeeded" }, status: await resources.pluginUpdateStatus(), message: "已更新" });
    render(<QueryClientProvider client={client()}><MemoryRouter initialEntries={["/capability/plugins/update"]}><Routes><Route path="/capability/plugins/:section" element={<PluginManagementPage />} /></Routes></MemoryRouter></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "重新测速" }));
    expect((await screen.findAllByText("镜像 1")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("官方源")).toBeInTheDocument();
    expect(screen.getByText(/本次选中源/)).toBeInTheDocument();
    expect(screen.getByText("更新源测速完成")).toBeInTheDocument();
    expect(screen.queryByText("API 请求失败")).not.toBeInTheDocument();
    const applyButton = screen.getByRole("button", { name: "执行更新" });
    expect(applyButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText("插件更新确认"), { target: { value: "UPDATE" } });
    expect(applyButton).toBeEnabled();
    fireEvent.click(applyButton);
    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
  });

  it("配置中心把模型 Provider 池渲染为逐字段路由编辑器，不暴露嵌套秘密", async () => {
    vi.spyOn(resources, "configMetadata").mockResolvedValue({ revision: "rev1", groups: ["模型与 API"], group_counts: { "模型与 API": 1 }, modified_counts: {}, total: 1, diagnostic_code: "config_metadata_ready" });
    vi.spyOn(resources, "config").mockResolvedValue({
      items: [{
        key: "api_pools", field_name: "personification_api_pools", display_name: "API Provider 池", description: "主回复模型路由池", group: "模型与 API", category: "config", scope: "global", kind: "providers", value_type: "list",
        value: [{ name: "主路由", provider: "gemini", api_url: "https://example.invalid/v1", api_key: "***", model: "gemini-test", enabled: true, _secret_ref: "opaque-ref" }], default: null,
        secret: false, advanced: false, hot_reloadable: true, restart_required: false, required: false, modified: true, aliases: ["provider池"], choices: [], min_value: null, max_value: null,
      }],
      page: 1, page_size: 20, total: 1, total_pages: 1, revision: "rev1", groups: ["模型与 API"], group_counts: { "模型与 API": 1 }, modified_counts: {},
    });
    render(<QueryClientProvider client={client()}><MemoryRouter initialEntries={["/operations/config/models"]}><ConfigCenterPage /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText("模型路由列表")).toBeInTheDocument();
    expect(screen.getByDisplayValue("gemini-test")).toBeInTheDocument();
    expect(screen.getByDisplayValue("***")).toHaveAttribute("type", "password");
    expect(screen.queryByDisplayValue("opaque-ref")).not.toBeInTheDocument();
    expect(screen.queryByText(/\{\s*"name"/)).not.toBeInTheDocument();
  });

  it("视频探针结果使用业务表格和诊断组件，不输出整段 JSON", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(resources, "videoRouteProbe").mockResolvedValue({
      ok: true,
      code: "health_category_rechecked",
      phase: "health_recheck",
      title: "视频路由探针完成",
      message: "探针完成。",
      overall: "error",
      summary: { ok: 3, warn: 1, error: 1 },
      categories: [{ name: "视频理解", checks: [{ key: "video_probe", label: "视频理解真实探测", status: "error", detail: "没有可用的视频媒体 Provider", hint: "配置视频路由后重试。" }] }],
      warnings: [], steps: [], retryable: false, partial: true, outcome_unknown: false,
    });
    render(<QueryClientProvider client={client()}><MemoryRouter><ModelTestsPage /></MemoryRouter></QueryClientProvider>);
    const file = new File(["video"], "sample.mp4", { type: "video/mp4" });
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "确认并运行路由探针" }));
    expect(await screen.findByText("视频理解真实探测")).toBeInTheDocument();
    expect(screen.getByText(/没有可用的视频媒体 Provider/)).toBeInTheDocument();
    expect(document.querySelector("pre.safe-json")).toBeNull();
  });
});
