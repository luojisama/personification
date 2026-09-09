<template>
  <div :class="['page-stack', { 'persona-feature-page--stickers': pageMode === 'stickers' }]">
    <!-- 1. 表情包管理 -->
    <template v-if="pageMode === 'stickers'">
      <PageHeader
        index="16"
        title="表情包管理"
        description="表情包目录检索、重扫、索引重建、上传与元数据标签编辑，支持一键安全同步。"
      >
        <template #actions>
          <div class="inline-controls">
            <button
              type="button"
              class="button button-secondary"
              :disabled="rescanMutation.isPending.value"
              @click="handleRescan"
            >
              {{ rescanMutation.isPending.value ? "正在重扫…" : "重扫目录" }}
            </button>
            <button
              type="button"
              class="button button-secondary"
              :disabled="rebuildMutation.isPending.value"
              @click="handleRebuildIndex"
            >
              {{ rebuildMutation.isPending.value ? "正在重建…" : "重建索引" }}
            </button>
          </div>
        </template>
      </PageHeader>

      <!-- 索引状态与上传面板 -->
      <Panel eyebrow="PERSONA / STICKER SYNC" title="表情包上传与索引状态">
        <div class="sticker-header-ledger">
          <div class="metric-ribbon">
            <article>
              <small>索引状态</small>
              <strong>
                <StateBadge :tone="stickerIndexTone">
                  {{ stickerIndexLabel }}
                </StateBadge>
              </strong>
            </article>
            <article>
              <small>总表情数</small>
              <strong>{{ stickersQuery.data.value?.total ?? 0 }}</strong>
            </article>
            <article>
              <small>索引刷新时间</small>
              <span>{{ formatDateTime(stickersQuery.data.value?.index_updated_at) }}</span>
            </article>
          </div>

          <form class="sticker-upload-form" @submit.prevent="handleUpload">
            <div class="inline-controls sticker-upload-controls">
              <input
                ref="fileInputRef"
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp"
                aria-label="选择表情包文件"
                @change="onFileSelected"
              />
              <TextField
                v-model="uploadDescription"
                label="表情包描述"
                hide-label
                placeholder="表情包描述（可选）"
              />
              <button
                type="submit"
                class="button button-primary"
                :disabled="!selectedUploadFile || uploadMutation.isPending.value"
              >
                {{ uploadMutation.isPending.value ? "上传中…" : "上传表情" }}
              </button>
            </div>
            <p v-if="uploadError" class="field-error-msg" role="alert">{{ uploadError }}</p>
          </form>
        </div>
      </Panel>

      <Panel eyebrow="PERSONA / VISUAL RELABEL" title="低置信视觉重标">
        <p class="field-hint">只处理标记为需要视觉复核的素材；暂停会在当前有界批次结束后生效，重启后会从持久作业继续。</p>
        <div class="inline-controls">
          <TextField v-model="visualRelabelBatch" label="每批数量（1-20）" inputmode="numeric" />
          <button type="button" class="button button-secondary" :disabled="visualRelabelStart.isPending.value" @click="startVisualRelabel">开始重标</button>
          <button v-if="visualRelabelJobId" type="button" class="button button-secondary" @click="pauseVisualRelabel">暂停</button>
          <button v-if="visualRelabelJobId" type="button" class="button button-primary" @click="resumeVisualRelabel">继续</button>
        </div>
        <dl v-if="visualRelabelQuery.data.value" class="count-ledger"><div><dt>状态</dt><dd>{{ textAt(visualRelabelQuery.data.value, 'status') }}</dd></div><div><dt>已处理</dt><dd>{{ textAt(visualRelabelQuery.data.value, 'processed') }}</dd></div><div><dt>剩余</dt><dd>{{ textAt(visualRelabelQuery.data.value, 'remaining') }}</dd></div><div><dt>失败数</dt><dd>{{ textAt(visualRelabelQuery.data.value, 'failed') }}</dd></div><div><dt>失败类型</dt><dd>{{ textAt(visualRelabelQuery.data.value, 'last_error') }}</dd></div></dl>
      </Panel>

      <!-- 表情包目录检索与展示 -->
      <Panel eyebrow="PERSONA / STICKER CATALOG" title="表情包目录">
        <template #actions>
          <TextField
            v-model="stickerSearch"
            class="search-field"
            label="搜索表情包"
            hide-label
            type="search"
            placeholder="搜索表情包文件名或描述…"
          />
        </template>

        <QueryBoundary
          :pending="stickersQuery.isPending.value"
          :error="stickersQuery.error.value"
          :empty="!stickersQuery.data.value?.items?.length"
          empty-text="暂无匹配的表情包。"
        >
          <div class="sticker-card-grid">
            <article
              v-for="item in stickersQuery.data.value?.items ?? []"
              :key="item.filename"
              class="sticker-card"
            >
              <div class="sticker-thumb-box">
                <img
                  v-if="item.thumbnail_url"
                  :src="item.thumbnail_url"
                  :alt="item.description || item.filename"
                  loading="lazy"
                />
                <div v-else class="sticker-thumb-fallback">IMG</div>
              </div>
              <div class="sticker-card-body">
                <div class="sticker-card-head">
                  <strong class="sticker-title" :title="item.filename">{{ item.filename }}</strong>
                  <StateBadge :tone="item.labeled ? 'ok' : 'warn'">
                    {{ item.labeled ? "已标注" : "未标注" }}
                  </StateBadge>
                </div>
                <p class="sticker-desc">{{ item.description || "暂无描述" }}</p>
                <div class="sticker-tags">
                  <span v-for="tag in item.mood_tags" :key="tag" class="tag-pill tag-mood">{{ tag }}</span>
                  <span v-for="tag in item.scene_tags" :key="tag" class="tag-pill tag-scene">{{ tag }}</span>
                </div>
                <div class="sticker-meta-line">
                  <small>{{ formatInteger(Math.round(item.size_bytes / 1024)) }} KB</small>
                  <small>{{ formatDateTime(item.modified_at) }}</small>
                </div>
                <div class="sticker-card-actions">
                  <button
                    type="button"
                    class="button button-secondary button-xs"
                    @click="startEditingSticker(item)"
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    class="button button-danger button-xs"
                    :disabled="deleteMutation.isPending.value"
                    @click="handleDeleteSticker(item.filename)"
                  >
                    删除
                  </button>
                </div>
              </div>
            </article>
          </div>

          <Pagination :page="stickerPage" :total-pages="stickersQuery.data.value?.total_pages || 1" :total="stickersQuery.data.value?.total || 0" :disabled="stickersQuery.isFetching.value" @update:page="stickerPage = $event" />
        </QueryBoundary>
      </Panel>

      <!-- 编辑表情包元数据对话抽屉/面板 -->
      <Panel v-if="editingSticker" eyebrow="PERSONA / EDIT METADATA" :title="`编辑表情: ${editingSticker.filename}`">
        <form class="sticker-edit-form" @submit.prevent="handleSaveStickerEdit">
          <TextField id="edit-desc" v-model="editForm.description" class="stacked-field" label="描述文本" />
          <TextField id="edit-mood" v-model="editForm.mood_tags" class="stacked-field" label="情绪标签（英文逗号分隔）" placeholder="happy, excited" />
          <TextField id="edit-scene" v-model="editForm.scene_tags" class="stacked-field" label="场景标签（英文逗号分隔）" placeholder="greeting, victory" />
          <div class="inline-controls">
            <button type="submit" class="button button-primary" :disabled="updateMutation.isPending.value">
              {{ updateMutation.isPending.value ? "保存中…" : "保存修改" }}
            </button>
            <button type="button" class="button button-secondary" @click="editingSticker = null">取消</button>
          </div>
        </form>
      </Panel>
    </template>

    <!-- 2. 人设预览 -->
    <template v-else-if="pageMode === 'preview'">
      <PageHeader
        index="17"
        title="人设预览"
        description="展示实际可见 Prompt、安全上下文、来源与质量告警；隐藏思维链和未清洗工具上下文不会出现在这里。"
      >
        <template #actions>
          <div class="segmented-control" role="tablist">
            <button
              type="button"
              role="tab"
              :aria-selected="currentSection !== 'warnings'"
              @click="setSection('prompt')"
            >
              实际 Prompt
            </button>
            <button
              type="button"
              role="tab"
              :aria-selected="currentSection === 'warnings'"
              @click="setSection('warnings')"
            >
              质量告警与来源
            </button>
          </div>
        </template>
      </PageHeader>

      <QueryBoundary :pending="previewQuery.isPending.value" :error="previewQuery.error.value">
        <template v-if="currentSection === 'warnings'">
          <Panel eyebrow="PERSONA / QUALITY & SOURCES" title="质量告警与来源清单">
            <div v-if="previewWarnings.length === 0 && previewSources.length === 0" class="query-empty">
              当前没有质量告警及附加来源记录。
            </div>
            <table v-else class="business-table">
              <thead>
                <tr>
                  <th>诊断码 / 来源</th>
                  <th>说明</th>
                  <th>级别 / 状态</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, idx) in (previewWarnings.length ? previewWarnings : previewSources)"
                  :key="String(row.code || row.path || row.source || idx)"
                >
                  <td><code>{{ row.code || row.path || row.source || row.name || "—" }}</code></td>
                  <td>{{ row.message || row.summary || row.description || "—" }}</td>
                  <td>
                    <StateBadge :tone="row.level === 'error' ? 'error' : row.level === 'warn' ? 'warn' : 'ok'">
                      {{ String(row.level || row.status || row.state || "info") }}
                    </StateBadge>
                  </td>
                </tr>
              </tbody>
            </table>
          </Panel>
        </template>

        <template v-else>
          <Panel eyebrow="PERSONA / EFFECTIVE PROMPT" title="有效 System Prompt 预览">
            <div v-if="!effectivePrompt" class="query-empty">
              服务端没有返回可见 Prompt 预览。
            </div>
            <div v-else class="prompt-container">
              <pre class="prompt-preview safe-prompt-preview">{{ effectivePrompt }}</pre>
            </div>
          </Panel>
        </template>
      </QueryBoundary>
    </template>

    <!-- 3. 人设构建 -->
    <template v-else-if="pageMode === 'builder'">
      <PageHeader
        index="18"
        title="人设构建"
        description="构建任务、候选、历史和模板操作保持 revision 与服务端结构化校验，不再展示接口字段转储。"
      />

      <Panel eyebrow="PERSONA / DYNAMIC PROFILE" title="动态画像与检索状态">
        <QueryBoundary :pending="profileJobsQuery.isPending.value" :error="profileJobsQuery.error.value">
          <dl v-if="profileJobsQuery.data.value" class="count-ledger">
            <div><dt>批处理阈值</dt><dd>{{ textAt(profileJobsQuery.data.value, 'auto_threshold') }} 条有效消息</dd></div>
            <div><dt>静默等待</dt><dd>{{ textAt(profileJobsQuery.data.value, 'quiet_period_seconds') }} 秒</dd></div>
            <div><dt>scope 冷却</dt><dd>{{ textAt(profileJobsQuery.data.value, 'scope_cooldown_seconds') }} 秒</dd></div>
            <div><dt>今日 API</dt><dd>{{ textAt(profileJobsQuery.data.value, 'daily_api_calls') }} / {{ textAt(profileJobsQuery.data.value, 'daily_api_budget') }}</dd></div>
            <div><dt>待处理范围</dt><dd>{{ textAt(profileJobsQuery.data.value, 'persisted_scope_count') }}</dd></div>
            <div><dt>隔离键</dt><dd><code>{{ textAt(profileJobsQuery.data.value, 'scope_key') }}</code></dd></div>
          </dl>
          <p class="field-hint">{{ textAt(profileJobsQuery.data.value, 'scope_isolation_note') || '调度状态暂不可用。' }}</p>
        </QueryBoundary>
      </Panel>

      <Panel eyebrow="PROFILE / SCOPED V3" title="场景画像与共享许可">
        <p class="field-hint">私聊画像默认仅限私聊；群聊画像仅限当前群。开启某条偏好后，其他场景只会读取这条稳定偏好，不会共享聊天原文、事件摘要或当前情绪。</p>
        <form class="builder-task-form" @submit.prevent="loadScopedProfileDocument">
          <div class="inline-controls filter-control-row">
            <TextField v-model="scopedProfileScope.platform" label="平台" placeholder="onebot" />
            <TextField v-model="scopedProfileScope.bot_id" label="Bot ID" placeholder="Bot QQ / 身份" />
            <TextField v-model="scopedProfileScope.user_id" label="用户 ID" placeholder="目标用户 QQ" />
            <TextField v-model="scopedProfileScope.group_id" label="群 ID（私聊留空）" placeholder="私聊留空" />
            <button type="submit" class="button button-secondary" :disabled="!scopedProfileReady || scopedProfileQuery.isFetching.value">
              {{ scopedProfileQuery.isFetching.value ? "读取中…" : "读取 v3 画像" }}
            </button>
          </div>
        </form>
        <p v-if="!scopedProfileRequested" class="field-hint">填写平台、Bot 与用户后读取。空群 ID 表示该 Bot 下的私聊来源，不会被解释为“所有群”。</p>
        <p v-if="scopedProfileConflict" class="field-error-msg" role="alert">{{ scopedProfileConflict }}</p>
        <QueryBoundary v-if="scopedProfileRequested && scopedProfileReady" :pending="scopedProfileQuery.isPending.value" :error="scopedProfileQuery.error.value">
          <div v-if="!scopedDocument" class="query-empty">这个四元作用域还没有 v3 画像文档；没有回退读取旧画像。</div>
          <template v-else>
            <dl class="count-ledger">
              <div><dt>文档修订</dt><dd><code>{{ textAt(scopedDocument, 'revision') }}</code></dd></div>
              <div><dt>更新时间</dt><dd>{{ formatDateTime(scopedDocument.updated_at as string | number) }}</dd></div>
              <div><dt>claim 数量</dt><dd>{{ scopedClaims.length }}</dd></div>
              <div><dt>当前来源</dt><dd>{{ scopedProfileScope.group_id.trim() ? `群 ${scopedProfileScope.group_id.trim()}` : "私聊（默认 private）" }}</dd></div>
            </dl>
            <div v-if="scopedClaims.length === 0" class="query-empty">文档暂时没有可审查的稳定 claim。</div>
            <table v-else class="business-table" aria-label="作用域画像 claim 共享设置">
              <thead><tr><th>claim</th><th>当前值</th><th>置信度 / 可见范围</th><th>跨场景使用</th></tr></thead>
              <tbody>
                <tr v-for="claim in scopedClaims" :key="String(claim.key)">
                  <td><code>{{ claim.key || "—" }}</code></td>
                  <td>{{ claim.value || "—" }}</td>
                  <td>{{ claim.confidence ?? "—" }} / {{ claim.visibility || (scopedProfileScope.group_id.trim() ? "group" : "private") }}</td>
                  <td>
                    <button type="button" class="button button-xs" :class="scopedSharedClaimKeys.has(String(claim.key)) ? 'button-danger' : 'button-primary'" :disabled="scopedProfileShare.isPending.value || !String(claim.key || '')" @click="toggleScopedClaimSharing(String(claim.key || ''), !scopedSharedClaimKeys.has(String(claim.key)))">
                      {{ scopedSharedClaimKeys.has(String(claim.key)) ? "取消共享" : "允许共享偏好" }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </template>
        </QueryBoundary>
      </Panel>

      <!-- 创建任务面板 -->
      <Panel
        v-if="currentSection === 'tasks' || currentSection === 'candidate' || currentSection === 'all'"
        eyebrow="PERSONA BUILDER / TASK"
        title="创建构建任务"
      >
        <form class="builder-task-form" @submit.prevent="handleCreateBuildTask">
          <div class="inline-controls filter-control-row">
            <TextField
              v-model="workTitle"
              label="作品名称"
              hide-label
              placeholder="作品名称"
            />
            <TextField
              v-model="characterName"
              label="角色名称"
              hide-label
              placeholder="角色名称"
            />
            <button
              type="submit"
              class="button button-primary"
              :disabled="!workTitle.trim() || !characterName.trim() || buildMutation.isPending.value"
            >
              {{ buildMutation.isPending.value ? "创建中…" : "创建任务" }}
            </button>
          </div>
        </form>

        <dl v-if="buildMutation.data.value" class="task-result-ledger count-ledger">
          <div>
            <dt>任务 ID</dt>
            <dd><code>{{ buildMutation.data.value.task_id || "—" }}</code></dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>{{ buildMutation.data.value.status || "—" }}</dd>
          </div>
          <div>
            <dt>阶段</dt>
            <dd>{{ buildMutation.data.value.stage || "—" }}</dd>
          </div>
          <div>
            <dt>进度说明</dt>
            <dd>{{ buildMutation.data.value.message || "—" }}</dd>
          </div>
        </dl>
      </Panel>

      <!-- 构建历史 -->
      <Panel eyebrow="PERSONA BUILDER / HISTORY" title="构建历史">
        <QueryBoundary
          :pending="historyQuery.isPending.value"
          :error="historyQuery.error.value"
          :empty="!historyRecords.length"
          empty-text="尚无人设构建历史。"
        >
          <table class="business-table">
            <thead>
              <tr>
                <th>候选角色 / 记录 ID</th>
                <th>作品</th>
                <th>校验</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(record, idx) in historyRecords" :key="String(record.record_id || idx)">
                <td>
                  <button
                    type="button"
                    class="text-link"
                    @click="selectedRecordId = String(record.record_id || '')"
                  >
                    <strong>{{ record.character_name || record.persona_name || "未命名" }}</strong>
                    <br />
                    <code>{{ record.record_id || "—" }}</code>
                  </button>
                </td>
                <td>{{ record.work_title || "—" }}</td>
                <td>
                  <StateBadge :tone="record.template_valid === false ? 'error' : 'ok'">
                    {{ record.template_valid === false ? "未通过" : "已校验" }}
                  </StateBadge>
                </td>
                <td>{{ formatDateTime(record.updated_at as string | number) }}</td>
                <td>
                  <button
                    type="button"
                    class="button button-secondary button-xs"
                    :disabled="applyMutation.isPending.value"
                    @click="handleApplyRecord(String(record.record_id || ''))"
                  >
                    应用
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </QueryBoundary>
      </Panel>

      <!-- 详情抽屉/卡片 -->
      <Panel
        v-if="selectedRecordId"
        eyebrow="PERSONA BUILDER / DETAIL"
        :title="`历史详情 ${selectedRecordId}`"
      >
        <QueryBoundary :pending="detailQuery.isPending.value" :error="detailQuery.error.value">
          <dl v-if="detailQuery.data.value" class="count-ledger">
            <div>
              <dt>作品</dt>
              <dd>{{ detailQuery.data.value.work_title || "—" }}</dd>
            </div>
            <div>
              <dt>角色</dt>
              <dd>{{ detailQuery.data.value.character_name || "—" }}</dd>
            </div>
            <div>
              <dt>更新时间</dt>
              <dd>{{ formatDateTime(detailQuery.data.value.updated_at as string | number) }}</dd>
            </div>
            <div>
              <dt>最后修改人</dt>
              <dd>{{ detailQuery.data.value.edited_by || "—" }}</dd>
            </div>
          </dl>
          <section v-if="layersQuery.data.value" class="persona-layer-grid">
            <article><h4>稳定核心</h4><pre>{{ JSON.stringify(layersQuery.data.value.core, null, 2) }}</pre></article>
            <article><h4>缓慢变化</h4><pre>{{ JSON.stringify(layersQuery.data.value.dynamic, null, 2) }}</pre></article>
            <article><h4>当前状态</h4><p>{{ textAt(layersQuery.data.value.current, 'message') }}</p></article>
          </section>
          <button v-if="historyRecords.length > 1" type="button" class="button button-secondary button-xs" @click="diffRecordId = String(historyRecords.find((item) => String(item.record_id) !== selectedRecordId)?.record_id || '')">与上一版本比较</button>
          <button type="button" class="button button-secondary button-xs" :disabled="previewMutation.isPending.value" @click="previewMutation.mutate(selectedRecordId)">{{ previewMutation.isPending.value ? '正在生成六场景预览…' : '生成六场景 API 预览' }}</button>
          <pre v-if="diffQuery.data.value" class="prompt-preview">{{ diffLines || '两个版本没有文本差异。' }}</pre>
          <div v-if="previewMutation.data.value" class="persona-layer-grid"><article v-for="sample in previewSamples" :key="sample.scene"><h4>{{ sample.scene }}</h4><p>{{ sample.text }}</p></article></div>
        </QueryBoundary>
      </Panel>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, reactive } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";

