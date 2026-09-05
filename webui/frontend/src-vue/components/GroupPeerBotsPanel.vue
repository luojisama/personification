<template>
  <div class="page-stack peer-bot-page">
    <QueryBoundary :pending="query.isPending.value" :error="query.error.value">
      <template v-if="data">
        <div class="summary-grid">
          <Panel eyebrow="PEER BOT / POLICY" title="协作与循环保护">
            <div class="form-grid peer-bot-settings-grid">
              <SwitchField
                id="peer-bot-enabled"
                :model-value="enabled"
                label="启用本群外部 Bot 调用"
                @update:model-value="updateSettings({ enabled: $event })"
              />
              <SwitchField
                id="peer-bot-auto-learn"
                :model-value="autoLearn"
                label="自动学习已批准 Bot 的新协议"
                @update:model-value="updateSettings({ autoLearn: $event })"
              />
              <NumberField
                id="peer-bot-cooldown"
                :model-value="numberOrNull(cooldown)"
                label="冷却秒数"
                :min="0"
                :max="3600"
                @update:model-value="updateSettings({ cooldown: stringifyNumber($event) })"
              />
              <NumberField
                id="peer-bot-ttl"
                :model-value="numberOrNull(ttl)"
                label="回复等待 TTL"
                :min="1"
                :max="600"
                @update:model-value="updateSettings({ ttl: stringifyNumber($event) })"
              />
            </div>
            <p class="muted-copy">
              自动学习只作用于已批准 Bot 的高置信 read/write 协议；不会覆盖管理员模板，admin/dangerous 永不自动批准。
            </p>
            <div class="inline-controls">
              <button
                class="button"
                type="button"
                :disabled="mutation.isPending.value"
                @click="() => (!data?.enabled || enabled || confirmAction('确认停用本群 Peer Bot 调用？')) && saveSettings()"
              >
                保存群级策略
              </button>
              <button
                class="button button-danger"
                type="button"
                :disabled="mutation.isPending.value"
                @click="() => confirmAction('确认清除本群进程内 pending 与 cooldown？不会重发任何命令。') && mutation.mutate({ kind: 'reset' })"
              >
                复位循环保护
              </button>
            </div>
            <dl class="compact-kv">
              <div><dt>单回合上限</dt><dd>1 次</dd></div>
              <div><dt>跨 Bot 深度</dt><dd>1 层</dd></div>
              <div><dt>Pending</dt><dd>{{ data.pending_count }}</dd></div>
              <div><dt>冷却项</dt><dd>{{ data.loop_protection.cooldown_count }}</dd></div>
              <div><dt>观察微批</dt><dd>{{ data.observer.pending_messages }} 条 / {{ data.observer.pending_users }} 个用户</dd></div>
            </dl>
          </Panel>

          <Panel eyebrow="PEER BOT / DISCOVERY" title="LLM 发现建议">
            <template #actions>
              <button
                class="button button-secondary"
                type="button"
                :disabled="mutation.isPending.value || !data.observer.enabled"
                @click="() => confirmAction('只评估当前群已缓冲的观察微批，不会自动授权。继续？') && mutation.mutate({ kind: 'discover' })"
              >
                发现一次
              </button>
            </template>
            <ul v-if="data.discovery_suggestions.length" class="business-list">
              <li v-for="item in data.discovery_suggestions" :key="item.user_id">
                <strong>{{ item.nickname || item.user_id }} · {{ (item.confidence * 100).toFixed(0) }}%</strong>
                <span>{{ item.evidence_tags.join(" / ") || "insufficient_context" }}</span>
                <code>{{ item.reason_code }}</code>
                <div class="inline-controls">
                  <button
                    class="button"
                    type="button"
                    :disabled="mutation.isPending.value"
                    @click="mutation.mutate({ kind: 'bot', userId: item.user_id, nickname: item.nickname, action: 'approve' })"
                  >
                    采纳为 Bot
                  </button>
                  <button
                    class="button button-secondary"
                    type="button"
                    :disabled="mutation.isPending.value"
                    @click="() => confirmAction(`确认忽略候选 ${item.user_id}？`) && mutation.mutate({ kind: 'bot', userId: item.user_id, action: 'reject' })"
                  >
                    忽略建议
                  </button>
                </div>
              </li>
            </ul>
            <div v-else class="query-empty">当前没有待审核的结构化建议。</div>
          </Panel>
        </div>

        <Panel eyebrow="PEER BOT / REGISTRY" :title="`识别与授权（${data.bots.length}）`">
          <div v-if="data.bots.length" class="trace-table-wrap">
            <table class="forensic-table">
              <thead>
                <tr>
                  <th>昵称</th>
                  <th>Bot ID</th>
                  <th>置信度</th>
                  <th>来源</th>
                  <th>状态</th>
                  <th>证据标签</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="bot in data.bots" :key="bot.user_id">
                  <td><strong>{{ bot.nickname || "未命名" }}</strong></td>
                  <td><code>{{ bot.user_id }}</code></td>
                  <td>{{ (bot.confidence * 100).toFixed(0) }}%</td>
                  <td>{{ bot.source }}</td>
                  <td><StateBadge :tone="botTone(bot.status)">{{ bot.status }}</StateBadge></td>
                  <td>{{ bot.evidence_tags.join(" / ") || "—" }}</td>
                  <td>
                    <div class="inline-controls">
                      <button
                        class="button"
                        type="button"
                        :disabled="mutation.isPending.value || bot.status === 'approved'"
                        @click="mutation.mutate({ kind: 'bot', userId: bot.user_id, nickname: bot.nickname, action: 'approve' })"
                      >
                        批准
                      </button>
                      <button
                        class="button button-secondary"
                        type="button"
                        :disabled="mutation.isPending.value || bot.status === 'rejected'"
                        @click="() => confirmAction(`确认拒绝 ${bot.user_id}？`) && mutation.mutate({ kind: 'bot', userId: bot.user_id, action: 'reject' })"
                      >
                        拒绝
                      </button>
                      <button
                        class="button button-danger"
                        type="button"
                        :disabled="mutation.isPending.value || !bot.manual_override"
                        @click="() => confirmAction(`确认清除 ${bot.user_id} 的管理员覆盖并恢复候选状态？`) && mutation.mutate({ kind: 'bot', userId: bot.user_id, action: 'clear' })"
                      >
                        清除覆盖
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="query-empty">当前群尚未观察或配置 Peer Bot。</div>
        </Panel>

        <Panel eyebrow="PEER BOT / COMMANDS" :title="`协议能力目录（${data.commands.length}）`">
          <template #actions>
            <button
              class="button button-secondary"
              type="button"
              @click="resetDraftToNew"
            >
              新增模板
            </button>
          </template>
          <div v-if="data.commands.length" class="trace-table-wrap">
            <table class="forensic-table">
              <thead>
                <tr>
                  <th>命令 ID</th>
                  <th>用途</th>
                  <th>完整模板</th>
                  <th>风险</th>
                  <th>学习证据</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="command in data.commands" :key="command.command_id">
                  <td>
                    <code>{{ command.command_id }}</code>
                    <small>{{ command.target_bot_id }}</small>
                  </td>
                  <td>{{ command.description || "未填写用途说明" }}</td>
                  <td>
                    <code>{{ command.full_template }}</code>
                    <small>{{ command.legacy_mode ? "legacy 兼容" : `${command.command_entry} / ${command.subcommands.join(" / ") || "无子命令"}` }}</small>
                  </td>
                  <td><StateBadge :tone="riskTone(command.risk_level)">{{ command.risk_level }}</StateBadge></td>
                  <td>{{ command.auto_approved ? "自动批准" : "管理员配置" }} · {{ command.evidence_count }} 条</td>
                  <td><StateBadge :tone="botTone(command.status)">{{ command.status }}</StateBadge></td>
                  <td>
                    <div class="inline-controls">
                      <button
                        class="button button-secondary"
                        type="button"
                        @click="editCommand(command)"
                      >
                        编辑 / Dry-run
                      </button>
                      <button
                        class="button button-danger"
                        type="button"
                        :disabled="mutation.isPending.value"
                        @click="() => confirmAction(`确认删除命令 ${command.command_id}？`) && mutation.mutate({ kind: 'delete-command', userId: command.target_bot_id, commandId: command.command_id })"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="query-empty">尚未配置完整命令模板。</div>

          <div class="peer-bot-editor">
            <h3>模板编辑器</h3>
            <div class="form-grid">
              <div class="member-selector form-span">
                <TextField
                  id="peer-bot-member-search"
                  v-model="memberSearch"
                  label="搜索当前群成员"
                  type="search"
                  placeholder="QQ 号、群名片或昵称"
                />
                <SelectField
                  id="peer-bot-target"
                  v-model="draft.target_bot_id"
                  label="目标 Bot"
                  :options="memberSelectOptions"
                  placeholder="选择当前群成员"
                  :error="validation.field === 'target_bot_id' ? validation.error : ''"
                  aria-describedby="peer-command-help"
                />
                <p v-if="membersQuery.isError.value" class="muted-copy">
                  实时成员目录暂不可用；仍可选择注册表中已有的 Bot。
                </p>
                <div v-else class="pagination" aria-label="群成员分页">
                  <button type="button" :disabled="memberOffset === 0" @click="memberOffset = Math.max(0, memberOffset - 50)">上一页</button>
                  <span>第 {{ Math.floor(memberOffset / 50) + 1 }} 页 · 共 {{ membersQuery.data.value?.total ?? 0 }} 人</span>
                  <button type="button" :disabled="membersQuery.data.value?.has_more !== true" @click="memberOffset += 50">下一页</button>
                </div>
              </div>
              <TextField
                id="peer-bot-command-id"
                v-model="draft.command_id"
                label="命令 ID"
                :error="validation.field === 'command_id' ? validation.error : ''"
                aria-describedby="peer-command-help"
              />
              <SelectField
                id="peer-bot-risk-level"
                v-model="draft.risk_level"
                label="风险等级"
                :options="riskLevelOptions"
              />
              <SelectField
                id="peer-bot-status"
                v-model="draft.status"
                label="审核状态"
                :options="statusOptions"
              />
              <fieldset class="peer-command-mode form-span">
                <legend>编辑模式</legend>
                <SelectField
                  id="peer-bot-edit-mode"
                  :model-value="draft.mode ?? 'legacy'"
                  label="编辑模式"
                  hide-label
                  :options="editModeOptions"
                  @update:model-value="switchEditMode($event as 'structured' | 'legacy')"
                />
              </fieldset>
              <template v-if="(draft.mode ?? 'legacy') === 'structured'">
                <TextField
                  id="peer-bot-command-entry"
                  v-model="draft.command_entry"
                  label="命令入口"
                  placeholder=".mc 或 /抽卡"
                  :error="validation.field === 'command_entry' ? validation.error : ''"
                  aria-describedby="peer-command-help"
                />
                <TextField id="peer-bot-subcommand-1" v-model="draft.subcommand_1" label="一级子命令（可选）" placeholder="say" />
                <TextField id="peer-bot-subcommand-2" v-model="draft.subcommand_2" label="二级子命令（可选）" />
                <TextField
                  id="peer-bot-argument-template"
                  v-model="draft.argument_template"
                  label="参数模板（可选）"
                  placeholder="{message}"
                  :error="validation.field === 'argument_template' ? validation.error : ''"
                  aria-describedby="peer-command-help"
                />
                <TextField
                  id="peer-bot-description"
                  v-model="draft.description"
                  class="form-span"
                  label="用途说明"
                  placeholder="例如：向 Minecraft 在线玩家发送聊天消息"
                />
              </template>
              <TextareaField
                v-else
                class="form-span"
                id="peer-bot-full-template"
                v-model="draft.full_template"
                label="完整命令模板"
                :error="validation.field === 'full_template' ? validation.error : ''"
                description="入口必填，子命令可留空；占位符使用单花括号。"
                :rows="3"
                placeholder=".mc say {message} 或 /抽卡"
              />
              <label class="form-span">
                完整命令预览
                <output class="peer-full-command-preview">{{ composedPreview }}</output>
              </label>
              <TextareaField
                class="form-span"
                id="peer-bot-parameter-schema"
                v-model="draft.parameter_schema_text"
                label="高级参数 schema"
                :error="validation.field === 'parameter_schema' ? validation.error : ''"
                description="参数 description 会进入 Agent 能力目录。"
                :rows="9"
              />
            </div>
            <p id="peer-command-help" class="muted-copy">
              入口必填，子命令可留空；占位符使用单花括号，例如 <code>{message}</code>。参数 description 会进入 Agent 能力目录。
            </p>
            <p v-if="validation.error" id="peer-command-error" class="state-error" role="alert">
              {{ validation.error }}
            </p>
            <div class="peer-bot-dry-run">
              <TextareaField id="peer-bot-dry-run-arguments" v-model="argumentsText" label="Dry-run 参数（JSON）" :rows="3" />
              <div class="inline-controls">
                <button class="button button-secondary" type="button" @click="generateSimpleSchema">
                  按占位符生成简单参数
                </button>
                <button
                  class="button button-secondary"
                  type="button"
                  :disabled="Boolean(validation.error)"
                  @click="runDryRun"
                >
                  仅验证，不发送
                </button>
                <button
                  class="button"
                  type="button"
                  :disabled="mutation.isPending.value || Boolean(validation.error) || !validation.schema"
                  @click="saveCommand"
                >
                  保存模板
                </button>
              </div>
              <output v-if="dryRunResult" class="peer-bot-preview" aria-live="polite">
                {{ dryRunResult }}
              </output>
            </div>
          </div>
        </Panel>

        <Panel eyebrow="PEER BOT / INVOCATIONS" title="近期调用与关联状态">
          <div v-if="data.recent_invocations.length" class="trace-table-wrap">
            <table class="forensic-table">
              <thead>
                <tr>
                  <th>Tracking ID</th>
                  <th>命令 ID</th>
                  <th>目标 Bot</th>
                  <th>发送</th>
                  <th>关联</th>
                  <th>回复数</th>
                  <th>耗时</th>
                  <th>诊断</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in data.recent_invocations" :key="`${item.tracking_id}:${item.status}`">
                  <td><code>{{ item.tracking_id }}</code></td>
                  <td><code>{{ item.command_id }}</code></td>
                  <td><code>{{ item.target_bot_id }}</code></td>
                  <td>
                    <StateBadge :tone="item.send_status === 'sent' ? 'ok' : item.send_status === 'failed' ? 'error' : 'unknown'">
                      {{ item.send_status }}
                    </StateBadge>
                  </td>
                  <td>
                    <StateBadge :tone="item.status === 'completed' ? 'ok' : item.status === 'pending' ? 'running' : item.status === 'timeout' ? 'warn' : 'error'">
                      {{ item.status }}
                    </StateBadge>
                  </td>
                  <td>{{ item.reply_message_count }}</td>
                  <td>{{ item.elapsed_ms }} ms</td>
                  <td><code>{{ item.diagnostic_code }}</code></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="query-empty">暂无进程内调用摘要；这里不会显示命令正文或第三方回复原文。</div>
          <p class="muted-copy">
            注册表更新时间：{{ formatDateTime(data.updated_at) }}。运行状态只显示稳定 ID、状态、计数、耗时和诊断码。
          </p>
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
import type {
  GroupMemberOption,
  OperationDiagnostic,
  PeerBotCommandTemplate,
} from "@/api/types";
import { formatDateTime } from "@/lib/format";
import DiagnosticPanel from "./DiagnosticPanel.vue";
import Panel from "./Panel.vue";
import QueryBoundary from "./QueryBoundary.vue";
import StateBadge from "./StateBadge.vue";
import NumberField from "./forms/NumberField.vue";
import SelectField from "./forms/SelectField.vue";
import SwitchField from "./forms/SwitchField.vue";
import TextareaField from "./forms/TextareaField.vue";
import TextField from "./forms/TextField.vue";
import {
  botTone,
  commandDraft,
  composePeerBotCommand,
  emptyDraft,
  renderPeerBotCommandDryRun,
  riskTone,
  structuredFieldsFromLegacy,
  templateFields,
  validatePeerBotCommandDraft,
  type CommandDraft,
} from "./peerBotCommand";

