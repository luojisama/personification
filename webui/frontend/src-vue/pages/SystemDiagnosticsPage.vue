<template>
  <div class="page-stack">
    <PageHeader
      index="32"
      title="系统诊断"
      description="只读展示多模态路由、QZone 分项能力、渐进工具、参与策略、情绪 v2 与完整备份边界。存在本地代码不等于生产可用。"
    />

    <nav class="tabs" aria-label="系统诊断导航">
      <div class="tab-list" role="tablist">
        <RouterLink
          to="/operations/systems/multimodal"
          role="tab"
          :class="['tab-item', { active: currentSection === 'multimodal' || !currentSection }]"
          :aria-selected="currentSection === 'multimodal' || !currentSection"
        >
          多模态与依赖
        </RouterLink>
        <RouterLink
          to="/operations/systems/indexes"
          role="tab"
          :class="['tab-item', { active: currentSection === 'indexes' }]"
          :aria-selected="currentSection === 'indexes'"
        >
          索引与 QZone 能力
        </RouterLink>
        <RouterLink
          to="/operations/systems/realtime"
          role="tab"
          :class="['tab-item', { active: currentSection === 'realtime' }]"
          :aria-selected="currentSection === 'realtime'"
        >
          开关与迁移边界
        </RouterLink>
      </div>
    </nav>

    <div class="systems-grid">
      <Panel eyebrow="MEDIA / ROUTES" title="音频与视频路由">
        <QueryBoundary :pending="multimodalQuery.isPending.value" :error="multimodalQuery.error.value">
          <div v-if="multimodalQuery.data.value" class="systems-ledger">
            <section>
              <h3>音频</h3>
              <p>
                <StateBadge :tone="multimodalQuery.data.value.audio.route_available ? 'ok' : 'unknown'">
                  {{ multimodalQuery.data.value.audio.route_available ? '存在可用路线' : '未确认可用路线' }}
                </StateBadge>
              </p>
              <dl>
                <div><dt>主模型原生</dt><dd>{{ String(multimodalQuery.data.value.audio.primary_native) }}</dd></div>
                <div><dt>ASR Provider</dt><dd><code>{{ multimodalQuery.data.value.audio.asr_provider }}</code></dd></div>
                <div><dt>回退顺序</dt><dd>{{ multimodalQuery.data.value.audio.fallback_order.join(" → ") || "无" }}</dd></div>
              </dl>
            </section>

            <section>
              <h3>视频</h3>
              <p>
                <StateBadge :tone="multimodalQuery.data.value.video.enabled ? 'ok' : 'unknown'">
                  {{ multimodalQuery.data.value.video.enabled ? '视频理解已启用' : '视频理解配置关闭' }}
                </StateBadge>
              </p>
              <dl>
                <div><dt>路线模式</dt><dd><code>{{ multimodalQuery.data.value.video.route_mode }}</code></dd></div>
                <div><dt>主模型原生</dt><dd>{{ String(multimodalQuery.data.value.video.primary_native) }}</dd></div>
                <div><dt>回退顺序</dt><dd>{{ multimodalQuery.data.value.video.fallback_order.join(" → ") || "无" }}</dd></div>
              </dl>
            </section>

            <section>
              <h3>服务器媒体依赖</h3>
              <p>
                <StateBadge :tone="multimodalQuery.data.value.dependencies.ffmpeg.available && multimodalQuery.data.value.dependencies.ffprobe.available ? 'ok' : 'unknown'">
                  {{ multimodalQuery.data.value.dependencies.ffmpeg.available && multimodalQuery.data.value.dependencies.ffprobe.available ? 'ffmpeg / ffprobe 已就绪' : '媒体依赖未就绪' }}
                </StateBadge>
              </p>
              <dl>
                <div><dt>ffmpeg</dt><dd><code>{{ multimodalQuery.data.value.dependencies.ffmpeg.version || multimodalQuery.data.value.dependencies.ffmpeg.diagnostic_code }}</code></dd></div>
                <div><dt>ffprobe</dt><dd><code>{{ multimodalQuery.data.value.dependencies.ffprobe.version || multimodalQuery.data.value.dependencies.ffprobe.diagnostic_code }}</code></dd></div>
              </dl>
            </section>

            <div class="unknown-warning">
              {{ multimodalQuery.data.value.production_verified ? '已完成生产验证' : '仅本地路线快照；真实 QQ、Gemini 与生产部署尚需管理员联调。' }}
              <code>{{ multimodalQuery.data.value.diagnostic_code }}</code>
            </div>
          </div>
        </QueryBoundary>
      </Panel>

      <Panel eyebrow="QZONE / MATRIX" title="QZone 分项能力">
        <QueryBoundary :pending="qzoneQuery.isPending.value" :error="qzoneQuery.error.value">
          <div v-if="qzoneQuery.data.value" class="capability-object">
            <div v-for="(item, key) in qzonePrimitiveEntries" :key="key">
              <dt>{{ key }}</dt>
              <dd><code>{{ String(item ?? '') }}</code></dd>
            </div>
            <div v-for="(item, key) in qzoneNestedEntries" :key="key">
              <dt>{{ key }}</dt>
              <dd>
                <StateBadge :tone="qzoneStateTone(item.state || item.status)">
                  {{ String(item.state || item.status || 'unknown') }}
                </StateBadge>
                <code>{{ String(item.detail_code || item.diagnostic_code || '') }}</code>
              </dd>
            </div>
          </div>
        </QueryBoundary>
      </Panel>

      <Panel class="wide-panel" eyebrow="ROLL-OUT / SAFETY" title="功能开关与迁移边界">
        <QueryBoundary :pending="settingsQuery.isPending.value" :error="settingsQuery.error.value">
          <div v-if="settingsQuery.data.value" class="rollout-ledger">
            <div>
              <span>参与概率 v2</span>
              <StateBadge :tone="settingsQuery.data.value.participation_v2_mode === 'on' ? 'ok' : 'running'">
                {{ String(settingsQuery.data.value.participation_v2_mode ?? 'unknown') }}
              </StateBadge>
              <small>shadow 只记录新旧差异，不改变实际行为。</small>
            </div>
            <div>
              <span>渐进式工具</span>
              <StateBadge :tone="settingsQuery.data.value.tool_disclosure_mode === 'off' ? 'unknown' : 'ok'">
                {{ String(settingsQuery.data.value.tool_disclosure_mode ?? 'unknown') }}
              </StateBadge>
              <small>副作用仍绑定原工具；发现过程不执行工具。</small>
            </div>
            <div>
              <span>情绪状态 v2</span>
              <StateBadge :tone="settingsQuery.data.value.emotion_v2_mode === 'on' ? 'ok' : 'running'">
                {{ String(settingsQuery.data.value.emotion_v2_mode ?? 'unknown') }}
              </StateBadge>
              <small>shadow 写入兼容状态但不影响提示。</small>
            </div>
            <div>
              <span>完整备份</span>
              <StateBadge tone="warn">step-up</StateBadge>
              <small>状态包与 AES-256-GCM 秘密包分离；恢复后端不可用时拒绝执行。</small>
            </div>
          </div>
        </QueryBoundary>
      </Panel>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useQuery } from "@tanstack/vue-query";