import { resources } from "@/api/resources";
import { ApiError } from "@/api/client";
import type { StickerListItem } from "@/api/types";
import { formatDateTime, formatInteger } from "@/lib/format";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Pagination from "@vue-app/components/Pagination.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import TextField from "@vue-app/components/forms/TextField.vue";

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();

const pageMode = computed(() => {
  const path = route.path || "";
  const name = String(route.name || "");
  if (name.includes("sticker") || path.includes("/persona/stickers")) return "stickers";
  if (name.includes("preview") || path.includes("/persona/persona-preview")) return "preview";
  return "builder";
});

const currentSection = computed(() => String(route.params.section || "all"));
const textAt = (value: unknown, key: string) => {
  const raw = value && typeof value === "object" ? (value as Record<string, unknown>)[key] : undefined;
  return raw === undefined || raw === null || raw === "" ? "—" : String(raw);
};

const profileJobsQuery = useQuery({
  queryKey: ["profile-job-diagnostics"],
  queryFn: ({ signal }) => resources.profileJobDiagnostics(signal),
  enabled: computed(() => pageMode.value === "builder"),
  refetchInterval: 30_000,
});

const scopedProfileScope = reactive({ platform: "onebot", bot_id: "", user_id: "", group_id: "" });
const scopedProfileRequested = ref(false);
const scopedProfileConflict = ref("");
const scopedProfileReady = computed(() => Boolean(
  scopedProfileScope.platform.trim() && scopedProfileScope.bot_id.trim() && scopedProfileScope.user_id.trim(),
));
const scopedProfileQuery = useQuery({
  queryKey: computed(() => [
    "scoped-profile-document",
    scopedProfileScope.platform.trim(), scopedProfileScope.bot_id.trim(),
    scopedProfileScope.user_id.trim(), scopedProfileScope.group_id.trim(),
  ]),
  queryFn: ({ signal }) => resources.scopedProfileDocument({
    platform: scopedProfileScope.platform.trim(), bot_id: scopedProfileScope.bot_id.trim(),
    user_id: scopedProfileScope.user_id.trim(), group_id: scopedProfileScope.group_id.trim(),
  }, signal),
  enabled: computed(() => pageMode.value === "builder" && scopedProfileRequested.value && scopedProfileReady.value),
});
const scopedDocument = computed<Record<string, unknown> | null>(() => {
  const raw = scopedProfileQuery.data.value?.document;
  return raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : null;
});
const scopedClaims = computed<Array<Record<string, unknown>>>(() => {
  const raw = scopedDocument.value?.document;
  const document = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
  return Array.isArray(document.claims) ? document.claims.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
});
const scopedSharedClaimKeys = computed(() => new Set(
  (Array.isArray(scopedProfileQuery.data.value?.shared_claims) ? scopedProfileQuery.data.value?.shared_claims : [])
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    .filter((item) => String(item.source_group_id ?? "") === scopedProfileScope.group_id.trim())
    .map((item) => String(item.key ?? ""))
    .filter(Boolean),
));
const scopedProfileShare = useMutation({
  mutationFn: (payload: { claim_key: string; enabled: boolean }) => resources.setScopedProfileSharing({
    platform: scopedProfileScope.platform.trim(), bot_id: scopedProfileScope.bot_id.trim(),
    user_id: scopedProfileScope.user_id.trim(), group_id: scopedProfileScope.group_id.trim(),
    claim_key: payload.claim_key, revision: Number(scopedDocument.value?.revision ?? -1), enabled: payload.enabled,
  }),
  onSuccess: async () => {
    scopedProfileConflict.value = "";
    await scopedProfileQuery.refetch();
  },
  onError: async (error: Error) => {
    scopedProfileConflict.value = error instanceof ApiError && error.status === 409
      ? "画像在操作前已更新；已刷新当前文档，请确认后再提交。"
      : error.message || "共享设置未保存。";
    await scopedProfileQuery.refetch();
  },
});
function loadScopedProfileDocument() {
  scopedProfileConflict.value = "";
  scopedProfileRequested.value = true;
  if (scopedProfileReady.value) void scopedProfileQuery.refetch();
}
function toggleScopedClaimSharing(claimKey: string, enabled: boolean) {
  if (!scopedDocument.value || !claimKey || scopedProfileShare.isPending.value) return;
  const action = enabled ? "允许此偏好跨场景使用" : "取消此偏好的跨场景使用";
  if (window.confirm(`确认${action}？不会共享私聊或群聊原文。`)) {
    scopedProfileShare.mutate({ claim_key: claimKey, enabled });
  }
}

