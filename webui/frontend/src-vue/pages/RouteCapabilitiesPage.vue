<template>
  <div class="page-stack">
    <PageHeader
      index="06"
      title="路由能力"
      description="能力状态与验证状态分别呈现。超时、网络、5xx 和解析异常都保持“未知 / 结果不确定”，不会伪装成“不支持”。"
    >
      <template #actions>
        <TextField
          v-model="searchTerm"
          class="search-field"
          label="搜索路由能力"
          hide-label
          type="search"
          placeholder="搜索 Provider、模型或指纹"
          @update:model-value="onSearchInput"
        />
      </template>
    </PageHeader>

    <nav class="tabs" aria-label="路由能力导航">
      <div class="tab-list" role="tablist">
        <RouterLink
          to="/runtime/routes/capabilities"
          role="tab"
          :class="['tab-item', { active: currentSection === 'capabilities' || !currentSection }]"
          :aria-selected="currentSection === 'capabilities' || !currentSection"
        >
          能力列表
        </RouterLink>
        <RouterLink
          to="/runtime/routes/probes"
          role="tab"
          :class="['tab-item', { active: currentSection === 'probes' }]"
          :aria-selected="currentSection === 'probes'"
        >
          探针状态
        </RouterLink>
        <RouterLink
          to="/runtime/routes/video"
          role="tab"
          :class="['tab-item', { active: currentSection === 'video' }]"
          :aria-selected="currentSection === 'video'"
        >
          视频协议与证据
        </RouterLink>
      </div>
    </nav>

    <QueryBoundary :pending="isPending" :error="error">
      <template v-if="data">
        <EmptyState v-if="data.items.length === 0" code="route_capability_list_empty">
          没有匹配的路由能力记录。
        </EmptyState>
        <template v-else>
          <div class="route-dossier-list">
            <Panel
              v-for="route in data.items"
              :key="route.route_fingerprint"
              as="article"
              class="route-dossier"
              :eyebrow="`${route.provider} / ${route.api_type}`"
              :title="route.model"
            >
              <div class="route-meta-line">
                <code :title="route.route_fingerprint">{{ shortId(route.route_fingerprint, 10) }}</code>
                <span>{{ route.media_protocol || '未声明媒体协议' }}</span>
                <StateBadge
                  :tone="route.probe_status === 'running' || route.probe_status === 'queued' ? 'running' : 'unknown'"
                  :raw="route.probe_status"
                >
                  {{ routeProbeStatusLabel(route.probe_status) }}
                </StateBadge>
              </div>

              <div class="capability-grid">
                <div
                  v-for="(cap, name) in route.capabilities"
                  :key="name"
                  class="capability-cell"
                >
                  <StateBadge
                    :tone="capabilityTone(cap.state, cap.verification_state)"
                    :raw="`${capabilityStateLabel(cap.state)} · ${verificationStateLabel(cap.verification_state)} · ${cap.detail_code}`"
                  >
                    {{ CAPABILITY_LABELS[name as CapabilityName] || name }}: {{ capabilityStateLabel(cap.state) }}
                  </StateBadge>
                  <StateBadge
                    :tone="verificationTone(cap.state, cap.verification_state)"
                    :raw="cap.verification_state"
                  >
                    {{ verificationStateLabel(cap.verification_state) }}
                  </StateBadge>
                  <dl>
                    <div><dt>证据</dt><dd>{{ capabilitySourceLabel(cap.source) }}</dd></div>
                    <div><dt>验证</dt><dd>{{ formatDateTime(cap.checked_at) }}</dd></div>
                    <div><dt>探针</dt><dd>{{ probeAvailabilityLabel(probeFor(route, name as CapabilityName)) }}</dd></div>
                    <div><dt>风险</dt><dd>{{ probeRiskLabel(probeFor(route, name as CapabilityName)) }}</dd></div>
                    <div><dt>要求</dt><dd>{{ probeRequirementLabel(probeFor(route, name as CapabilityName)) }}</dd></div>
                    <div><dt>诊断</dt><dd><code>{{ cap.detail_code }}</code></dd></div>
                  </dl>
                  <div v-if="isMediaProbe(probeFor(route, name as CapabilityName))" class="probe-media-input">
                    <fieldset class="media-mode-switch">
                      <legend>样例模式</legend>
                      <label>
                        <input
                          type="radio"
                          :name="mediaInputId(route.route_fingerprint, name as CapabilityName) + '-mode'"
                          :checked="sampleModeFor(route.route_fingerprint, name as CapabilityName) === 'builtin'"
                          @change="setSampleMode(route.route_fingerprint, name as CapabilityName, 'builtin')"
                        />
                        内置确定性样例（默认）
                      </label>
                      <label>
                        <input
                          type="radio"
                          :name="mediaInputId(route.route_fingerprint, name as CapabilityName) + '-mode'"
                          :checked="sampleModeFor(route.route_fingerprint, name as CapabilityName) === 'upload'"
                          @change="setSampleMode(route.route_fingerprint, name as CapabilityName, 'upload')"
                        />
                        自定义上传
                      </label>
                    </fieldset>
                    <template v-if="sampleModeFor(route.route_fingerprint, name as CapabilityName) === 'upload'">
                      <label :for="mediaInputId(route.route_fingerprint, name as CapabilityName)">
                        管理员受限{{ CAPABILITY_LABELS[name as CapabilityName] }}样例
                      </label>
                      <input
                        :id="mediaInputId(route.route_fingerprint, name as CapabilityName)"
                        data-testid="route-media-probe-input"
                        type="file"
                        :accept="mediaAccept(probeFor(route, name as CapabilityName))"
                        @change="selectMediaSample(route.route_fingerprint, name as CapabilityName, $event)"
                      />
                      <small>{{ mediaSelectionLabel(route.route_fingerprint, name as CapabilityName, probeFor(route, name as CapabilityName)) }}</small>
                    </template>
                    <small v-else>
                      {{ probeFor(route, name as CapabilityName)?.builtin_sample?.description || '由服务器校验摘要后发送，不向浏览器暴露答案或路径。' }}
                    </small>
                    <small v-if="route.probe_results?.[name as CapabilityName]">
                      最近结果：{{ route.probe_results[name as CapabilityName]?.content_verified ? '内容理解已验证' : route.probe_results[name as CapabilityName]?.transport_verified ? '仅传输/解码链可用' : '结果不确定' }}
                    </small>
                  </div>
                  <button
                    class="button button-secondary"
                    type="button"
                    :disabled="!canRunProbe(route, name as CapabilityName) || !hasRequiredMediaSample(route.route_fingerprint, name as CapabilityName, probeFor(route, name as CapabilityName)) || isProbing(route.route_fingerprint, name as CapabilityName) || isPendingProbe || isPendingMediaProbe"
                    :title="probeActionHint(probeFor(route, name as CapabilityName))"
                    @click="triggerProbe(route.route_fingerprint, name as CapabilityName)"
                  >
                    <Icon name="refresh" />
                    {{ isProbing(route.route_fingerprint, name as CapabilityName) ? '正在排队' : probeButtonLabel(probeFor(route, name as CapabilityName)) }}
                  </button>
                </div>
              </div>

              <footer class="route-summary">
                <span>已验证支持 {{ countVerifiedCapabilities(route.capabilities, 'supported') }}</span>
                <span>待核实 {{ countUnverifiedCapabilities(route.capabilities) }}</span>
                <span>已验证不支持 {{ countVerifiedCapabilities(route.capabilities, 'unsupported') }}</span>
              </footer>
            </Panel>
          </div>

          <nav v-if="data.total_pages > 1" class="pagination" aria-label="分页导航">
            <button
              type="button"
              :disabled="page <= 1"
              aria-label="上一页"
              @click="page = Math.max(1, page - 1)"
            >
              ‹
            </button>
            <span>第 {{ data.page }} / {{ data.total_pages }} 页</span>
            <button
              type="button"
              :disabled="page >= data.total_pages"
              aria-label="下一页"
              @click="page = Math.min(data.total_pages, page + 1)"
            >
              ›
            </button>
          </nav>
        </template>
      </template>
    </QueryBoundary>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";

