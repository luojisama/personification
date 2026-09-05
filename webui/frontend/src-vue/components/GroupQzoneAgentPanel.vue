<template>
  <div class="page-stack qzone-agent-page">
    <QueryBoundary :pending="query.isPending.value" :error="query.error.value">
      <template v-if="data">
        <div class="summary-grid qzone-agent-summary">
          <Panel eyebrow="QZONE AGENT / GATES" title="空间互动门禁">
            <div class="qzone-gate-strip" aria-label="空间互动三重门禁">
              <div>
                <span>QZone 总开关</span>
                <StateBadge :tone="data.qzone_enabled ? 'ok' : 'warn'">
                  {{ data.qzone_enabled ? "已开启" : "未开启" }}
                </StateBadge>
              </div>
              <div>
                <span>Agent 全局开关</span>
                <StateBadge :tone="data.global_enabled ? 'ok' : 'warn'">
                  {{ data.global_enabled ? "已开启" : "未开启" }}
                </StateBadge>
              </div>
              <div>
                <span>本群开关</span>
                <StateBadge :tone="data.settings.enabled ? 'ok' : 'unknown'">
                  {{ data.settings.enabled ? "已开启" : "未开启" }}
                </StateBadge>
              </div>
            </div>
            <p class="muted-copy">三个开关全部开启后，Agent 才能读取当前群友空间并执行受控点赞或评论。这里不提供在线试发按钮。</p>
            <div class="form-grid qzone-agent-settings">
              <SwitchField
                id="qzone-agent-enabled"
                :model-value="draft.enabled"
                label="启用本群空间互动"
                @update:model-value="update({ enabled: $event })"
              />
              <NumberField
                id="qzone-agent-group-limit"
                :model-value="numberOrNull(draft.groupDailyLimit)"
                label="本群每日写入上限"
                :min="0"
                :max="data.limits.group_daily_limit"
                :error="fieldErrors.group"
                @update:model-value="update({ groupDailyLimit: stringifyNumber($event) })"
              />
              <NumberField
                id="qzone-agent-target-limit"
                :model-value="numberOrNull(draft.targetDailyLimit)"
                label="同一目标每日上限"
                :min="0"
                :max="data.limits.target_daily_limit"
                :error="fieldErrors.target"
                @update:model-value="update({ targetDailyLimit: stringifyNumber($event) })"
              />
              <NumberField
                id="qzone-agent-cooldown"
                :model-value="numberOrNull(draft.targetCooldownSeconds)"
                label="同一目标冷却（秒）"
                :min="data.limits.target_cooldown_seconds"
                :max="86400"
                :error="fieldErrors.cooldown"
                @update:model-value="update({ targetCooldownSeconds: stringifyNumber($event) })"
              />
            </div>
            <div class="inline-controls">
              <button
                class="button"
                type="button"
                :disabled="!dirty || Boolean(validationError) || mutation.isPending.value"
                @click="save"
              >
                保存群级空间策略
              </button>
            </div>
          </Panel>
          <Panel eyebrow="QZONE AGENT / QUOTA" title="今日额度与边界">
            <dl class="compact-kv">
              <div><dt>今日已占用</dt><dd>{{ data.quota.used_today }} / {{ data.settings.group_daily_limit }}</dd></div>
              <div><dt>同一目标每日</dt><dd>{{ data.settings.target_daily_limit }} 次</dd></div>
              <div><dt>同一目标冷却</dt><dd>{{ data.settings.target_cooldown_seconds }} 秒</dd></div>
              <div><dt>结果未知</dt><dd>占用额度，不自动重试</dd></div>
              <div><dt>可执行动作</dt><dd>仅读取、点赞、评论</dd></div>
              <div><dt>禁止动作</dt><dd>转发、代发、删除、跨群操作</dd></div>
            </dl>
          </Panel>
        </div>

        <Panel eyebrow="QZONE AGENT / OPERATIONS" :title="`脱敏近期操作（${data.recent_operations.length}）`">
          <div v-if="data.recent_operations.length" class="trace-table-wrap">
            <table class="forensic-table">
              <thead>
                <tr>
                  <th>Operation ID</th>
                  <th>动作</th>
                  <th>状态</th>
                  <th>诊断码</th>
                  <th>开始</th>
                  <th>更新</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="operation in data.recent_operations" :key="operation.operation_id">
                  <td><code>{{ operation.operation_id }}</code></td>
                  <td>{{ operation.action === "like" ? "点赞" : "评论" }}</td>
                  <td><StateBadge :tone="operationTone(operation.status)">{{ operation.status }}</StateBadge></td>
                  <td><code>{{ operation.result_code || "—" }}</code></td>
                  <td>{{ formatDateTime(operation.created_at) }}</td>
                  <td>{{ formatDateTime(operation.updated_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted-copy">暂无互动操作摘要。此处不会展示 QQ 号、动态正文、评论正文、Cookie 或原始响应。</p>
        </Panel>
      </template>
    </QueryBoundary>
    <DiagnosticPanel
      v-for="(item, index) in diagnostics"
      :key="`${item.code}:${index}`"
      :diagnostic="item"
      :default-open="index === 0"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery } from "@tanstack/vue-query";

import { diagnosticFromError, safeDiagnostic } from "@/api/diagnostics";
import { resources } from "@/api/resources";
import type { GroupQzoneAgentSettings, OperationDiagnostic } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import DiagnosticPanel from "@vue-app/components/DiagnosticPanel.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import NumberField from "@vue-app/components/forms/NumberField.vue";
import SwitchField from "@vue-app/components/forms/SwitchField.vue";

interface QzoneSettingsDraft {
  enabled: boolean;
  groupDailyLimit: string;
  targetDailyLimit: string;
  targetCooldownSeconds: string;
}

const props = defineProps<{ groupId: string }>();

const diagnostics = ref<OperationDiagnostic[]>([]);

const query = useQuery({
  queryKey: computed(() => ["group-qzone-agent", props.groupId]),
  queryFn: ({ signal }) => resources.groupQzoneAgent(props.groupId, signal),
  enabled: computed(() => Boolean(props.groupId)),
});

const data = computed(() => query.data.value);

const draft = ref<QzoneSettingsDraft>({
  enabled: false,
  groupDailyLimit: "3",
  targetDailyLimit: "1",
  targetCooldownSeconds: "1800",
});

const dirty = ref(false);

watch(data, (next) => {
  if (!next || dirty.value) return;
  draft.value = {
    enabled: next.settings.enabled,
    groupDailyLimit: String(next.settings.group_daily_limit),
    targetDailyLimit: String(next.settings.target_daily_limit),
    targetCooldownSeconds: String(next.settings.target_cooldown_seconds),
  };
}, { immediate: true });

watch(() => props.groupId, () => {
  dirty.value = false;
});

const fieldErrors = computed(() => {
  if (!data.value) return { group: "", target: "", cooldown: "" };
  const groupLimit = Number(draft.value.groupDailyLimit);
  const targetLimit = Number(draft.value.targetDailyLimit);
  const cooldown = Number(draft.value.targetCooldownSeconds);
  return {
    group: !Number.isInteger(groupLimit) || groupLimit < 0 || groupLimit > data.value.limits.group_daily_limit
      ? `本群每日上限必须是 0 到 ${data.value.limits.group_daily_limit} 的整数。`
      : "",
    target: !Number.isInteger(targetLimit) || targetLimit < 0 || targetLimit > data.value.limits.target_daily_limit
      ? `同一目标每日上限必须是 0 到 ${data.value.limits.target_daily_limit} 的整数。`
      : "",
    cooldown: !Number.isFinite(cooldown) || cooldown < data.value.limits.target_cooldown_seconds || cooldown > 86400
      ? `同一目标冷却不能低于全局下限 ${data.value.limits.target_cooldown_seconds} 秒，且不能超过 86400 秒。`
      : "",
  };
});

const validationError = computed(() => fieldErrors.value.group || fieldErrors.value.target || fieldErrors.value.cooldown);

const mutation = useMutation({
  mutationFn: (settings: GroupQzoneAgentSettings) => resources.updateGroupQzoneAgent(props.groupId, settings),
  onSuccess: (result) => {
    diagnostics.value = [safeDiagnostic(result), ...diagnostics.value];
    void query.refetch().then(() => {
      dirty.value = false;
    });
  },
  onError: (error) => {
    diagnostics.value = [diagnosticFromError(error), ...diagnostics.value];
  },
});

function save() {
  if (validationError.value || !data.value) return;
  if (data.value.settings.enabled && !draft.value.enabled && !window.confirm("确认停用本群 QQ 空间 Agent 互动？")) return;
  if (!data.value.settings.enabled && draft.value.enabled && !window.confirm("确认启用本群 QQ 空间 Agent 互动？")) return;
  mutation.mutate({
    enabled: draft.value.enabled,
    group_daily_limit: Number(draft.value.groupDailyLimit),
    target_daily_limit: Number(draft.value.targetDailyLimit),
    target_cooldown_seconds: Number(draft.value.targetCooldownSeconds),
  });
}

function update(patch: Partial<QzoneSettingsDraft>) {
  draft.value = { ...draft.value, ...patch };
  dirty.value = true;
}

function numberOrNull(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function stringifyNumber(value: number | null): string {
  return value === null ? "" : String(value);
}

function operationTone(status: string): "ok" | "warn" | "error" | "running" | "unknown" {
  if (status === "succeeded") return "ok";
  if (status === "definite_failure") return "error";
  if (status === "reserved" || status === "dispatching") return "running";
  if (status === "unknown") return "unknown";
  return "warn";
}
</script>