function setSection(section: string) {
  router.push({ name: route.name || "persona-preview", params: { ...route.params, section } });
}

/* ====================== 1. 表情包管理 (Stickers) ====================== */
const stickerPage = ref(1);
const stickerSearch = ref("");
const selectedUploadFile = ref<File | null>(null);
const uploadDescription = ref("");
const uploadError = ref("");
const visualRelabelJobId = ref("");
const visualRelabelBatch = ref("5");
const visualRelabelStart = useMutation({ mutationFn: () => resources.startVisualRelabel(Math.max(1, Math.min(20, Number(visualRelabelBatch.value) || 5))), onSuccess: (job) => { visualRelabelJobId.value = String(job.job_id || ""); } });
const visualRelabelQuery = useQuery({ queryKey: computed(() => ["visual-relabel", visualRelabelJobId.value]), queryFn: ({ signal }) => resources.visualRelabelJob(visualRelabelJobId.value, signal), enabled: computed(() => Boolean(visualRelabelJobId.value) && pageMode.value === "stickers"), refetchInterval: 1500 });
const visualRelabelPause = useMutation({ mutationFn: () => resources.pauseVisualRelabel(visualRelabelJobId.value), onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["visual-relabel", visualRelabelJobId.value] }) });
const visualRelabelResume = useMutation({ mutationFn: () => resources.resumeVisualRelabel(visualRelabelJobId.value), onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["visual-relabel", visualRelabelJobId.value] }) });
function startVisualRelabel() { visualRelabelStart.mutate(); }
function pauseVisualRelabel() { if (visualRelabelJobId.value) visualRelabelPause.mutate(); }
function resumeVisualRelabel() { if (visualRelabelJobId.value) visualRelabelResume.mutate(); }
const fileInputRef = ref<HTMLInputElement | null>(null);

