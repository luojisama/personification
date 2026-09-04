<template>
  <div class="page-stack">
    <PageHeader
      index="05"
      title="模型测试"
      description="单路由、全路由、人设 Prompt 与媒体测试入口。视频路由探针只证明路由接受视频；完整视频回合必须经过语义帧、Agent、工具、证据门与输出检查。"
    />

    <div v-if="topDiagnostic" class="diagnostic-panel diagnostic-panel-error" role="alert">
      <div class="diagnostic-header">
        <StateBadge tone="error">{{ topDiagnostic.title }}</StateBadge>
        <code>{{ topDiagnostic.code }}</code>
      </div>
      <p v-if="topDiagnostic.message" class="diagnostic-message">{{ topDiagnostic.message }}</p>
      <p v-if="topDiagnostic.suggestion" class="diagnostic-suggestion"><strong>建议：</strong>{{ topDiagnostic.suggestion }}</p>
    </div>

    <div class="overview-grid">
      <Panel eyebrow="PROVIDER / CHAT" title="单路由与全路由对照">
        <TextareaField
          class="stacked-field"
          id="model-test-prompt"
          v-model="prompt"
          label="测试消息"
          description="测试消息将发送至对应 Provider 路由，产生真实 API 消耗。"
          :error="prompt.trim() ? '' : '测试消息不能为空。'"
          :rows="4"
        />
        <div class="dossier-actions">
          <button
            class="button button-secondary"
            type="button"
            :disabled="chatMutation.isPending.value"
            @click="handleRunChat('single')"
          >
            {{ chatMutation.isPending.value ? '测试中…' : '测试当前路由' }}
          </button>
          <button
            class="button button-secondary"
            type="button"
            :disabled="chatMutation.isPending.value"
            @click="handleRunChat('all')"
          >
            {{ chatMutation.isPending.value ? '测试中…' : '测试全部路由' }}
          </button>
        </div>
      </Panel>

      <Panel eyebrow="PERSONA / PROMPT" title="人设 Prompt 预览">
        <p>读取当前服务端实际生效的人设来源和质量信息，不在浏览器重新拼接 Prompt。</p>
        <button
          class="button button-secondary"
          type="button"
          :disabled="personaQuery.isFetching.value"
          @click="loadPersonaPrompt"
        >
          {{ personaQuery.isFetching.value ? '加载中…' : '加载当前 Prompt' }}
        </button>
        <QueryBoundary :pending="personaQuery.isFetching.value" :error="personaQuery.error.value">
          <div v-if="personaData" class="page-stack" style="margin-top: var(--space-3);">
            <dl class="compact-kv">
              <div><dt>来源</dt><dd>{{ textAt(personaData, 'source') }}</dd></div>
              <div>
                <dt>状态</dt>
                <dd>
                  <StateBadge :tone="personaData.exists === true ? 'ok' : 'error'">
                    {{ personaData.exists === true ? '可用' : '不可用' }}
                  </StateBadge>
                </dd>
              </div>
              <div><dt>类型</dt><dd>{{ personaData.is_file === true ? '文件' : '运行时内联配置' }}</dd></div>
              <div><dt>大小</dt><dd>{{ typeof personaData.size === 'number' ? `${personaData.size} bytes` : '—' }}</dd></div>
            </dl>
            <pre
              v-if="personaContent !== '—'"
              class="prompt-preview"
              aria-label="当前人设 Prompt"
            >{{ personaContent }}</pre>
            <EmptyState v-else code="persona_prompt_content_empty">
              当前来源没有可展示的 Prompt 内容。
            </EmptyState>
            <div v-if="personaDiagnostic" class="diagnostic-panel">
              <div class="diagnostic-header">
                <StateBadge :tone="personaDiagnostic.ok ? 'ok' : 'warn'">{{ personaDiagnostic.title }}</StateBadge>
                <code>{{ personaDiagnostic.code }}</code>
              </div>
              <p v-if="personaDiagnostic.message">{{ personaDiagnostic.message }}</p>
            </div>
          </div>
        </QueryBoundary>
      </Panel>

      <Panel eyebrow="VIDEO / ROUTE PROBE" title="视频路由探针">
        <p>只验证配置的视频理解路线，不进入聊天 Agent，也不发送 QQ。</p>
        <div class="stacked-field">
          <input
            type="file"
            accept="video/*,.mkv,.avi"
            aria-label="选择测试视频文件"
            @change="onFileChange"
          />
        </div>
        <div class="dossier-actions">
          <button
            class="button button-secondary"
            type="button"
            :disabled="!selectedVideo || probeVideoMutation.isPending.value"
            @click="handleProbeVideo"
          >
            {{ probeVideoMutation.isPending.value ? '探针运行中…' : '确认并运行路由探针' }}
          </button>
        </div>
      </Panel>

      <Panel eyebrow="VIDEO / FULL TURN" title="完整视频回合">
        <p>使用与真实 QQ 回合一致的语义帧、规划、Agent、媒体工具、证据门和可见输出门。发送接口被替换为只捕获代理，不会触达 QQ。</p>
        <div class="dossier-actions">
          <button
            class="button"
            type="button"
            :disabled="!selectedVideo || videoTurnMutation.isPending.value"
            @click="handleVideoTurn"
          >
            {{ videoTurnMutation.isPending.value ? '完整回合运行中…' : '确认并运行完整无发送回合' }}
          </button>
        </div>
        <div class="security-manifest">
          成功条件同时要求捕获可见回复和关联成功的 <code>vision_analyze</code> 视频证据；仅有路由探针结果不会通过。
        </div>
      </Panel>
    </div>

    <Panel v-if="resultRecord" eyebrow="TEST RESULT" title="最近测试结果">
      <div class="page-stack">
        <dl class="compact-kv">
          <div>
            <dt>结果</dt>
            <dd>
              <StateBadge :tone="resultRecord.ok === true ? 'ok' : 'error'">
                {{ resultRecord.ok === true ? 'succeeded' : textAt(resultRecord, 'overall', 'state') }}
              </StateBadge>
            </dd>
          </div>
          <div><dt>诊断码</dt><dd><code>{{ textAt(resultRecord, 'code', 'diagnosis_code', 'diagnostic_code') }}</code></dd></div>
          <div><dt>耗时</dt><dd>{{ typeof resultRecord.duration_ms === 'number' ? `${resultRecord.duration_ms} ms` : '—' }}</dd></div>
          <div><dt>出站</dt><dd>{{ textAt(resultRecord, 'outbound') }}</dd></div>
          <div v-if="resultTraceId !== '—'">
            <dt>Trace</dt>
            <dd>
              <RouterLink :to="`/runtime/traces/timeline/${encodeURIComponent(resultTraceId)}`">
                <code>{{ resultTraceId }}</code>
              </RouterLink>
            </dd>
          </div>
        </dl>

        <div v-if="summaryEntries.length > 0" class="metric-ribbon" aria-label="测试统计">
          <article v-for="[key, count] in summaryEntries" :key="key">
            <span>{{ key }}</span>
            <strong>{{ String(count) }}</strong>
          </article>
        </div>

        <section v-if="visibleReply !== '—'" class="captured-reply">
          <h3>捕获的可见回复</h3>
          <p>{{ visibleReply }}</p>
        </section>

        <div v-if="providerRows.length > 0" class="table-wrap">
          <table class="forensic-table">
            <thead>
              <tr>
                <th>Provider</th>
                <th>结果</th>
                <th>耗时</th>
                <th>安全回复摘要</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, idx) in providerRows" :key="`${textAt(item, 'name', 'model_used')}:${idx}`">
                <td>
                  <strong>{{ textAt(item, 'name') }}</strong><br />
                  <code>{{ textAt(item, 'model', 'model_used') }}</code>
                </td>
                <td>
                  <StateBadge :tone="item.ok === true ? 'ok' : 'error'">
                    {{ item.ok === true ? 'succeeded' : 'failed' }}
                  </StateBadge>
                </td>
                <td>{{ typeof item.duration_ms === 'number' ? `${item.duration_ms} ms` : '—' }}</td>
                <td>{{ textAt(item, 'content', 'error') }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="checkRows.length > 0" class="table-wrap">
          <table class="forensic-table">
            <thead>
              <tr>
                <th>分类</th>
                <th>检查项</th>
                <th>状态</th>
                <th>证据与建议</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, idx) in checkRows" :key="`${textAt(item, 'key')}:${idx}`">
                <td>{{ textAt(item, 'category') }}</td>
                <td>
                  <strong>{{ textAt(item, 'label') }}</strong><br />
                  <code>{{ textAt(item, 'key') }}</code>
                </td>
                <td>
                  <StateBadge :tone="item.ok === true || item.state === 'ok' ? 'ok' : item.state === 'warn' ? 'warn' : 'error'">
                    {{ textAt(item, 'state', 'status') }}
                  </StateBadge>
                </td>
                <td>
                  {{ textAt(item, 'detail') }}
                  <span v-if="textAt(item, 'hint') !== '—'" class="muted-copy"> · {{ textAt(item, 'hint') }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="evidenceRows.length > 0" class="table-wrap">
          <table class="forensic-table">
            <thead>
              <tr>
                <th>媒体工具</th>
                <th>采用状态</th>
                <th>脱敏证据摘要</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, idx) in evidenceRows" :key="`${textAt(item, 'tool')}:${idx}`">
                <td><code>{{ textAt(item, 'tool') }}</code></td>
                <td>
                  <StateBadge :tone="item.ok === true || item.state === 'ok' || item.status === 'succeeded' ? 'ok' : 'warn'">
                    {{ textAt(item, 'state', 'status') }}
                  </StateBadge>
                </td>
                <td>{{ textAt(item, 'detail') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState v-else-if="resultRecord.outbound === 'captured_not_sent'" code="video_turn_evidence_empty">
          完整回合没有关联成功的视频证据，因此不能视为通过。
        </EmptyState>

        <div v-if="resultDiag" class="diagnostic-panel" :class="{ 'diagnostic-panel-error': !resultDiag.ok }">
          <div class="diagnostic-header">
            <StateBadge :tone="resultDiag.ok ? 'ok' : 'warn'">{{ resultDiag.title }}</StateBadge>
            <code>{{ resultDiag.code }}</code>
          </div>
          <p v-if="resultDiag.message">{{ resultDiag.message }}</p>
          <p v-if="resultDiag.suggestion"><strong>建议：</strong>{{ resultDiag.suggestion }}</p>
        </div>
      </div>
    </Panel>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink } from "vue-router";
import { useMutation, useQuery } from "@tanstack/vue-query";

import { diagnosticFromError, safeDiagnostic } from "@/api/diagnostics";
import { resources } from "@/api/resources";
import type { OperationDiagnostic } from "@/api/types";
import EmptyState from "@vue-app/components/EmptyState.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import TextareaField from "@vue-app/components/forms/TextareaField.vue";

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as UnknownRecord) : {};
}