import { resources } from "@/api/resources";
import type {
  CapabilityName,
  CapabilityState,
  RouteCapabilities,
  RouteCapabilityItem,
  RouteCapabilityProbe,
  VerificationState,
} from "@/api/types";
import { formatDateTime, shortId } from "@/lib/format";
import { capabilitySourceLabel, capabilityStateLabel, verificationStateLabel } from "@/lib/labels";
import EmptyState from "@vue-app/components/EmptyState.vue";
import Icon from "@vue-app/components/Icon.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import TextField from "@vue-app/components/forms/TextField.vue";

const CAPABILITY_LABELS: Record<CapabilityName, string> = {
  image_input: "图片",
  audio_input: "音频",
  video_input: "视频",
  reasoning: "推理",
  function_call: "函数",
  native_web_search: "原生搜索",
  external_network_access: "Agent 外网",
};

const route = useRoute();
const queryClient = useQueryClient();
const currentSection = computed(() => String(route.params.section || "capabilities"));

const page = ref(1);
const searchTerm = ref("");
const probingMap = reactive<Record<string, boolean>>({});
const selectedMedia = reactive<Record<string, File | undefined>>({});
const selectedSampleModes = reactive<Record<string, "builtin" | "upload">>({});

type MediaCapability = Extract<CapabilityName, "audio_input" | "video_input">;