const props = withDefaults(
  defineProps<{
    groupId: string;
    botId?: string;
  }>(),
  { botId: "" },
);

const diagnostics = ref<OperationDiagnostic[]>([]);
function recordDiagnostic(diag: OperationDiagnostic) {
  diagnostics.value = [diag, ...diagnostics.value];
}

const query = useQuery({
  queryKey: computed(() => ["group-peer-bots", props.groupId]),
  queryFn: ({ signal }) => resources.groupPeerBots(props.groupId, signal),
  enabled: computed(() => Boolean(props.groupId)),
});

const data = computed(() => query.data.value);
const memberSearch = ref("");
const memberSearchApplied = ref("");
const memberOffset = ref(0);

const membersQuery = useQuery({
  queryKey: computed(() => ["group-peer-bot-member-options", props.groupId, props.botId, memberOffset.value, memberSearchApplied.value]),
  queryFn: ({ signal }) => resources.groupMembers(props.groupId, props.botId, signal, memberOffset.value, memberSearchApplied.value),
  enabled: computed(() => Boolean(props.groupId && props.botId)),
});

const enabled = ref(false);
const autoLearn = ref(false);
const cooldown = ref("10");
const ttl = ref("30");
const settingsDirty = ref(false);
const draft = ref<CommandDraft>(emptyDraft());
const argumentsText = ref("{}");
const dryRunResult = ref("");