function textAt(source: UnknownRecord, ...keys: string[]): string {
  for (const key of keys) {
    const val = source[key];
    if (typeof val === "string" && val.trim() !== "") return val;
    if (typeof val === "number" || typeof val === "boolean") return String(val);
  }
  return "—";
}

function recordsAt(source: UnknownRecord, key: string): UnknownRecord[] {
  const val = source[key];
  if (!Array.isArray(val)) return [];
  return val.filter((item): item is UnknownRecord => typeof item === "object" && item !== null && !Array.isArray(item));
}

function resultDiagnostic(row: UnknownRecord): OperationDiagnostic | null {
  const nested = asRecord(row.diagnostic);
  const source = Object.keys(nested).length ? nested : row;
  if (textAt(source, "code", "diagnostic_code") === "—") return null;
  return safeDiagnostic(source as Partial<OperationDiagnostic>);
}

const prompt = ref("用一句话说明当前模型已连通。");
const selectedVideo = ref<File | null>(null);
const result = ref<unknown>(null);
const topError = ref<unknown>(null);

const topDiagnostic = computed(() => (topError.value != null ? diagnosticFromError(topError.value) : null));

const personaQuery = useQuery({
  queryKey: ["persona-prompt-preview"],
  queryFn: ({ signal }) => resources.personaPromptPreview(signal),
  enabled: false,
});