const stickersQuery = useQuery({
  queryKey: computed(() => ["stickers", stickerPage.value, stickerSearch.value]),
  queryFn: ({ signal }) => resources.stickers(stickerPage.value, 20, stickerSearch.value, signal),
  enabled: computed(() => pageMode.value === "stickers"),
});

const stickerIndexStatus = computed(() => String(stickersQuery.data.value?.index_status || "").trim().toLowerCase());

const stickerIndexLabel = computed(() => {
  const payload = stickersQuery.data.value;
  if (!payload) {
    if (stickersQuery.isPending.value) return "正在读取";
    if (stickersQuery.error.value) return "不可用";
    return "未知／未配置";
  }
  return stickerIndexStatus.value || "未知／未配置";
});

const stickerIndexTone = computed<"ok" | "warn" | "error" | "running" | "unknown">(() => {
  if (!stickersQuery.data.value) {
    return stickersQuery.isPending.value ? "running" : stickersQuery.error.value ? "error" : "unknown";
  }
  if (!stickerIndexStatus.value || ["unknown", "not_configured", "unconfigured"].includes(stickerIndexStatus.value)) {
    return "unknown";
  }
  if (["error", "failed", "unavailable"].includes(stickerIndexStatus.value)) return "error";
  return stickersQuery.data.value.index_stale ? "warn" : "ok";
});