watch(memberSearch, (value, _previous, onCleanup) => {
  const timer = window.setTimeout(() => {
    memberSearchApplied.value = value.trim();
    memberOffset.value = 0;
  }, 250);
  onCleanup(() => window.clearTimeout(timer));
});

function confirmAction(message: string): boolean {
  return window.confirm(message);
}

watch(
  () => props.groupId,
  () => {
    settingsDirty.value = false;
    memberOffset.value = 0;
  },
);

watch(
  [() => query.data.value, settingsDirty],
  ([currentData, dirty]) => {
    if (!currentData) return;
    if (!dirty) {
      enabled.value = currentData.enabled;
      autoLearn.value = Boolean(currentData.policies.auto_learn_approved_commands);
      cooldown.value = String(currentData.policies.cooldown_seconds);
      ttl.value = String(currentData.policies.pending_ttl_seconds);
    }
    if (!draft.value.target_bot_id) {
      draft.value = emptyDraft(currentData.bots[0]?.user_id ?? "");
    }
  },
  { immediate: true },
);

const validation = computed(() =>
  validatePeerBotCommandDraft(draft.value, data.value?.max_command_chars ?? 500),
);

const composedPreview = computed(() =>
  composePeerBotCommand(draft.value) || "尚未生成命令",
);

const memberOptions = computed(() => {
  const options = new Map<string, GroupMemberOption>();
  for (const member of membersQuery.data.value?.members ?? []) {
    const userId = String(member.user_id ?? "").trim();
    if (userId) options.set(userId, member);
  }
  for (const bot of data.value?.bots ?? []) {
    if (!options.has(bot.user_id)) {
      options.set(bot.user_id, { user_id: bot.user_id, nickname: bot.nickname });
    }
  }
  const needle = memberSearch.value.trim().toLowerCase();
  return [...options.values()].filter((member) => {
    if (draft.value.target_bot_id && String(member.user_id) === draft.value.target_bot_id) return true;
    if (!needle) return true;
    return [member.user_id, member.card, member.nickname].some((value) =>
      String(value ?? "").toLowerCase().includes(needle),
    );
  });
});

