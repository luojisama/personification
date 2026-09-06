<template>
  <div class="page-stack">
    <PageHeader
      index="群聊安全"
      title="受控禁言"
      description="查看提醒进度、依据与执行结果。两轮提醒确认送达且对方继续冒犯，才可能执行禁言；解除前会重新核验权限。"
    >
      <template #actions>
        <RouterLink class="button button-quiet" to="/operations/config/general">前往配置中心</RouterLink>
      </template>
    </PageHeader>

    <QueryBoundary :pending="statusQuery.isPending.value" :error="statusQuery.error.value">
      <Panel v-if="statusQuery.data.value" eyebrow="CONTROL / EFFECTIVE CONFIG" title="当前受控范围">
        <div class="moderation-overview">
          <div>
            <span class="field-label">功能开关</span>
            <StateBadge :tone="statusQuery.data.value.enabled ? 'ok' : 'unknown'">
              {{ statusQuery.data.value.enabled ? "已启用" : "未启用" }}
            </StateBadge>
          </div>
          <div>
            <span class="field-label">已授权群</span>
            <p v-if="statusQuery.data.value.authorized_groups.length" class="id-chip-list">
              <code v-for="groupId in statusQuery.data.value.authorized_groups" :key="groupId">{{ groupId }}</code>
            </p>
            <p v-else class="muted-copy">未配置授权群；不会执行禁言。</p>
          </div>
        </div>
      </Panel>
    </QueryBoundary>

    <QueryBoundary :pending="incidentsQuery.isPending.value" :error="incidentsQuery.error.value" :empty="!incidentsQuery.isPending.value && (incidentsQuery.data.value?.items.length || 0) === 0" empty-text="暂无提醒或受控禁言事件。">
      <Panel v-if="incidentsQuery.data.value?.items.length" eyebrow="INCIDENTS / CONFIRMED EVIDENCE" :title="`受控事件（本页 ${incidentsQuery.data.value.items.length} 项）`">
        <div class="trace-table-wrap">
          <table class="forensic-table moderation-table">
            <thead><tr><th>事件与对象</th><th>提醒确认</th><th>引用证据</th><th>状态与时长</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="item in incidentsQuery.data.value.items" :key="item.incident">
                <td>
                  <strong><code>{{ shortId(item.incident) }}</code></strong>
                  <small>平台 {{ item.platform }} · Bot {{ item.bot_id }}</small>
                  <small>群 <code>{{ item.group_id }}</code> · 成员 <code>{{ item.target_id }}</code></small>
                </td>
                <td>
                  <StateBadge :tone="warningTone(item)">{{ item.warning_count }} / 2 轮已确认{{ isExpired(item) ? "（已过期）" : "" }}</StateBadge>
                  <small>最后更新 {{ formatDateTime(item.updated_at) }}</small>
                  <small v-if="isExpired(item)">提醒窗口已过期，不能作为当前处罚依据</small>
                </td>
                <td class="evidence-cell">
                  <CollapsibleText :text="evidenceText(item)" :limit="100" />
                </td>
                <td>
                  <StateBadge :tone="statusTone(item.status)" :raw="item.status">{{ statusLabel(item.status) }}</StateBadge>
                  <small v-if="item.minutes > 0">时长 {{ item.minutes }} 分钟</small>
                  <small v-else>尚未形成禁言时长</small>
                </td>
                <td>
                  <button
                    v-if="canRelease(item)"
                    class="button button-danger"
                    type="button"
                    :disabled="releasingId === item.operation_id"
                    @click="confirmRelease(item)"
                  >{{ releasingId === item.operation_id ? "正在重新核验…" : "解除禁言" }}</button>
                  <span v-else class="muted-copy">{{ releaseHint(item) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>
    </QueryBoundary>

    <Pagination v-if="incidentsQuery.data.value" :page="page" :total-pages="incidentsQuery.data.value.total_pages" :total="incidentsQuery.data.value.total" :disabled="incidentsQuery.isFetching.value" @update:page="setPage" />

    <Panel v-if="feedback" eyebrow="OPERATION / FEEDBACK" title="解除结果" aria-live="polite">
      <div class="diagnostic-summary"><StateBadge :tone="feedback.ok ? 'ok' : 'error'">{{ feedback.ok ? "已解除" : "未确认解除" }}</StateBadge><span><code>{{ feedback.code }}</code> {{ feedbackMessage }}</span></div>
    </Panel>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { resources } from "@/api/resources";
import type { ControlledModerationIncident } from "@/api/types";
import { formatDateTime, shortId } from "@/lib/format";
import CollapsibleText from "@vue-app/components/CollapsibleText.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Pagination from "@vue-app/components/Pagination.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const page = computed(() => Math.max(1, Number(route.query.page ?? 1) || 1));
const releasingId = ref("");
const feedback = ref<{ ok: boolean; code: string } | null>(null);

const statusQuery = useQuery({ queryKey: ["controlled-moderation-status"], queryFn: ({ signal }) => resources.moderationStatus(signal) });
const incidentsQuery = useQuery({ queryKey: computed(() => ["controlled-moderation-incidents", page.value]), queryFn: ({ signal }) => resources.moderationIncidents(page.value, 20, signal) });
const releaseMutation = useMutation({
  mutationFn: (operationId: string) => resources.releaseModerationOperation(operationId),
  onSuccess: (result) => { feedback.value = result; void queryClient.invalidateQueries({ queryKey: ["controlled-moderation-incidents"] }); },
  onError: () => { feedback.value = { ok: false, code: "moderation_release_request_failed" }; },
  onSettled: () => { releasingId.value = ""; },
});

const feedbackMessage = computed(() => feedback.value?.ok ? "服务端已完成权限和目标状态核验后解除。" : "服务端未确认解除；请根据状态码核对 Bot 权限、目标成员状态和送达结果。" );
function setPage(target: number) { void router.replace({ query: { ...route.query, page: target > 1 ? String(target) : undefined } }); }
function evidenceText(item: ControlledModerationIncident): string { return `提醒消息引用：${item.warning_message_ids.filter(Boolean).join("、") || "无可展示引用"}\n触发消息引用：${item.evidence_message_ids.filter(Boolean).join("、") || "无可展示引用"}`; }
function isExpired(item: ControlledModerationIncident): boolean {
  if (item.expires_at === null || item.expires_at === undefined || item.expires_at === "") return false;
  const raw = typeof item.expires_at === "number" ? item.expires_at * (item.expires_at < 10_000_000_000 ? 1000 : 1) : Date.parse(item.expires_at);
  return Number.isFinite(raw) && raw <= Date.now();
}
function warningTone(item: ControlledModerationIncident): "ok" | "warn" | "unknown" { return isExpired(item) ? "unknown" : item.warning_count >= 2 ? "ok" : "warn"; }
function canRelease(item: ControlledModerationIncident): boolean { return Boolean(item.operation_id) && (item.status === "sent" || item.status === "unknown"); }
function releaseHint(item: ControlledModerationIncident): string { return item.status === "warning_only" ? "仅记录提醒，不可解除" : item.status === "running" ? "操作尚在执行" : item.status === "failed" ? "未送达禁言，无需解除" : "当前状态不支持解除"; }
function statusLabel(status: string): string { return ({ warning_only: "仅提醒", running: "执行中", sent: "已禁言", failed: "执行失败", unknown: "结果未知", released: "已解除" } as Record<string, string>)[status] || "未知状态"; }
function statusTone(status: string): "ok" | "warn" | "error" | "running" | "unknown" { if (status === "sent" || status === "released") return "ok"; if (status === "running") return "running"; if (status === "failed") return "error"; if (status === "warning_only") return "warn"; return "unknown"; }
function confirmRelease(item: ControlledModerationIncident) { if (!window.confirm("确认请求解除该操作对应的禁言？服务端会重新核验当前 Bot 权限、目标成员和禁言状态。")) return; releasingId.value = item.operation_id; releaseMutation.mutate(item.operation_id); }
watch(() => incidentsQuery.data.value?.total_pages, (total) => { if (total && page.value > total) setPage(total); });
</script>
