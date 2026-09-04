<template>
  <!-- 1. 用户策略与黑名单 -->
  <div v-if="activeMode === 'user-policies'" class="page-stack">
    <PageHeader
      index="25"
      title="用户策略与黑名单"
      description="策略状态、来源、revision 和证据分开展示；修改前必须绑定目标 QQ 与当前 revision。"
    >
      <template #actions>
        <SelectField v-model="policyTier" :options="policyTierOptions" label="策略等级筛选" hide-label />
      </template>
    </PageHeader>

    <Panel eyebrow="POLICY / STATES" title="策略列表">
      <QueryBoundary :pending="policyStatesQuery.isPending.value" :error="policyStatesQuery.error.value">
        <div class="table-responsive">
          <table class="data-table" role="table" aria-label="策略记录列表">
            <thead>
              <tr>
                <th scope="col">QQ ID</th>
                <th scope="col">等级</th>
                <th scope="col">来源</th>
                <th scope="col">Revision</th>
                <th scope="col">到期</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="policyRows.length === 0">
                <td colspan="5" class="empty-notice">当前没有策略记录。</td>
              </tr>
              <tr v-for="(row, idx) in policyRows" :key="textAt(row, 'user_id', 'qq') + idx">
                <td>
                  <button
                    type="button"
                    class="text-link"
                    @click="selectPolicyTarget(textAt(row, 'user_id', 'qq'), Number(row.revision ?? 0))"
                  >
                    <code>{{ textAt(row, "user_id", "qq") }}</code>
                  </button>
                </td>
                <td><StateBadge :tone="resolveStatusTone(row)">{{ textAt(row, "tier", "mode", "state") || "未知" }}</StateBadge></td>
                <td>{{ textAt(row, "source", "reason_code", "actor") || "—" }}</td>
                <td><code>{{ textAt(row, "revision") || "0" }}</code></td>
                <td>{{ formatDateTime(row.expires_at as string | number | null) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </QueryBoundary>
    </Panel>

    <Panel
      v-if="currentSection !== 'list'"
      eyebrow="POLICY / DETAIL"
      :title="policyUserId ? `策略详情 ${policyUserId}` : '选择策略目标'"
    >
      <div v-if="!policyUserId" class="empty-notice" role="status">请先从策略列表选择一个 QQ。</div>
      <div v-else class="page-stack">
        <QueryBoundary :pending="policyEventsQuery.isPending.value" :error="policyEventsQuery.error.value">
          <div class="table-responsive">
            <table class="data-table" role="table" aria-label="策略事件列表">
              <thead>
                <tr>
                  <th scope="col">时间</th>
                  <th scope="col">事件</th>
                  <th scope="col">证据来源</th>
                  <th scope="col">结果</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="policyEventRows.length === 0">
                  <td colspan="4" class="empty-notice">该用户没有保留中的策略事件。</td>
                </tr>
                <tr v-for="(row, idx) in policyEventRows" :key="textAt(row, 'id', 'ts') + idx">
                  <td>{{ formatDateTime(row.ts as string | number | null) }}</td>
                  <td>{{ textAt(row, "action", "event_type", "reason_code") }}</td>
                  <td>{{ textAt(row, "source_kind", "source", "actor") || "—" }}</td>
                  <td><StateBadge :tone="resolveStatusTone(row)">{{ textAt(row, "outcome", "status") || "—" }}</StateBadge></td>
                </tr>
              </tbody>
            </table>
          </div>
        </QueryBoundary>

        <div v-if="currentSection === 'edit'" class="inline-controls filter-control-row">
          <SelectField
            :model-value="policyMode"
            :options="policyModeOptions"
            label="手工策略"
            hide-label
            @update:model-value="updatePolicyMode"
          />
          <NumberField
            :model-value="policyRevision"
            label="期望 revision"
            hide-label
            :min="0"
            @update:model-value="updatePolicyRevision"
          />
          <button
            type="button"
            class="button button-primary"
            :disabled="policyUpdateMutation.isPending.value"
            @click="confirmSavePolicy"
          >
            保存策略
          </button>
        </div>
      </div>
    </Panel>

    <DiagnosticPanel
      v-if="policyUpdateDiagnostic"
      :diagnostic="policyUpdateDiagnostic"
      default-open
    />
  </div>

  <!-- 2. 近期 Bot 消息 -->
  <div v-else-if="activeMode === 'outbound'" class="page-stack">
    <PageHeader
      index="26"
      title="近期 Bot 消息"
      description="按 operation 展示发送证据与 Trace；撤回要求精确确认串，unknown/partial 结果不会出现自动重试入口。"
    />

    <Panel eyebrow="OUTBOUND / LEDGER" title="出站记录">
      <QueryBoundary :pending="outboundQuery.isPending.value" :error="outboundQuery.error.value">
        <div class="table-responsive">
          <table class="data-table" role="table" aria-label="出站记录列表">
            <thead>
              <tr>
                <th scope="col">时间</th>
                <th scope="col">Operation</th>
                <th scope="col">会话</th>
                <th scope="col">发送结果</th>
                <th scope="col">Trace</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="outboundRows.length === 0">
                <td colspan="5" class="empty-notice">当前没有出站账本记录。</td>
              </tr>
              <tr v-for="(row, idx) in outboundRows" :key="textAt(row, 'id', 'operation_id') + idx">
                <td>{{ formatDateTime((row.created_at ?? row.ts) as string | number | null) }}</td>
                <td>
                  <button type="button" class="text-link" @click="selectOutboundRow(row)">
                    <code>{{ textAt(row, "operation_id") }}</code>
                  </button>
                </td>
                <td>{{ textAt(row, "conversation_kind", "session_type") }} / {{ textAt(row, "conversation_id", "session_id") }}</td>
                <td><StateBadge :tone="resolveStatusTone(row)">{{ textAt(row, "status", "outcome") || "未知" }}</StateBadge></td>
                <td><code>{{ textAt(row, "trace_id") || "—" }}</code></td>
              </tr>
            </tbody>
          </table>
        </div>
      </QueryBoundary>
    </Panel>

    <Panel
      v-if="selectedOutbound"
      eyebrow="OUTBOUND / EVIDENCE"
      :title="`Operation ${textAt(selectedOutbound, 'operation_id')}`"
    >
      <dl class="detail-list">
        <div><dt>Bot</dt><dd><code>{{ textAt(selectedOutbound, "bot_id") }}</code></dd></div>
        <div><dt>会话</dt><dd>{{ textAt(selectedOutbound, "conversation_kind", "session_type") }} / {{ textAt(selectedOutbound, "conversation_id", "session_id") }}</dd></div>
        <div><dt>平台消息数</dt><dd>{{ textAt(selectedOutbound, "message_count", "segment_count") || "0" }}</dd></div>
        <div><dt>结果</dt><dd><StateBadge :tone="resolveStatusTone(selectedOutbound)">{{ textAt(selectedOutbound, "status", "outcome") || "未知" }}</StateBadge></dd></div>
      </dl>

      <div v-if="currentSection === 'detail'" class="danger-zone" style="margin-top: var(--space-4);">
        <p>要撤回，输入 <code>RECALL {{ textAt(selectedOutbound, 'operation_id') }}</code>。结果未知时界面不会再次提交。</p>
        <div class="inline-controls filter-control-row">
          <TextField v-model="outboundConfirmation" label="撤回确认串" hide-label placeholder="输入精确确认串" />
          <button
            type="button"
            class="button button-danger"
            :disabled="outboundConfirmation !== `RECALL ${textAt(selectedOutbound, 'operation_id')}` || outboundRecallMutation.isPending.value"
            @click="executeOutboundRecall"
          >
            撤回完整 operation
          </button>
        </div>
      </div>
    </Panel>

    <DiagnosticPanel
      v-if="outboundRecallDiagnostic"
      :diagnostic="outboundRecallDiagnostic"
      default-open
    />
  </div>

  <!-- 3. 数据迁移 -->
  <div v-else-if="activeMode === 'data-transfer'" class="page-stack">
    <PageHeader
      index="27"
      title="数据迁移"
      description="导出、上传、inspect、dry-run、apply、journal 与 rollback 使用同一服务；秘密包在公网 HTTP 下仍由后端拒绝。"
    />

    <Panel
      :eyebrow="`TRANSFER / ${currentSection.toUpperCase()}`"
      :title="currentSection === 'export' ? '导出群安全状态包' : currentSection === 'inspect' ? '上传与验包' : currentSection === 'apply' ? '应用已预演计划' : 'Journal 与回滚'"
    >
      <div class="stacked-form">
        <div class="form-field">
          <span>目标 Bot</span>
          <code v-if="transferBotId">{{ transferBotId }}</code>
          <span v-else class="form-description">请先在页面顶部选择 Bot。</span>
        </div>
        <CurrentGroupSelect
          label="目标群"
          description="使用当前 Bot 的已确认或已配置群；选择会同步到地址栏。"
          required
        />

        <!-- Export 阶段 -->
        <div v-if="currentSection === 'export'" style="margin-top: var(--space-2);">
          <button
            type="button"
            class="button button-primary"
            :disabled="!transferBotId || !transferGroupId || exportMutation.isPending.value"
            @click="confirmCreateExport"
          >
            创建导出
          </button>
        </div>

        <!-- Inspect 阶段 -->
        <template v-if="currentSection === 'inspect'">
          <label>
            迁移包
            <input type="file" accept=".zip,application/zip" @change="onTransferFileSelect" />
          </label>
          <button
            type="button"
            class="button button-primary"
            :disabled="!transferFile || uploadMutation.isPending.value"
            @click="uploadMutation.mutate()"
          >
            上传并安全验包
          </button>
          <TextField v-model="transferTaskId" label="Task ID" placeholder="上传后生成的 Task ID" />
          <button
            type="button"
            class="button button-secondary"
            :disabled="!transferTaskId || !transferBotId || !transferGroupId || dryRunMutation.isPending.value"
            @click="dryRunMutation.mutate()"
          >
            执行 Dry-run
          </button>
        </template>

        <!-- Apply 阶段 -->
        <template v-if="currentSection === 'apply'">
          <TextField v-model="transferTaskId" label="Task ID" placeholder="Task ID" />
          <TextField v-model="transferPlanToken" label="Plan Token" placeholder="Dry-run 返回的 Plan Token" />
          <p>输入 <code>APPLY {{ transferTaskId }}</code> 才能应用。</p>
          <TextField v-model="transferConfirmation" label="应用导入确认串" placeholder="APPLY <Task ID>" />
          <button
            type="button"
            class="button button-danger"
            :disabled="transferConfirmation !== `APPLY ${transferTaskId}` || !transferPlanToken || applyMutation.isPending.value"
            @click="applyMutation.mutate()"
          >
            应用导入
          </button>
        </template>

        <!-- Journal / 回滚 阶段 -->
        <template v-if="currentSection === 'journal'">
          <TextField v-model="transferJournalId" label="Journal ID" placeholder="导入成功的 Journal ID" />
          <p>输入 <code>ROLLBACK {{ transferJournalId }}</code> 才能回滚。</p>
          <TextField v-model="transferConfirmation" label="回滚确认串" placeholder="ROLLBACK <Journal ID>" />
          <button
            type="button"
            class="button button-danger"
            :disabled="transferConfirmation !== `ROLLBACK ${transferJournalId}` || rollbackMutation.isPending.value"
            @click="rollbackMutation.mutate()"
          >
            回滚本次导入
          </button>
        </template>
      </div>

      <dl v-if="inspectQuery.data.value && currentSection === 'inspect'" class="detail-list" style="margin-top: var(--space-4);">
        <div><dt>任务 ID</dt><dd><code>{{ transferTaskId }}</code></dd></div>
        <div><dt>Schema</dt><dd>{{ textAt(asRecord(inspectQuery.data.value), "schema_version") }}</dd></div>
        <div><dt>源 Bot</dt><dd><code>{{ textAt(asRecord(inspectQuery.data.value), "bot_id", "source_bot_id") }}</code></dd></div>
        <div><dt>源群</dt><dd><code>{{ textAt(asRecord(inspectQuery.data.value), "group_id", "source_group_id") }}</code></dd></div>
      </dl>
    </Panel>

    <DiagnosticPanel
      v-if="transferDiagnostic"
      :diagnostic="transferDiagnostic"
      default-open
    />
  </div>

  <!-- 4. 审计日志 -->
  <div v-else-if="activeMode === 'audit'" class="page-stack">
    <PageHeader
      index="28"
      title="审计日志"
      description="游标式读取管理员操作、目标、结果和脱敏详情；完整请求体、密钥和 Cookie 不进入此页面。"
    >
      <template #actions>
        <SelectField v-model="auditActionFilter" :options="auditActionOptions" label="审计动作筛选" hide-label />
      </template>
    </PageHeader>

    <Panel eyebrow="AUDIT / RECORDS" :title="currentSection === 'overview' ? '操作概览' : '审计记录'">
      <QueryBoundary :pending="auditRecordsQuery.isPending.value" :error="auditRecordsQuery.error.value">
        <div class="table-responsive">
          <table class="data-table" role="table" aria-label="审计日志列表">
            <thead>
              <tr>
                <th scope="col">时间</th>
                <th scope="col">动作</th>
                <th scope="col">管理员</th>
                <th scope="col">目标</th>
                <th scope="col">结果</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="auditRows.length === 0">
                <td colspan="5" class="empty-notice">当前筛选条件下没有审计记录。</td>
              </tr>
              <tr v-for="(row, idx) in auditRows" :key="textAt(row, 'id', 'ts') + idx">
                <td>{{ formatDateTime(row.ts as string | number | null) }}</td>
                <td>
                  <button type="button" class="text-link" @click="selectedAudit = row">
                    {{ textAt(row, "action") }}
                  </button>
                </td>
                <td><code>{{ textAt(row, "qq", "admin_qq") }}</code></td>
                <td>{{ textAt(row, "target") }}</td>
                <td><StateBadge :tone="resolveStatusTone(row)">{{ textAt(row, "outcome", "status") || "—" }}</StateBadge></td>
              </tr>
            </tbody>
          </table>
        </div>
      </QueryBoundary>
    </Panel>

    <Panel
      v-if="(currentSection === 'detail' || selectedAudit) && selectedAudit"
      eyebrow="AUDIT / SAFE DETAIL"
      title="脱敏详情"
    >
      <dl class="detail-list">
        <div><dt>动作</dt><dd>{{ textAt(selectedAudit, "action") }}</dd></div>
        <div><dt>目标</dt><dd>{{ textAt(selectedAudit, "target") }}</dd></div>
        <div><dt>设备摘要</dt><dd><code>{{ textAt(selectedAudit, "device_id", "device_hash") || "—" }}</code></dd></div>
        <div><dt>诊断码</dt><dd><code>{{ textAt(asRecord(selectedAudit.detail), "code", "diagnostic_code") || "—" }}</code></dd></div>
      </dl>
    </Panel>
  </div>

  <!-- 5. QQ 管理 -->
  <div v-else-if="activeMode === 'qq'" class="page-stack">
    <PageHeader
      index="30"
      title="QQ 管理"
      description="账号、群和好友使用专用视图；外部写操作绑定目标 ID，服务端三态结果未知时不会自动重试。"
    >
      <template v-if="currentSection !== 'accounts' && currentSection !== 'profile'" #actions>
        <div class="search-field">
          <TextField v-model="qqSearch" label="搜索 ID 或名称" hide-label placeholder="搜索 ID 或名称" type="search" />
        </div>
      </template>
    </PageHeader>

    <Panel
      v-if="currentSection === 'accounts' || currentSection === 'profile'"
      eyebrow="QQ / PROFILE"
      title="Bot 账号与资料操作"
    >
      <QueryBoundary :pending="qqQuery.isPending.value" :error="qqQuery.error.value">
        <dl class="detail-list">
          <div><dt>Bot QQ</dt><dd><code>{{ textAt(asRecord(qqQuery.data.value), "user_id") }}</code></dd></div>
          <div><dt>昵称</dt><dd>{{ textAt(asRecord(qqQuery.data.value), "nickname") || "—" }}</dd></div>
        </dl>
      </QueryBoundary>

      <div v-if="currentSection === 'profile'" class="stacked-form" style="margin-top: var(--space-4);">
        <TextField v-model="qqNickname" label="新昵称" placeholder="输入新昵称" />
        <button
          type="button"
          class="button button-primary"
          :disabled="!qqNickname.trim() || qqProfileMutation.isPending.value"
          @click="confirmUpdateNickname"
        >
          修改昵称
        </button>

        <TextareaField
          id="qq-new-signature"
          v-model="qqSignature"
          label="新签名"
          :rows="3"
          placeholder="输入新个性签名"
        />
        <button
          type="button"
          class="button button-primary"
          :disabled="qqProfileMutation.isPending.value"
          @click="confirmUpdateSignature"
        >
          修改签名
        </button>
      </div>
    </Panel>

    <Panel
      v-if="currentSection === 'groups' || currentSection === 'friends'"
      :eyebrow="`QQ / ${currentSection.toUpperCase()}`"
      :title="currentSection === 'groups' ? '已知群目录' : '好友目录'"
    >
      <QueryBoundary :pending="qqQuery.isPending.value" :error="qqQuery.error.value">
        <div class="table-responsive">
          <table class="data-table" role="table" :aria-label="currentSection === 'groups' ? '群列表' : '好友列表'">
            <thead>
              <tr>
                <th scope="col">ID</th>
                <th scope="col">名称</th>
                <th scope="col">{{ currentSection === 'groups' ? '成员数' : '备注' }}</th>
                <th scope="col">危险操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="filteredQqRows.length === 0">
                <td colspan="4" class="empty-notice">当前没有可展示的目录记录。</td>
              </tr>
              <tr v-for="(row, idx) in filteredQqRows" :key="textAt(row, 'group_id', 'user_id') + idx">
                <td><code>{{ textAt(row, "group_id", "user_id") }}</code></td>
                <td>{{ textAt(row, "group_name", "nickname", "remark") }}</td>
                <td>{{ textAt(row, "member_count", "remark") || "—" }}</td>
                <td>
                  <button
                    type="button"
                    class="button button-danger"
                    @click="initiateQqDangerous(textAt(row, 'group_id', 'user_id'))"
                  >
                    {{ currentSection === "groups" ? "准备退群" : "准备删除" }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </QueryBoundary>
    </Panel>

    <Panel v-if="qqTargetId" eyebrow="QQ / EXTERNAL WRITE" title="外部写操作二次核对">
      <p>目标：<code>{{ qqTargetId }}</code>。请输入目标 ID 才能继续；提交后如果结果未知，页面不会自动再次调用。</p>
      <div class="inline-controls filter-control-row">
        <TextField v-model="qqConfirmation" label="QQ 操作目标确认" hide-label placeholder="输入目标 ID" />
        <button
          type="button"
          class="button button-danger"
          :disabled="qqConfirmation !== qqTargetId || qqDangerousMutation.isPending.value"
          @click="executeQqDangerous"
        >
          确认执行
        </button>
      </div>
    </Panel>

    <DiagnosticPanel
      v-if="qqDiagnostic"
      :diagnostic="qqDiagnostic"
      default-open
    />
  </div>

  <!-- 6. 设备管理 -->
  <div v-else-if="activeMode === 'devices'" class="page-stack">
    <PageHeader
      index="31"
      title="设备管理"
      description="当前、已授权、待审批和历史信任设备分开显示；审批和撤销绑定设备 ID 并写入审计。"
    />

    <Panel
      :eyebrow="`DEVICES / ${currentSection.toUpperCase()}`"
      :title="currentSection === 'pending' ? '待审批设备' : currentSection === 'trusted' ? '历史信任设备' : currentSection === 'current' ? '当前设备' : '已授权设备'"
    >
      <p v-if="currentSection === 'current'">当前设备 ID：<code>{{ currentDeviceId || "—" }}</code></p>
      <QueryBoundary :pending="devicesQuery.isPending.value" :error="devicesQuery.error.value">
        <div class="table-responsive">
          <table class="data-table" role="table" aria-label="设备列表">
            <thead>
              <tr>
                <th scope="col">设备 ID</th>
                <th scope="col">名称</th>
                <th scope="col">浏览器摘要</th>
                <th scope="col">状态</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="filteredDevicesRows.length === 0">
                <td colspan="5" class="empty-notice">当前分类没有设备。</td>
              </tr>
              <tr v-for="(row, idx) in filteredDevicesRows" :key="textAt(row, 'id') + idx">
                <td><code>{{ textAt(row, "id") }}</code></td>
                <td>{{ textAt(row, "label") }}</td>
                <td>{{ textAt(row, "ua") || "—" }}</td>
                <td><StateBadge :tone="resolveStatusTone(row)">{{ textAt(row, "status") || "未知" }}</StateBadge></td>
                <td>
                  <button
                    type="button"
                    :class="currentSection === 'pending' ? 'button button-primary' : 'button button-danger'"
                    @click="initiateDeviceAction(textAt(row, 'id'))"
                  >
                    {{ currentSection === 'pending' ? '准备批准' : currentSection === 'trusted' ? '准备移除信任' : '准备撤销' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </QueryBoundary>
    </Panel>

    <Panel v-if="deviceTargetId" eyebrow="DEVICES / CONFIRM" title="核对设备目标">
      <p>请输入设备 ID <code>{{ deviceTargetId }}</code>。</p>
      <div class="inline-controls filter-control-row">
        <TextField v-model="deviceConfirmation" label="设备目标核对" hide-label placeholder="输入设备 ID" />
        <button
          type="button"
          class="button button-danger"
          :disabled="deviceConfirmation !== deviceTargetId || deviceActionMutation.isPending.value"
          @click="executeDeviceAction"
        >
          确认操作
        </button>
      </div>
    </Panel>

    <DiagnosticPanel
      v-if="deviceDiagnostic"
      :diagnostic="deviceDiagnostic"
      default-open
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";

import { safeDiagnostic } from "@/api/diagnostics";
import { resources } from "@/api/resources";
import type { OperationDiagnostic } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import DiagnosticPanel from "@vue-app/components/DiagnosticPanel.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import CurrentGroupSelect from "@vue-app/components/CurrentGroupSelect.vue";
import NumberField from "@vue-app/components/forms/NumberField.vue";
import SelectField from "@vue-app/components/forms/SelectField.vue";
import TextField from "@vue-app/components/forms/TextField.vue";
import TextareaField from "@vue-app/components/forms/TextareaField.vue";
import { groupIdFromQuery } from "@vue-app/composables/currentGroup";
import { useBotStore } from "@vue-app/stores/bot";
import { useCurrentGroupStore } from "@vue-app/stores/currentGroup";

type BusinessRecord = Record<string, unknown>;

const props = withDefaults(
  defineProps<{
    mode?: "user-policies" | "outbound" | "data-transfer" | "audit" | "qq" | "devices";
  }>(),
  {
    mode: undefined,
  },
);

const route = useRoute();
const client = useQueryClient();
const botStore = useBotStore();
const currentGroupStore = useCurrentGroupStore();

const activeMode = computed(() => props.mode || (route.meta.mode as typeof props.mode) || "user-policies");
const currentSection = computed(() => String(route.params.section || (activeMode.value === "audit" ? "records" : activeMode.value === "qq" ? "accounts" : activeMode.value === "devices" ? "current" : activeMode.value === "data-transfer" ? "export" : "list")));

function asRecord(value: unknown): BusinessRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as BusinessRecord)
    : {};
}

function recordsAt(source: unknown, ...keys: string[]): BusinessRecord[] {
  const rec = asRecord(source);
  for (const key of keys) {
    const val = rec[key];
    if (Array.isArray(val)) return val.map((item) => asRecord(item));
  }
  return [];
}

function textAt(source: unknown, ...keys: string[]): string {
  const rec = asRecord(source);
  for (const key of keys) {
    const val = rec[key];
    if (typeof val === "string" && val.trim().length > 0) return val;
    if (typeof val === "number" || typeof val === "boolean") return String(val);
  }
  return "";
}

function operationDiagnostic(value: unknown): OperationDiagnostic {
  const payload = asRecord(value);
  const nested = asRecord(payload.diagnostic);
  return safeDiagnostic(Object.keys(nested).length ? nested : payload);
}

function resolveStatusTone(row: BusinessRecord): "ok" | "warn" | "error" | "running" | "unknown" {
  const status = textAt(row, "status", "outcome", "state", "tier", "mode").toLowerCase();
  if (status === "ok" || status === "allow" || status === "manual_allow" || status === "active" || status === "succeeded") return "ok";
  if (status === "warn" || status === "partial" || status === "pending" || status === "quarantined") return "warn";
  if (status === "failed" || status === "block" || status === "blocked" || status === "manual_block" || status === "error") return "error";
  return "unknown";
}

/* ==================== 1. 用户策略与黑名单 ==================== */
const policyTier = ref("");
const policyUserId = ref("");
const policyMode = ref<"block" | "allow" | "inherit">("inherit");
const policyRevision = ref(0);
const policyTierOptions = [
  { value: "", label: "全部等级" },
  { value: "allow", label: "允许" },
  { value: "blocked", label: "阻止" },
  { value: "manual_allow", label: "手工允许" },
  { value: "manual_block", label: "手工阻止" },
];
const policyModeOptions = [
  { value: "inherit", label: "继承自动策略" },
  { value: "allow", label: "手工允许" },
  { value: "block", label: "手工阻止" },
];

const policyStatesQuery = useQuery({
  queryKey: computed(() => ["user-policies", policyTier.value]),
  queryFn: ({ signal }) => resources.userPolicyStates(policyTier.value, signal),
  enabled: computed(() => activeMode.value === "user-policies"),
});

const policyEventsQuery = useQuery({
  queryKey: computed(() => ["user-policy-events", policyUserId.value]),
  queryFn: ({ signal }) => resources.userPolicyEvents(policyUserId.value, signal),
  enabled: computed(() => activeMode.value === "user-policies" && Boolean(policyUserId.value) && currentSection.value !== "list"),
});

const policyUpdateMutation = useMutation({
  mutationFn: () =>
    resources.updateUserPolicy(policyUserId.value, {
      mode: policyMode.value,
      expected_revision: policyRevision.value,
      reason_code: "webui_manual_override",
    }),
  onSuccess: () => {
    void client.invalidateQueries({ queryKey: ["user-policies"] });
  },
});

const policyRows = computed(() => recordsAt(policyStatesQuery.data.value, "states", "items"));
const policyEventRows = computed(() => recordsAt(policyEventsQuery.data.value, "events"));
const policyUpdateDiagnostic = computed(() =>
  policyUpdateMutation.data.value ? operationDiagnostic(policyUpdateMutation.data.value) : null,
);

function selectPolicyTarget(userId: string, rev: number) {
  policyUserId.value = userId;
  policyRevision.value = rev;
}

function updatePolicyMode(value: string) {
  if (value === "block" || value === "allow" || value === "inherit") {
    policyMode.value = value;
  }
}

function updatePolicyRevision(value: number | null) {
  if (value !== null && value >= 0) policyRevision.value = value;
}

function confirmSavePolicy() {
  if (window.confirm(`确认把 QQ ${policyUserId.value} 的策略修改为 ${policyMode.value}，revision=${policyRevision.value}？`)) {
    policyUpdateMutation.mutate();
  }
}

/* ==================== 2. 近期 Bot 消息 ==================== */
const selectedOutbound = ref<BusinessRecord | null>(null);
const outboundConfirmation = ref("");

const outboundQuery = useQuery({
  queryKey: ["outbound-recent"],
  queryFn: ({ signal }) => resources.outboundRecent(signal),
  enabled: computed(() => activeMode.value === "outbound"),
});

const outboundRecallMutation = useMutation({
  mutationFn: (row: BusinessRecord) =>
    resources.recallOutbound(textAt(row, "operation_id"), {
      bot_id: textAt(row, "bot_id"),
      conversation_kind: textAt(row, "conversation_kind", "session_type"),
      conversation_id: textAt(row, "conversation_id", "session_id"),
      confirmation: outboundConfirmation.value,
    }),
});

const outboundRows = computed(() => recordsAt(outboundQuery.data.value, "messages", "items"));
const outboundRecallDiagnostic = computed(() =>
  outboundRecallMutation.data.value ? operationDiagnostic(outboundRecallMutation.data.value) : null,
);

function selectOutboundRow(row: BusinessRecord) {
  selectedOutbound.value = row;
  outboundConfirmation.value = "";
}

function executeOutboundRecall() {
  if (selectedOutbound.value) {
    outboundRecallMutation.mutate(selectedOutbound.value);
  }
}

/* ==================== 3. 数据迁移 ==================== */
const transferBotId = computed(() => String(botStore.selectedBotId || "").trim());
const transferGroupId = computed(() =>
  groupIdFromQuery(route.query.group_id) || currentGroupStore.groupIdFor(transferBotId.value),
);
const transferTaskId = ref("");
const transferJournalId = ref("");
const transferPlanToken = ref("");
const transferFile = ref<File | null>(null);
const transferConfirmation = ref("");

const exportMutation = useMutation({
  mutationFn: () =>
    resources.createStateExport({
      bot_id: transferBotId.value,
      group_id: transferGroupId.value,
      datasets: [],
    }),
});

const uploadMutation = useMutation({
  mutationFn: () => resources.uploadStateImport(transferFile.value as File),
  onSuccess: (value) => {
    transferTaskId.value = textAt(asRecord(value), "task_id");
  },
});

const inspectQuery = useQuery({
  queryKey: computed(() => ["data-transfer-inspect", transferTaskId.value]),
  queryFn: ({ signal }) => resources.inspectImport(transferTaskId.value, signal),
  enabled: computed(() => activeMode.value === "data-transfer" && currentSection.value !== "export" && Boolean(transferTaskId.value)),
});

const dryRunMutation = useMutation({
  mutationFn: () =>
    resources.dryRunImport(transferTaskId.value, {
      target_bot_id: transferBotId.value,
      target_group_id: transferGroupId.value,
      mode: "merge",
      allow_same_identity: false,
    }),
  onSuccess: (value) => {
    transferPlanToken.value = textAt(asRecord(value), "plan_token");
  },
});

const applyMutation = useMutation({
  mutationFn: () =>
    resources.applyImport(transferTaskId.value, {
      target_bot_id: transferBotId.value,
      target_group_id: transferGroupId.value,
      mode: "merge",
      allow_same_identity: false,
      plan_token: transferPlanToken.value,
    }),
  onSuccess: (value) => {
    transferJournalId.value = textAt(asRecord(value), "journal_id");
  },
});

const rollbackMutation = useMutation({
  mutationFn: () => resources.rollbackImport(transferJournalId.value),
});

const latestTransferResult = computed(
  () =>
    exportMutation.data.value ??
    uploadMutation.data.value ??
    dryRunMutation.data.value ??
    applyMutation.data.value ??
    rollbackMutation.data.value,
);

const transferDiagnostic = computed(() =>
  latestTransferResult.value ? operationDiagnostic(latestTransferResult.value) : null,
);

function onTransferFileSelect(event: Event) {
  const target = event.target as HTMLInputElement;
  transferFile.value = target.files?.[0] ?? null;
}

function confirmCreateExport() {
  if (window.confirm(`确认导出 Bot ${transferBotId.value} / 群 ${transferGroupId.value} 的群安全状态包？`)) {
    exportMutation.mutate();
  }
}

/* ==================== 4. 审计日志 ==================== */
const auditActionFilter = ref("");
const selectedAudit = ref<BusinessRecord | null>(null);

const auditActionsQuery = useQuery({
  queryKey: ["audit-actions"],
  queryFn: ({ signal }) => resources.auditActions(signal),
  enabled: computed(() => activeMode.value === "audit"),
});

const auditRecordsQuery = useQuery({
  queryKey: computed(() => ["audit-records", auditActionFilter.value]),
  queryFn: ({ signal }) => resources.auditRecent(auditActionFilter.value, signal),
  enabled: computed(() => activeMode.value === "audit"),
});

const auditActionRows = computed(() => recordsAt(auditActionsQuery.data.value, "actions"));
const auditActionOptions = computed(() => [
  { value: "", label: "全部动作" },
  ...auditActionRows.value
    .map((row) => ({ value: textAt(row, "key"), label: textAt(row, "label", "key") }))
    .filter((row) => Boolean(row.value)),
]);
const auditRows = computed(() => recordsAt(auditRecordsQuery.data.value, "entries", "items"));

/* ==================== 5. QQ 管理 ==================== */
const qqSearch = ref("");
const qqNickname = ref("");
const qqSignature = ref("");
const qqTargetId = ref("");
const qqConfirmation = ref("");

const qqPath = computed(() =>
  currentSection.value === "groups" ? "groups" : currentSection.value === "friends" ? "friends" : "info",
);

const qqQuery = useQuery({
  queryKey: computed(() => ["qq-management", qqPath.value]),
  queryFn: ({ signal }) => resources.qqGet(qqPath.value, signal),
  enabled: computed(() => activeMode.value === "qq"),
});

const qqProfileMutation = useMutation({
  mutationFn: ({ kind, value }: { kind: "nickname" | "signature"; value: string }) =>
    resources.qqPost(kind, { [kind]: value }),
  onSuccess: () => {
    void client.invalidateQueries({ queryKey: ["qq-management"] });
  },
});

const qqDangerousMutation = useMutation({
  mutationFn: ({ kind, id }: { kind: "group" | "friend"; id: string }) =>
    kind === "group"
      ? resources.qqPost(`groups/${encodeURIComponent(id)}/leave`, {
          confirm: qqConfirmation.value,
          is_dismiss: false,
        })
      : resources.qqDelete(`friends/${encodeURIComponent(id)}`, {
          confirm: qqConfirmation.value,
        }),
});

const allQqRows = computed(() =>
  currentSection.value === "groups"
    ? recordsAt(qqQuery.data.value, "groups")
    : currentSection.value === "friends"
      ? recordsAt(qqQuery.data.value, "friends")
      : [asRecord(qqQuery.data.value)],
);

const filteredQqRows = computed(() =>
  allQqRows.value.filter((row) =>
    `${textAt(row, "group_id", "user_id")} ${textAt(row, "group_name", "nickname", "remark")}`
      .toLocaleLowerCase("zh-CN")
      .includes(qqSearch.value.toLocaleLowerCase("zh-CN")),
  ),
);

const qqDiagnostic = computed(() => {
  const res = qqProfileMutation.data.value ?? qqDangerousMutation.data.value;
  return res ? operationDiagnostic(res) : null;
});

function confirmUpdateNickname() {
  if (window.confirm(`确认把 Bot 昵称修改为“${qqNickname.value}”？`)) {
    qqProfileMutation.mutate({ kind: "nickname", value: qqNickname.value });
  }
}

function confirmUpdateSignature() {
  if (window.confirm("确认修改当前 Bot 的个性签名？")) {
    qqProfileMutation.mutate({ kind: "signature", value: qqSignature.value });
  }
}

function initiateQqDangerous(id: string) {
  qqTargetId.value = id;
  qqConfirmation.value = "";
}

function executeQqDangerous() {
  qqDangerousMutation.mutate({
    kind: currentSection.value === "groups" ? "group" : "friend",
    id: qqTargetId.value,
  });
}

/* ==================== 6. 设备管理 ==================== */
const deviceTargetId = ref("");
const deviceConfirmation = ref("");

const deviceEndpoint = computed(() =>
  currentSection.value === "pending"
    ? "pending-devices"
    : currentSection.value === "trusted"
      ? "trusted-devices"
      : "devices",
);

const devicesQuery = useQuery({
  queryKey: computed(() => ["devices", deviceEndpoint.value]),
  queryFn: ({ signal }) => resources.deviceGet(deviceEndpoint.value, signal),
  enabled: computed(() => activeMode.value === "devices"),
});

const deviceActionMutation = useMutation({
  mutationFn: ({ id, kind }: { id: string; kind: "approve" | "revoke" | "untrust" }) =>
    kind === "approve"
      ? resources.devicePost(`devices/${encodeURIComponent(id)}/approve`)
      : kind === "untrust"
        ? resources.deviceDelete(`trusted-devices/${encodeURIComponent(id)}`)
        : resources.deviceDelete(`devices/${encodeURIComponent(id)}`),
  onSuccess: () => {
    void client.invalidateQueries({ queryKey: ["devices"] });
  },
});

const deviceRows = computed(() => recordsAt(devicesQuery.data.value, "devices", "items"));
const currentDeviceId = computed(() => textAt(asRecord(devicesQuery.data.value), "current_device_id"));

const filteredDevicesRows = computed(() =>
  currentSection.value === "current"
    ? deviceRows.value.filter((row) => textAt(row, "id") === currentDeviceId.value)
    : deviceRows.value,
);

const deviceDiagnostic = computed(() =>
  deviceActionMutation.data.value ? operationDiagnostic(deviceActionMutation.data.value) : null,
);

function initiateDeviceAction(id: string) {
  deviceTargetId.value = id;
  deviceConfirmation.value = "";
}

function executeDeviceAction() {
  deviceActionMutation.mutate({
    id: deviceTargetId.value,
    kind: currentSection.value === "pending" ? "approve" : currentSection.value === "trusted" ? "untrust" : "revoke",
  });
}
</script>
