<template>
  <div class="page-stack">
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
                <StateBadge :tone="stickersQuery.data.value?.index_stale ? 'warn' : 'ok'">
                  {{ stickersQuery.data.value?.index_status || "正常" }}
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
            <div class="inline-controls">
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

          <!-- 分页控件 -->
          <div class="pagination">
            <button
              type="button"
              :disabled="stickerPage <= 1"
              @click="stickerPage--"
            >
              上一页
            </button>
            <span>第 {{ stickerPage }} / {{ stickersQuery.data.value?.total_pages || 1 }} 页</span>
            <button
              type="button"
              :disabled="stickerPage >= (stickersQuery.data.value?.total_pages || 1)"
              @click="stickerPage++"
            >
              下一页
            </button>
          </div>
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
import type { StickerListItem } from "@/api/types";
import { formatDateTime, formatInteger } from "@/lib/format";
import PageHeader from "@vue-app/components/PageHeader.vue";
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

function setSection(section: string) {
  router.push({ name: route.name || "persona-preview", params: { ...route.params, section } });
}

/* ====================== 1. 表情包管理 (Stickers) ====================== */
const stickerPage = ref(1);
const stickerSearch = ref("");
const selectedUploadFile = ref<File | null>(null);
const uploadDescription = ref("");
const uploadError = ref("");
const fileInputRef = ref<HTMLInputElement | null>(null);

const stickersQuery = useQuery({
  queryKey: computed(() => ["stickers", stickerPage.value, stickerSearch.value]),
  queryFn: ({ signal }) => resources.stickers(stickerPage.value, 20, stickerSearch.value, signal),
  enabled: computed(() => pageMode.value === "stickers"),
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