const memberSelectOptions = computed(() => memberOptions.value.map((member) => ({
  value: String(member.user_id),
  label: `${member.card || member.nickname || "未命名成员"}（${String(member.user_id)}）`,
})));
const riskLevelOptions = [
  { value: "read", label: "read（只读）" },
  { value: "write", label: "write（写入）" },
  { value: "admin", label: "admin（Agent 永不调用）" },
  { value: "dangerous", label: "dangerous（Agent 永不调用）" },
];
const statusOptions = [
  { value: "candidate", label: "candidate" },
  { value: "approved", label: "approved" },
  { value: "rejected", label: "rejected" },
];
const editModeOptions = [
  { value: "structured", label: "结构化协议 v2" },
  { value: "legacy", label: "legacy 完整模板" },
];

function updateSettings(patch: Partial<{ enabled: boolean; autoLearn: boolean; cooldown: string; ttl: string }>): void {
  if (patch.enabled !== undefined) enabled.value = patch.enabled;
  if (patch.autoLearn !== undefined) autoLearn.value = patch.autoLearn;
  if (patch.cooldown !== undefined) cooldown.value = patch.cooldown;
  if (patch.ttl !== undefined) ttl.value = patch.ttl;
  settingsDirty.value = true;
}

function numberOrNull(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function stringifyNumber(value: number | null): string {
  return value === null ? "" : String(value);
}

const mutation = useMutation({
  mutationFn: async (action: { kind: string; [key: string]: unknown }) => {
    if (action.kind === "settings") {
      return resources.updateGroupPeerBotSettings(props.groupId, action.body as {
        enabled: boolean;
        auto_learn_approved_commands: boolean;
        max_calls_per_turn: 1;
        cooldown_seconds: number;
        pending_ttl_seconds: number;
        max_chain_depth: 1;
      });
    }
    if (action.kind === "bot") {
      return resources.updateGroupPeerBotStatus(
        props.groupId,
        String(action.userId),
        action.action as "approve" | "reject" | "clear",
        String(action.nickname ?? ""),
      );
    }
    if (action.kind === "discover") {
      return resources.discoverGroupPeerBots(props.groupId);
    }
    if (action.kind === "reset") {
      return resources.resetGroupPeerBotLoop(props.groupId);
    }
    if (action.kind === "delete-command") {
      return resources.deleteGroupPeerBotCommand(
        props.groupId,
        String(action.userId),
        String(action.commandId),
      );
    }
    const saveDraft = action.draft as CommandDraft;
    const saveSchema = action.schema as PeerBotCommandTemplate["parameter_schema"];
    const structured = (saveDraft.mode ?? "legacy") === "structured";
    return resources.saveGroupPeerBotCommand(
      props.groupId,
      saveDraft.target_bot_id,
      saveDraft.command_id,
      {
        full_template: composePeerBotCommand(saveDraft),
        ...(structured ? {
          command_entry: saveDraft.command_entry?.trim() ?? "",
          subcommands: [saveDraft.subcommand_1, saveDraft.subcommand_2]
            .map((item) => (item as string | undefined)?.trim() ?? "")
            .filter(Boolean),
          argument_template: saveDraft.argument_template?.trim() ?? "",
          description: saveDraft.description?.trim() ?? "",
        } : {}),
        parameter_schema: saveSchema,
        risk_level: saveDraft.risk_level,
        status: saveDraft.status,
      },
    );
  },
  onSuccess: (result, action) => {
    recordDiagnostic(safeDiagnostic(result));
    void query.refetch().then(() => {
      if (action.kind === "settings") {
        settingsDirty.value = false;
      }
    });
  },
  onError: (error) => {
    recordDiagnostic(diagnosticFromError(error));
  },
});

function saveSettings() {
  const cooldownValue = Number(cooldown.value);
  const ttlValue = Number(ttl.value);
  if (
    !Number.isFinite(cooldownValue) ||
    cooldownValue < 0 ||
    cooldownValue > 3600 ||
    !Number.isFinite(ttlValue) ||
    ttlValue < 1 ||
    ttlValue > 600
  ) {
    return;
  }
  if (
    !data.value?.policies.auto_learn_approved_commands &&
    autoLearn.value &&
    !confirmAction("启用后，高置信 read/write 新协议可能自动进入 Agent 能力目录；admin/dangerous 仍只会成为候选。确认启用？")
  ) {
    return;
  }
  mutation.mutate({
    kind: "settings",
    body: {
      enabled: enabled.value,
      auto_learn_approved_commands: autoLearn.value,
      max_calls_per_turn: 1,
      cooldown_seconds: cooldownValue,
      pending_ttl_seconds: ttlValue,
      max_chain_depth: 1,
    },
  });
}

function switchEditMode(mode: "structured" | "legacy") {
  if (mode === "legacy") {
    draft.value = {
      ...draft.value,
      mode,
      full_template: composePeerBotCommand(draft.value) || draft.value.full_template,
    };
  } else {
    const fields = draft.value.command_entry?.trim()
      ? {}
      : structuredFieldsFromLegacy(draft.value.full_template);
    draft.value = {
      ...draft.value,
      ...fields,
      mode,
    };
  }
}

function generateSimpleSchema() {
  const parsed = templateFields(composePeerBotCommand(draft.value));
  if (parsed.error) {
    dryRunResult.value = parsed.error;
    return;
  }
  const schema = {
    type: "object",
    properties: Object.fromEntries(
      parsed.fields.map((field) => [field, { type: "string", description: `${field} 参数` }]),
    ),
    required: parsed.fields,
    additionalProperties: false,
  };
  draft.value = { ...draft.value, parameter_schema_text: JSON.stringify(schema, null, 2) };
  dryRunResult.value = parsed.fields.length
    ? "已按占位符生成简单参数定义；可在高级 schema 中继续填写类型、说明和边界。"
    : "当前命令没有参数，占位符 schema 已清空。";
}

function resetDraftToNew() {
  draft.value = emptyDraft(data.value?.bots[0]?.user_id ?? "");
  argumentsText.value = "{}";
  dryRunResult.value = "";
}

function editCommand(command: PeerBotCommandTemplate) {
  draft.value = commandDraft(command);
  argumentsText.value = "{}";
  dryRunResult.value = "";
}

function runDryRun() {
  dryRunResult.value = renderPeerBotCommandDryRun(draft.value, validation.value, argumentsText.value);
}

function saveCommand() {
  if (!validation.value.schema || validation.value.error) return;
  mutation.mutate({
    kind: "save-command",
    draft: draft.value,
    schema: validation.value.schema,
  });
}
</script>