const { data, isPending, error } = useQuery({
  queryKey: computed(() => ["route-capabilities", page.value, searchTerm.value]),
  queryFn: ({ signal }) => resources.routes(page.value, 20, searchTerm.value, signal),
});

const { mutate: mutateProbe, isPending: isPendingProbe } = useMutation({
  mutationFn: ({ fingerprint, capability }: { fingerprint: string; capability: CapabilityName }) => {
    const probe = probeForFingerprint(fingerprint, capability);
    return isMediaProbe(probe)
      ? resources.queueRouteProbe(
          fingerprint,
          capability,
          true,
          sampleModeFor(fingerprint, capability),
          probe?.default_sample_id || "",
        )
      : resources.queueRouteProbe(fingerprint, capability, true);
  },
  onSuccess: (_, request) => {
    probingMap[probeMapKey(request.fingerprint, request.capability)] = false;
    void queryClient.invalidateQueries({ queryKey: ["route-capabilities"] });
  },
  onError: (_, request) => {
    probingMap[probeMapKey(request.fingerprint, request.capability)] = false;
  },
});

const { mutate: mutateMediaProbe, isPending: isPendingMediaProbe } = useMutation({
  mutationFn: ({ fingerprint, capability, file }: { fingerprint: string; capability: MediaCapability; file: File }) =>
    resources.uploadRouteMediaProbe(fingerprint, capability, file),
  onSuccess: (_, request) => {
    probingMap[probeMapKey(request.fingerprint, request.capability)] = false;
    selectedMedia[probeMapKey(request.fingerprint, request.capability)] = undefined;
    void queryClient.invalidateQueries({ queryKey: ["route-capabilities"] });
  },
  onError: (_, request) => {
    probingMap[probeMapKey(request.fingerprint, request.capability)] = false;
  },
});

function probeMapKey(fingerprint: string, capability: CapabilityName): string {
  return `${fingerprint}:${capability}`;
}

function probeFor(routeItem: RouteCapabilityItem, capability: CapabilityName): RouteCapabilityProbe | undefined {
  return routeItem.probe_catalog?.[capability];
}

function canRunProbe(routeItem: RouteCapabilityItem, capability: CapabilityName): boolean {
  return probeFor(routeItem, capability)?.available === true;
}

function isMediaProbe(probe: RouteCapabilityProbe | undefined): boolean {
  return probe?.input_kind === "media" || probe?.input_kind === "media_upload";
}