const rescanMutation = useMutation({
  mutationFn: () => resources.rescanStickers(),
  onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["stickers"] }),
});

const rebuildMutation = useMutation({
  mutationFn: () => resources.rebuildStickerIndex(),
  onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["stickers"] }),
});

const uploadMutation = useMutation({
  mutationFn: (data: { file: File; description: string }) => resources.uploadSticker(data.file, data.description),
  onSuccess: () => {
    selectedUploadFile.value = null;
    uploadDescription.value = "";
    uploadError.value = "";
    if (fileInputRef.value) fileInputRef.value.value = "";
    void queryClient.invalidateQueries({ queryKey: ["stickers"] });
  },
  onError: (err: Error) => {
    uploadError.value = err.message || "上传失败";
  },
});

const deleteMutation = useMutation({
  mutationFn: (filename: string) => resources.deleteSticker(filename),
  onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["stickers"] }),
});

const updateMutation = useMutation({
  mutationFn: (payload: { name: string; body: Record<string, unknown> }) =>
    resources.updateSticker(payload.name, payload.body),
  onSuccess: () => {
    editingSticker.value = null;
    void queryClient.invalidateQueries({ queryKey: ["stickers"] });
  },
});

const editingSticker = ref<StickerListItem | null>(null);
const editForm = reactive({ description: "", mood_tags: "", scene_tags: "" });