const personaData = computed(() => (personaQuery.data.value ? asRecord(personaQuery.data.value) : null));
const personaContent = computed(() => (personaData.value ? textAt(personaData.value, "content") : "—"));
const personaDiagnostic = computed(() => (personaData.value ? resultDiagnostic(personaData.value) : null));

function loadPersonaPrompt() {
  void personaQuery.refetch();
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  selectedVideo.value = target.files?.[0] ?? null;
}

const chatMutation = useMutation({
  mutationFn: async (mode: "single" | "all") => resources.modelChat(mode, prompt.value),
  onSuccess: (value) => {
    result.value = value;
    topError.value = null;
  },
  onError: (err) => {
    topError.value = err;
  },
});

function handleRunChat(mode: "single" | "all") {
  const confirmed = window.confirm(`将向${mode === "single" ? "当前模型" : "全部已配置模型"}发起真实外部调用并产生额度消耗，确认继续吗？`);
  if (!confirmed) return;
  chatMutation.mutate(mode);
}

const probeVideoMutation = useMutation({
  mutationFn: async () => {
    if (!selectedVideo.value) throw new Error("请先选择视频文件");
    return resources.videoRouteProbe(selectedVideo.value);
  },
  onSuccess: (value) => {
    result.value = value;
    topError.value = null;
  },
  onError: (err) => {
    topError.value = err;
  },
});