function probeForFingerprint(fingerprint: string, capability: CapabilityName): RouteCapabilityProbe | undefined {
  const item = data.value?.items.find((routeItem) => routeItem.route_fingerprint === fingerprint);
  return item ? probeFor(item, capability) : undefined;
}

function sampleModeFor(fingerprint: string, capability: CapabilityName): "builtin" | "upload" {
  const probe = probeForFingerprint(fingerprint, capability);
  const modes = Array.isArray(probe?.sample_modes) ? probe.sample_modes : [];
  if (!modes.includes("builtin")) return "upload";
  return selectedSampleModes[probeMapKey(fingerprint, capability)] || "builtin";
}

function setSampleMode(fingerprint: string, capability: CapabilityName, mode: "builtin" | "upload") {
  selectedSampleModes[probeMapKey(fingerprint, capability)] = mode;
}

function isMediaCapability(capability: CapabilityName): capability is MediaCapability {
  return capability === "audio_input" || capability === "video_input";
}

function mediaInputId(fingerprint: string, capability: CapabilityName): string {
  return `route-media-${probeMapKey(fingerprint, capability).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function mediaAccept(probe: RouteCapabilityProbe | undefined): string {
  return Array.isArray(probe?.accepted_mime_types) ? probe.accepted_mime_types.join(",") : "";
}

function selectMediaSample(fingerprint: string, capability: CapabilityName, event: Event) {
  const input = event.target as HTMLInputElement | null;
  selectedMedia[probeMapKey(fingerprint, capability)] = input?.files?.item(0) ?? undefined;
}

function hasRequiredMediaSample(
  fingerprint: string,
  capability: CapabilityName,
  probe: RouteCapabilityProbe | undefined,
): boolean {
  return !isMediaProbe(probe)
    || sampleModeFor(fingerprint, capability) === "builtin"
    || selectedMedia[probeMapKey(fingerprint, capability)] instanceof File;
}

function mediaSelectionLabel(
  fingerprint: string,
  capability: CapabilityName,
  probe: RouteCapabilityProbe | undefined,
): string {
  const file = selectedMedia[probeMapKey(fingerprint, capability)];
  if (!(file instanceof File)) {
    const limit = Number(probe?.max_upload_bytes || 0);
    return limit > 0 ? `请选择不超过 ${Math.ceil(limit / (1024 * 1024))} MB 的样例；上传后会自动删除。` : "请选择一个受限样例；上传后会自动删除。";
  }
  return `已选择受限样例（${Math.max(1, Math.ceil(file.size / 1024))} KiB）；不会保存到能力快照。`;
}

function isProbing(fingerprint: string, capability: CapabilityName): boolean {
  return probingMap[probeMapKey(fingerprint, capability)] === true;
}

function triggerProbe(fingerprint: string, capability: CapabilityName) {
  const routeItem = data.value?.items.find((item) => item.route_fingerprint === fingerprint);
  const probe = routeItem ? probeFor(routeItem, capability) : undefined;
  if (!probe?.available) return;

  if (isMediaProbe(probe) && sampleModeFor(fingerprint, capability) === "upload") {
    const file = selectedMedia[probeMapKey(fingerprint, capability)];
    if (!(file instanceof File) || !isMediaCapability(capability)) return;
    if (Array.isArray(probe.accepted_mime_types) && !probe.accepted_mime_types.includes(file.type)) {
      window.alert("所选样例的 MIME 类型不在服务端允许列表中；未上传，也未调用 Provider。");
      return;
    }
    if (typeof probe.max_upload_bytes === "number" && file.size > probe.max_upload_bytes) {
      window.alert("所选样例超过服务端允许大小；未上传，也未调用 Provider。");
      return;
    }
    const detail = `${CAPABILITY_LABELS[capability]}探针会把当前受限样例发送给所选 Provider，可能产生网络或 Token 额度消耗。样例仅用于本次验证，结束后自动删除；不会发送 QQ，也不会保存媒体内容。\n\n确认上传并运行吗？`;
    if (!window.confirm(detail)) return;
    probingMap[probeMapKey(fingerprint, capability)] = true;
    mutateMediaProbe({ fingerprint, capability, file });
    return;
  }

  if (isMediaProbe(probe)) {
    const detail = `${CAPABILITY_LABELS[capability]}探针会使用服务器内置的确定性样例调用当前固定路由，可能产生额度消耗。正确答案仅在服务端评分；不会回退路由、发送 QQ、保存模型原文或返回媒体路径。\n\n确认运行吗？`;
    if (!window.confirm(detail)) return;
  }

  if (probe.confirmation_required && !isMediaProbe(probe)) {
    const detail = `${CAPABILITY_LABELS[capability]}探针会调用当前 Provider，可能产生网络或 Token 额度消耗。它不会发送 QQ，也不会执行模型返回的工具调用；推理探针不会请求或展示思维链。\n\n确认运行吗？`;
    if (!window.confirm(detail)) return;
  }
  probingMap[probeMapKey(fingerprint, capability)] = true;
  mutateProbe({ fingerprint, capability });
}

function onSearchInput() {
  page.value = 1;
}

function capabilityTone(state: CapabilityState, verificationState: VerificationState): "ok" | "error" | "unknown" {
  if (verificationState !== "verified") return "unknown";
  if (state === "supported") return "ok";
  if (state === "unsupported") return "error";
  return "unknown";
}

function verificationTone(capabilityState: CapabilityState, state: VerificationState): "ok" | "unknown" {
  return capabilityState !== "unknown" && state === "verified" ? "ok" : "unknown";
}

function probeAvailabilityLabel(probe: RouteCapabilityProbe | undefined): string {
  if (!probe) return "目录缺失";
  if (!probe.available) return "当前不可用";
  return isMediaProbe(probe) ? "内置样例可直接运行" : "可运行";
}

function probeRiskLabel(probe: RouteCapabilityProbe | undefined): string {
  if (!probe) return "未声明";
  if (probe.risk === "external_write") return "外部写入";
  if (probe.risk === "external_read") return "Provider 外部读取";
  return "本地只读";
}

function probeRequirementLabel(probe: RouteCapabilityProbe | undefined): string {
  if (!probe) return "无";
  if (!probe.available) {
    if (!probe.confirmation_required) return "当前无安全探针";
    return "不可用；启用时需确认";
  }
  if (isMediaProbe(probe)) {
    const limit = Number(probe.max_upload_bytes || 0);
    return limit > 0 ? `默认内置样例；自定义上传上限 ${Math.ceil(limit / (1024 * 1024))} MB` : "默认内置样例";
  }
  return probe.confirmation_required ? "需确认（网络/Token 消耗）" : "无需确认";
}

function probeActionHint(probe: RouteCapabilityProbe | undefined): string {
  if (!probe) return "服务端没有提供此能力的探针目录。";
  if (!probe.available) return `当前不可运行：${probe.reason_code}`;
  if (isMediaProbe(probe)) return "默认使用服务器内置样例验证内容；自定义模式只验证传输/解码链。";
  return probe.confirmation_required ? "需明确确认，可能消耗网络或 Token 额度。" : "可运行无副作用探针。";
}

function probeButtonLabel(probe: RouteCapabilityProbe | undefined): string {
  if (!probe?.available) return "探针不可用";
  return isMediaProbe(probe) ? "确认并运行媒体探针" : "运行探针";
}

function routeProbeStatusLabel(status: RouteCapabilityItem["probe_status"]): string {
  if (status === "running") return "探针运行中";
  if (status === "queued") return "探针已排队";
  if (status === "failed") return "最近探针失败";
  if (status === "finished") return "探针已完成";
  return "暂无运行中的探针";
}

function countVerifiedCapabilities(capabilities: RouteCapabilities, state: CapabilityState): number {
  return Object.values(capabilities).filter(
    (capability) => capability.state === state && capability.verification_state === "verified",
  ).length;
}

function countUnverifiedCapabilities(capabilities: RouteCapabilities): number {
  return Object.values(capabilities).filter((capability) => capability.verification_state !== "verified").length;
}
</script>