import { resources } from "@/api/resources";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";

const route = useRoute();
const currentSection = computed(() => String(route.params.section || "multimodal"));

const multimodalQuery = useQuery({
  queryKey: ["multimodal-routes"],
  queryFn: ({ signal }) => resources.multimodalRoutes(signal),
});

const qzoneQuery = useQuery({
  queryKey: ["qzone-capabilities"],
  queryFn: ({ signal }) => resources.qzoneCapabilities(signal),
});

const settingsQuery = useQuery({
  queryKey: ["settings"],
  queryFn: ({ signal }) => resources.runtimeSettings(signal),
});

const qzonePrimitiveEntries = computed(() => {
  const data = qzoneQuery.data.value || {};
  return Object.fromEntries(
    Object.entries(data).filter(([, val]) => typeof val !== "object" || val === null).slice(0, 24),
  );
});

const qzoneNestedEntries = computed(() => {
  const data = qzoneQuery.data.value || {};
  return Object.fromEntries(
    Object.entries(data)
      .filter(([, val]) => typeof val === "object" && val !== null && !Array.isArray(val))
      .map(([k, v]) => [k, v as Record<string, unknown>])
      .slice(0, 16),
  );
});

function qzoneStateTone(stateVal: unknown): "ok" | "error" | "unknown" {
  const state = String(stateVal ?? "unknown");
  if (state === "supported" || state === "ok") return "ok";
  if (state === "unsupported" || state === "failed") return "error";
  return "unknown";
}
</script>