function onFileSelected(e: Event) {
  const target = e.target as HTMLInputElement;
  if (target.files && target.files[0]) {
    selectedUploadFile.value = target.files[0];
    uploadError.value = "";
  }
}

function handleUpload() {
  if (!selectedUploadFile.value) return;
  uploadMutation.mutate({
    file: selectedUploadFile.value,
    description: uploadDescription.value,
  });
}

function handleRescan() {
  if (window.confirm("确认重新扫描服务器表情包文件目录并同步？")) {
    rescanMutation.mutate();
  }
}

function handleRebuildIndex() {
  if (window.confirm("确认全量重建表情包向量与元数据索引？此操作可能耗时较长。")) {
    rebuildMutation.mutate();
  }
}

function handleDeleteSticker(filename: string) {
  if (window.confirm(`确认永久删除表情包「${filename}」？此操作不可恢复。`)) {
    deleteMutation.mutate(filename);
  }
}

function startEditingSticker(item: StickerListItem) {
  editingSticker.value = item;
  editForm.description = item.description || "";
  editForm.mood_tags = (item.mood_tags || []).join(", ");
  editForm.scene_tags = (item.scene_tags || []).join(", ");
}

function handleSaveStickerEdit() {
  if (!editingSticker.value) return;
  const mood_tags = editForm.mood_tags
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const scene_tags = editForm.scene_tags
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  updateMutation.mutate({
    name: editingSticker.value.filename,
    body: {
      description: editForm.description,
      mood_tags,
      scene_tags,
    },
  });
}