function handleProbeVideo() {
  if (!selectedVideo.value) return;
  const video = selectedVideo.value;
  const sizeMb = (video.size / 1024 / 1024).toFixed(1);
  const confirmed = window.confirm(`将上传 ${video.name}（${sizeMb} MB）并调用视频路由，可能产生供应商额度消耗。确认继续吗？`);
  if (!confirmed) return;
  probeVideoMutation.mutate();
}

const videoTurnMutation = useMutation({
  mutationFn: async () => {
    if (!selectedVideo.value) throw new Error("请先选择视频文件");
    return resources.videoTurnTest(selectedVideo.value, prompt.value);
  },
  onSuccess: (value) => {
    result.value = value;
    topError.value = null;
  },
  onError: (err) => {
    topError.value = err;
  },
});

function handleVideoTurn() {
  if (!selectedVideo.value) return;
  const video = selectedVideo.value;
  const confirmed = window.confirm(`将上传 ${video.name} 并进入与真实聊天一致的完整 Agent 链路。最终回复只在 WebUI 捕获，绝不发送 QQ。确认继续吗？`);
  if (!confirmed) return;
  videoTurnMutation.mutate();
}

const resultRecord = computed(() => (result.value != null ? asRecord(result.value) : null));
const resultDiag = computed(() => (resultRecord.value ? resultDiagnostic(resultRecord.value) : null));
const resultTraceId = computed(() => (resultRecord.value ? textAt(resultRecord.value, "trace_id") : "—"));
const visibleReply = computed(() => (resultRecord.value ? textAt(resultRecord.value, "reply", "content") : "—"));

const summaryEntries = computed(() => {
  if (!resultRecord.value) return [];
  const summary = asRecord(resultRecord.value.summary);
  return Object.entries(summary).filter(([, count]) => typeof count === "number");
});

const providerRows = computed(() => (resultRecord.value ? recordsAt(resultRecord.value, "results") : []));
const evidenceRows = computed(() => (resultRecord.value ? recordsAt(resultRecord.value, "media_evidence") : []));
const checkRows = computed<UnknownRecord[]>(() => {
  if (!resultRecord.value) return [];
  return recordsAt(resultRecord.value, "categories").flatMap((cat) => {
    const categoryName = textAt(cat, "name");
    return recordsAt(cat, "checks").map((chk): UnknownRecord => ({ ...chk, category: categoryName }));
  });
});
</script>