/* ====================== 2. 人设预览 (Persona Preview) ====================== */
const previewQuery = useQuery({
  queryKey: ["persona-prompt-preview"],
  queryFn: ({ signal }) => resources.personaPromptPreview(signal),
  enabled: computed(() => pageMode.value === "preview"),
});

const effectivePrompt = computed(() => {
  const data = previewQuery.data.value;
  if (!data) return "";
  return String(data.prompt || data.prompt_preview || data.system_prompt || data.persona_prompt || "");
});

const previewWarnings = computed(() => {
  const data = previewQuery.data.value;
  if (!data) return [];
  const raw = data.warnings || data.quality_warnings;
  return Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : [];
});

const previewSources = computed(() => {
  const data = previewQuery.data.value;
  if (!data) return [];
  const raw = data.sources || data.source_files;
  return Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : [];
});

/* ====================== 3. 人设构建 (Persona Builder) ====================== */
const workTitle = ref("");
const characterName = ref("");
const selectedRecordId = ref("");
const diffRecordId = ref("");

const historyQuery = useQuery({
  queryKey: ["persona-builder-history"],
  queryFn: ({ signal }) => resources.personaBuilderGet("history", signal),
  enabled: computed(() => pageMode.value === "builder"),
});

const detailQuery = useQuery({
  queryKey: computed(() => ["persona-builder-detail", selectedRecordId.value]),
  queryFn: ({ signal }) => resources.personaBuilderGet(`history/${encodeURIComponent(selectedRecordId.value)}`, signal),
  enabled: computed(() => pageMode.value === "builder" && Boolean(selectedRecordId.value)),
});
const layersQuery = useQuery({
  queryKey: computed(() => ["persona-builder-layers", selectedRecordId.value]),
  queryFn: ({ signal }) => resources.personaBuilderGet(`history/${encodeURIComponent(selectedRecordId.value)}/layers`, signal),
  enabled: computed(() => pageMode.value === "builder" && Boolean(selectedRecordId.value)),
});
const diffQuery = useQuery({
  queryKey: computed(() => ["persona-builder-diff", selectedRecordId.value, diffRecordId.value]),
  queryFn: ({ signal }) => resources.personaBuilderGet(`history/${encodeURIComponent(selectedRecordId.value)}/diff/${encodeURIComponent(diffRecordId.value)}`, signal),
  enabled: computed(() => pageMode.value === "builder" && Boolean(selectedRecordId.value) && Boolean(diffRecordId.value)),
});
const diffLines = computed(() => {
  const raw = diffQuery.data.value?.unified_diff;
  return Array.isArray(raw) ? raw.map((item) => String(item)).join("\n") : "";
});
const previewMutation = useMutation({
  mutationFn: (recordId: string) => resources.personaBuilderPost(`history/${encodeURIComponent(recordId)}/preview`, {}),
});
const previewSamples = computed(() => {
  const raw = previewMutation.data.value?.samples;
  return Array.isArray(raw) ? raw.filter((item): item is { scene: string; text: string } => Boolean(item) && typeof item === "object").map((item) => ({ scene: String((item as Record<string, unknown>).scene || "场景"), text: String((item as Record<string, unknown>).text || "") })) : [];
});

const historyRecords = computed(() => {
  const data = historyQuery.data.value;
  if (!data) return [];
  const raw = data.records || data.items;
  return Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : [];
});

const buildMutation = useMutation({
  mutationFn: (payload: { work_title: string; character_name: string }) =>
    resources.personaBuilderPost("build-task", payload),
  onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["persona-builder-history"] }),
});

const applyMutation = useMutation({
  mutationFn: (recordId: string) => resources.personaBuilderPost("apply", { record_id: recordId }),
});

function handleCreateBuildTask() {
  if (!workTitle.value.trim() || !characterName.value.trim()) return;
  if (window.confirm(`确认创建 ${workTitle.value} / ${characterName.value} 的人设构建任务？`)) {
    buildMutation.mutate({
      work_title: workTitle.value.trim(),
      character_name: characterName.value.trim(),
    });
  }
}

function handleApplyRecord(recordId: string) {
  if (!recordId) return;
  if (window.confirm(`确认应用人设记录 ${recordId}？`)) {
    applyMutation.mutate(recordId);
  }
}
</script>
