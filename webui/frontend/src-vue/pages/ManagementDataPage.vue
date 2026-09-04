<template>
  <div class="page-stack">
    <!-- 列表视图 -->
    <template v-if="currentSection === 'list' || (dataset === 'stickers' && currentSection === 'catalog')">
      <PageHeader
        :index="dataset === 'personas' ? '用户画像 / 列表' : dataset === 'groups' ? '群信息 / 列表' : '表情包 / 目录'"
        :title="dataset === 'personas' ? '用户画像' : dataset === 'groups' ? '群信息' : '表情包'"
        :description="dataset === 'personas' ? '只读取白名单摘要投影；不逐行调用 OneBot，也不返回原始画像正文。' : dataset === 'groups' ? '群目录使用 SQL 分页；未确认历史候选默认隐藏。' : '读取持久化贴纸索引；完整 metadata 只在编辑时加载当前项。'"
      >
        <template #actions>
          <div class="search-field">
            <input
              v-model="searchDraft"
              placeholder="搜索当前目录"
              aria-label="搜索当前目录"
              @input="handleSearchInput"
            />
          </div>
        </template>
      </PageHeader>

      <!-- Personas 筛选 -->
      <Panel v-if="dataset === 'personas'" eyebrow="FILTER / PERSONAS" title="服务端筛选">
        <div class="inline-controls filter-control-row">
          <input :value="groupIdQuery" placeholder="群 ID" aria-label="按群筛选" @input="updateParam('group_id', ($event.target as HTMLInputElement).value)" />
          <input :value="favorabilityLevelQuery" placeholder="好感等级" aria-label="按好感等级筛选" @input="updateParam('favorability_level', ($event.target as HTMLInputElement).value)" />
          <select :value="sortByQuery" aria-label="画像排序" @change="updateParam('sort', ($event.target as HTMLSelectElement).value)">
            <option value="updated_at">更新时间降序</option>
            <option value="favorability">好感度降序</option>
            <option value="user_id">QQ 号升序</option>
          </select>
        </div>
      </Panel>

      <!-- Groups 筛选 -->
      <Panel v-if="dataset === 'groups'" eyebrow="FILTER / GROUPS" title="服务端筛选">
        <div class="inline-controls filter-control-row">
          <select :value="membershipStateQuery" aria-label="关系来源" @change="onMembershipStateChange">
            <option value="">确认与配置</option>
            <option value="confirmed">仅已确认</option>
            <option value="configured">仅配置</option>
            <option value="unconfirmed">仅未确认候选</option>
          </select>
          <select :value="enabledQuery" aria-label="群开关状态" @change="updateParam('enabled', ($event.target as HTMLSelectElement).value)">
            <option value="">全部开关</option>
            <option value="true">已启用</option>
            <option value="false">已停用</option>
          </select>
          <label class="checkbox-label">
            <input type="checkbox" :checked="includeUnconfirmedQuery" @change="updateParam('include_unconfirmed', ($event.target as HTMLInputElement).checked ? 'true' : '')" />
            显示未确认候选
          </label>
        </div>
      </Panel>

      <!-- 列表内容 -->
      <QueryBoundary :pending="listQuery.isPending.value" :error="listQuery.error.value">
        <template v-if="listQuery.data.value?.items?.length">
          <!-- 画像表格 -->
          <Panel v-if="dataset === 'personas'" eyebrow="CACHE / PERSONAS" title="画像摘要索引">
            <div class="trace-table-wrap">
              <table class="forensic-table">
                <thead>
                  <tr>
                    <th>用户</th>
                    <th>QQ ID</th>
                    <th>好感</th>
                    <th>最近群</th>
                    <th>更新时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in (listQuery.data.value.items as PersonaListItem[])" :key="item.user_id">
                    <td>
                      <div class="table-identity">
                        <span class="avatar-stamp">
                          <img v-if="item.avatar_url" :src="item.avatar_url" :alt="item.nickname || item.user_id" referrerpolicy="no-referrer" />
                          <span v-else>{{ (item.nickname || item.user_id).slice(0, 1) }}</span>
                        </span>
                        <strong>{{ item.nickname || "未缓存昵称" }}</strong>
                      </div>
                    </td>
                    <td>
                      <code>{{ item.qq_id || item.user_id }}</code>
                      <button class="copy-id" type="button" @click="copyText(item.qq_id || item.user_id)">复制</button>
                    </td>
                    <td :class="favorabilityDisplayClass(item.favorability_score ?? item.favorability?.score)">
                      {{ item.favorability_level || item.favorability?.level || "未分级" }} · {{ item.favorability_score ?? item.favorability?.score ?? 0 }}
                    </td>
                    <td><code>{{ item.recent_group_id || "—" }}</code></td>
                    <td>{{ formatDateTime(item.updated_at) }}</td>
                    <td>
                      <button class="button button-secondary" type="button" @click="navigateToDetail('personas', item.user_id)">查看画像</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Panel>

          <!-- 群信息表格 -->
          <Panel v-if="dataset === 'groups'" eyebrow="CACHE / GROUPS" title="群目录快照">
            <div class="trace-table-wrap">
              <table class="forensic-table">
                <thead>
                  <tr>
                    <th>群</th>
                    <th>群 ID</th>
                    <th>关系</th>
                    <th>开关</th>
                    <th>关联 Bot</th>
                    <th>成员</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in (listQuery.data.value.items as GroupListItem[])" :key="item.group_id">
                    <td>
                      <div class="table-identity">
                        <span class="avatar-stamp">
                          <img v-if="item.avatar_url" :src="item.avatar_url" :alt="item.group_name || item.group_id" referrerpolicy="no-referrer" />
                          <span v-else>{{ (item.group_name || item.group_id).slice(0, 1) }}</span>
                        </span>
                        <strong>{{ item.group_name || "未缓存群名" }}</strong>
                      </div>
                    </td>
                    <td><code>{{ item.group_id }}</code></td>
                    <td>
                      <StateBadge :tone="item.membership_state === 'confirmed' ? 'ok' : item.membership_state === 'configured' ? 'unknown' : 'warn'">
                        {{ item.membership_state === "confirmed" ? "已确认" : item.membership_state === "configured" ? "仅配置" : "未确认候选" }}
                      </StateBadge>
                    </td>
                    <td>
                      <StateBadge :tone="item.enabled ? 'ok' : 'unknown'">
                        {{ item.enabled ? "启用" : "停用" }}
                      </StateBadge>
                    </td>
                    <td>{{ (item.bot_ids || item.bot_self_ids || []).join("、") || "未确认" }}</td>
                    <td>{{ item.member_count ?? "—" }}</td>
                    <td>
                      <button class="button button-secondary" type="button" @click="navigateToDetail('groups', item.group_id)">打开详情</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Panel>

          <!-- 表情包目录 -->
          <Panel v-if="dataset === 'stickers'" eyebrow="INDEX / STICKERS" title="持久贴纸索引">
            <div class="sticker-grid">
              <article v-for="item in (listQuery.data.value.items as StickerListItem[])" :key="item.filename">
                <img :src="item.thumbnail_url" :alt="item.description || item.filename" loading="lazy" referrerpolicy="no-referrer" />
                <div>
                  <strong>{{ item.filename }}</strong>
                  <p>{{ item.description || "未标注" }}</p>
                  <small>{{ formatInteger(item.size_bytes) }} B · {{ [...item.mood_tags, ...item.scene_tags].join(" / ") || "无标签" }}</small>
                  <button class="button button-secondary" type="button" @click="navigateToDetail('stickers', item.filename)">编辑</button>
                </div>
              </article>
            </div>
          </Panel>
        </template>
        <Panel v-else eyebrow="EMPTY" title="暂无数据">
          <p class="muted-copy">当前筛选条件下没有记录。</p>
        </Panel>
      </QueryBoundary>

      <!-- 索引状态行与分页 -->
      <div v-if="managementData?.index" class="index-status-line">
        <StateBadge :tone="managementData.index.state === 'ready' ? 'ok' : 'running'">
          {{ managementData.index.state === "ready" ? "管理投影已就绪" : "管理投影后台重建中" }}
        </StateBadge>
        <code>{{ managementData.index.detail_code }}</code>
        <span>索引时间 {{ formatDateTime(managementData.index.indexed_at) }}</span>
      </div>

      <div v-if="listQuery.data.value && listQuery.data.value.total_pages > 1" class="pagination">
        <button type="button" :disabled="pageQuery <= 1" @click="setPage(pageQuery - 1)">上一页</button>
        <span>第 {{ listQuery.data.value.page }} / {{ listQuery.data.value.total_pages }} 页（共 {{ listQuery.data.value.total }} 条）</span>
        <button type="button" :disabled="pageQuery >= listQuery.data.value.total_pages" @click="setPage(pageQuery + 1)">下一页</button>
      </div>
    </template>

    <!-- 画像详情视图 -->
    <template v-else-if="dataset === 'personas'">
      <PageHeader
        :index="`用户画像 / ${currentSection === 'refresh' ? '后台刷新' : '详情'}`"
        title="画像详情"
        description="详情按 QQ ID 懒加载，并把 QQ 公开资料、用户明确更正、系统观察和模型结构化字段分区展示。"
      >
        <template #actions>
          <div class="inline-controls">
            <input :value="detailUserId" placeholder="QQ ID" inputmode="numeric" @input="updateParam('user_id', ($event.target as HTMLInputElement).value)" />
            <input :value="detailGroupId" placeholder="群 ID（可选）" inputmode="numeric" @input="updateParam('group_id', ($event.target as HTMLInputElement).value)" />
          </div>
        </template>
      </PageHeader>

      <Panel v-if="!detailUserId" eyebrow="PROMPT" title="需要用户 ID">
        <p class="muted-copy">从画像列表选择用户，或输入 QQ ID。</p>
      </Panel>
      <QueryBoundary v-else :pending="personaDetailQuery.isPending.value" :error="personaDetailQuery.error.value">
        <div v-if="personaDetailQuery.data.value" class="page-stack">
          <div class="summary-grid">
            <Panel eyebrow="QQ / PUBLIC PROFILE" :title="text(personaQqProfile.nickname, detailUserId)">
              <dl class="compact-kv">
                <dt>QQ ID</dt><dd><code>{{ detailUserId }}</code></dd>
                <dt>签名</dt><dd>{{ text(personaQqProfile.signature) }}</dd>
                <dt>所在地</dt><dd>{{ [personaQqProfile.country, personaQqProfile.province, personaQqProfile.city].filter(Boolean).join(" / ") || "—" }}</dd>
                <dt>等级</dt><dd>{{ text(personaQqProfile.level) }}</dd>
                <dt>更新时间</dt><dd>{{ formatDateTime(personaCoreProfile.updated_at as string | number | null) }}</dd>
              </dl>
            </Panel>
            <GroupFavorabilityPanel :favorability="personaFavorability" eyebrow="RELATION / FAVORABILITY" />
          </div>

          <Panel eyebrow="PROFILE / SOURCE-AWARE" title="结构化画像（不显示原始 profile_text）">
            <dl class="compact-kv">
              <dt>用户明确更正</dt>
              <dd>{{ Object.keys(personaUserCorrections).length ? Object.entries(personaUserCorrections).map(([k, v]) => `${k}=${text(v)}`).join("；") : "无" }}</dd>
              <dt>结构化字段</dt>
              <dd>{{ Object.entries(personaStructured).filter(([, item]) => typeof item !== 'object').slice(0, 20).map(([k, v]) => `${k}=${text(v)}`).join("；") || "暂无白名单字段" }}</dd>
              <dt>头像分析状态</dt>
              <dd>{{ Object.keys(personaAvatarAnalysis).length ? "已有受控分析" : "未分析" }}</dd>
              <dt>证据范围</dt>
              <dd>{{ detailGroupId ? `群 ${detailGroupId}` : "全局" }}</dd>
            </dl>
          </Panel>

          <Panel eyebrow="ACTIONS / EXPLICIT TARGET" title="画像维护">
            <div class="form-grid">
              <label>更正字段<input v-model="correctionField" placeholder="例如 nickname_preference" /></label>
              <label>更正值<input v-model="correctionValue" /></label>
            </div>
            <div class="inline-controls">
              <button class="button" type="button" :disabled="!correctionField || !correctionValue || personaActionMutation.isPending.value" @click="personaActionMutation.mutate('correct')">保存用户明确更正</button>
              <button class="button button-secondary" type="button" :disabled="!detailGroupId || !botStore.selectedBotId || personaActionMutation.isPending.value" @click="personaActionMutation.mutate('refresh')">按群实时刷新</button>
              <button class="button button-secondary" type="button" :disabled="personaActionMutation.isPending.value" @click="personaActionMutation.mutate('avatar')">刷新头像分析</button>
              <button class="button button-danger" type="button" :disabled="personaActionMutation.isPending.value" @click="confirmClearAvatar">清除头像分析</button>
            </div>
            <p class="muted-copy">群刷新会核对 Bot、群和成员三元关系；缺少在线 Bot 或 membership 时后端会拒绝。</p>
          </Panel>
        </div>
      </QueryBoundary>
    </template>

    <!-- 群详情视图 -->
    <template v-else-if="dataset === 'groups'">
      <PageHeader
        :index="`群信息 / ${currentSection === 'knowledge' ? '知识与风格' : currentSection === 'members' ? '成员与别名' : currentSection === 'peer-bots' ? 'Peer Bot 协作' : currentSection === 'qzone-agent' ? '空间互动' : '群详情'}`"
        title="群详情"
        description="成员画像、别名、群风格、知识、梗、Agent 状态、Peer Bot 协作、空间互动和计划均按当前群懒加载。"
      >
        <template #actions>
          <input :value="detailGroupId" placeholder="群 ID" inputmode="numeric" @input="updateParam('group_id', ($event.target as HTMLInputElement).value)" />
        </template>
      </PageHeader>

      <Panel v-if="!detailGroupId" eyebrow="PROMPT" title="需要群 ID">
        <p class="muted-copy">从群列表选择群，或输入群 ID。</p>
      </Panel>

      <GroupPeerBotsPanel
        v-else-if="currentSection === 'peer-bots'"
        :group-id="detailGroupId"
        :bot-id="botStore.selectedBotId"
      />
      <GroupQzoneAgentPanel
        v-else-if="currentSection === 'qzone-agent'"
        :group-id="detailGroupId"
      />

      <!-- 基础群详情 -->
      <QueryBoundary v-else :pending="groupDetailQuery.isPending.value" :error="groupDetailQuery.error.value">
        <div v-if="groupDetailData" class="page-stack">
          <!-- members 分区 -->
          <template v-if="currentSection === 'members'">
            <GroupFavorabilityPanel :favorability="groupMemberFavorability" />
            <GroupMembersPanel :profiles="groupMemberProfiles" />
            <GroupAliasesPanel
              :aliases="groupAliases"
              :member-id="aliasMemberId"
              :alias-text="aliasText"
              :pending="groupActionMutation.isPending.value"
              @update:member-id="aliasMemberId = $event"
              @update:alias-text="aliasText = $event"
              @save="groupActionMutation.mutate('alias-save')"
              @delete="confirmDeleteAlias"
            />
          </template>

          <!-- knowledge 分区 -->
          <template v-else-if="currentSection === 'knowledge'">
            <div class="summary-grid">
              <Panel eyebrow="GROUP / KNOWLEDGE" title="群知识">
                <p>{{ text(groupKnowledge.summary ?? groupKnowledge.knowledge, "暂无知识摘要") }}</p>
              </Panel>
              <Panel eyebrow="GROUP / STYLE" title="群风格">
                <p>{{ text(groupStyle.summary ?? groupStyle.style, "暂无风格摘要") }}</p>
              </Panel>
            </div>
            <Panel eyebrow="ACTIONS / REBUILD" title="重建任务">
              <div class="inline-controls">
                <button class="button" type="button" :disabled="groupActionMutation.isPending.value" @click="confirmRebuildKnowledge">重建群知识</button>
                <button class="button button-secondary" type="button" :disabled="groupActionMutation.isPending.value" @click="confirmRebuildStyle">重建群风格</button>
              </div>
            </Panel>
            <Panel eyebrow="GROUP / MEMES" :title="`群梗（${groupMemes.length}）`">
              <ul v-if="groupMemes.length" class="business-list">
                <li v-for="(item, index) in groupMemes" :key="`${text(item.term)}:${index}`">
                  <strong>{{ text(item.term, `群梗 ${index + 1}`) }}</strong>
                  <span>{{ text(item.meaning ?? item.description) }}</span>
                </li>
              </ul>
              <p v-else class="muted-copy">暂无群梗记录。</p>
            </Panel>
          </template>

          <!-- 默认作息与状态分区 -->
          <template v-else>
            <div class="summary-grid">
              <Panel eyebrow="AGENT / STATE" title="群内 Agent 状态">
                <dl class="compact-kv">
                  <dt>心情</dt><dd>{{ text(groupAgentState.mood) }}</dd>
                  <dt>能量</dt><dd>{{ text(groupAgentState.energy) }}</dd>
                  <dt>待处理</dt><dd>{{ text(groupAgentState.pending) }}</dd>
                </dl>
              </Panel>
              <Panel eyebrow="GROUP / SCHEDULE" title="群作息与计划">
                <label class="checkbox-label">
                  <input v-model="scheduleEnabled" type="checkbox" />
                  启用群作息
                </label>
                <textarea v-model="scheduleText" rows="8" placeholder="群作息表" />
                <div class="inline-controls">
                  <button class="button" type="button" :disabled="groupActionMutation.isPending.value" @click="groupActionMutation.mutate('schedule-save')">保存计划</button>
                  <button class="button button-secondary" type="button" :disabled="groupActionMutation.isPending.value" @click="groupActionMutation.mutate('schedule-generate')">自动生成草稿</button>
                </div>
              </Panel>
            </div>
            <Panel eyebrow="GROUP / OBSERVATION" title="可观察状态">
              <dl class="compact-kv">
                <dt>群 ID</dt><dd><code>{{ detailGroupId }}</code></dd>
                <dt>群梗数量</dt><dd>{{ groupMemes.length }}</dd>
                <dt>调度启用</dt><dd>{{ scheduleEnabled ? "是" : "否" }}</dd>
                <dt>诊断边界</dt><dd>不展示隐藏思维链或原始聊天正文</dd>
              </dl>
            </Panel>
          </template>
        </div>
      </QueryBoundary>
    </template>

    <!-- 表情包操作视图 -->
    <template v-else-if="dataset === 'stickers'">
      <PageHeader
        :index="`表情包 / ${currentSection === 'index' ? '索引任务' : '上传与编辑'}`"
        :title="currentSection === 'index' ? '表情包索引' : '上传与编辑'"
        description="上传限制由后端校验；删除会移动到库内回收目录，结果未知时不会自动重试。"
      />
      <Panel v-if="currentSection === 'index'" eyebrow="INDEX / TASKS" title="索引维护">
        <div class="inline-controls">
          <button class="button" type="button" :disabled="stickerMutation.isPending.value" @click="stickerMutation.mutate('rescan')">增量扫描</button>
          <button class="button button-secondary" type="button" :disabled="stickerMutation.isPending.value" @click="stickerMutation.mutate('rebuild')">后台重建管理投影</button>
        </div>
      </Panel>
      <div v-else class="page-stack">
        <Panel eyebrow="UPLOAD / NEW" title="上传新表情包">
          <div class="form-grid">
            <label>图片文件<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" @change="onStickerFileChange" /></label>
            <label>描述<input v-model="stickerUploadDesc" /></label>
          </div>
          <button class="button" type="button" :disabled="!stickerUploadFile || stickerMutation.isPending.value" @click="stickerMutation.mutate('upload')">上传并写入索引</button>
        </Panel>
        <Panel eyebrow="EDIT / EXISTING" title="编辑现有表情包">
          <div class="form-grid">
            <label>文件名<input :value="selectedStickerParam" placeholder="从目录页选择" @input="updateParam('sticker', ($event.target as HTMLInputElement).value)" /></label>
            <label>描述<input v-model="stickerEditDesc" /></label>
            <label>心情标签<input v-model="stickerMoodTags" /></label>
            <label>场景标签<input v-model="stickerSceneTags" /></label>
          </div>
          <div class="inline-controls">
            <button class="button" type="button" :disabled="!selectedStickerItem || stickerMutation.isPending.value" @click="stickerMutation.mutate('save')">保存 metadata</button>
            <button class="button button-danger" type="button" :disabled="!selectedStickerItem || stickerMutation.isPending.value" @click="confirmDeleteSticker">移到回收目录</button>
          </div>
          <p v-if="selectedStickerParam && !stickerItemQuery.isPending.value && !selectedStickerItem" class="error-copy">未找到完全匹配的文件名，请返回目录重新选择。</p>
        </Panel>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, useRouter, type LocationQueryRaw } from "vue-router";

import { resources } from "@/api/resources";
import type { GroupListItem, Page, PersonaListItem, StickerListItem } from "@/api/types";
import { formatDateTime, formatInteger } from "@/lib/format";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import { useBotStore } from "@vue-app/stores/bot";
import GroupPeerBotsPanel from "@vue-app/components/GroupPeerBotsPanel.vue";
import GroupQzoneAgentPanel from "@vue-app/components/GroupQzoneAgentPanel.vue";

import GroupAliasesPanel from "./subcomponents/GroupAliasesPanel.vue";
import GroupFavorabilityPanel from "./subcomponents/GroupFavorabilityPanel.vue";
import GroupMembersPanel from "./subcomponents/GroupMembersPanel.vue";
import { favorabilityDisplayClass, record, records, text } from "./subcomponents/managementHelpers";

const props = withDefaults(defineProps<{ dataset?: "personas" | "groups" | "stickers" }>(), {
  dataset: "personas",
});

const route = useRoute();
const router = useRouter();
const botStore = useBotStore();
const queryClient = useQueryClient();

const currentSection = computed(() => {
  const s = String(route.params.section || "");
  if (s) return s;
  return props.dataset === "stickers" ? "catalog" : "list";
});

const pageQuery = computed(() => Math.max(1, Number(route.query.page ?? 1) || 1));
const searchQuery = computed(() => String(route.query.search ?? ""));
const groupIdQuery = computed(() => String(route.query.group_id ?? ""));
const favorabilityLevelQuery = computed(() => String(route.query.favorability_level ?? ""));
const sortByQuery = computed(() => String(route.query.sort ?? "updated_at"));
const membershipStateQuery = computed(() => String(route.query.membership_state ?? ""));
const includeUnconfirmedQuery = computed(() => route.query.include_unconfirmed === "true");
const enabledQuery = computed(() => String(route.query.enabled ?? ""));

const searchDraft = ref(searchQuery.value);
watch(searchQuery, (next) => { searchDraft.value = next; });

let debounceTimer: ReturnType<typeof setTimeout> | undefined;
function handleSearchInput() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    updateParam("search", searchDraft.value);
  }, 300);
}

function updateParam(key: string, value: string) {
  const next = { ...route.query, [key]: value || undefined };
  if (key !== "page") next.page = "1";
  router.replace({ query: next });
}

function onMembershipStateChange(event: Event) {
  const val = (event.target as HTMLSelectElement).value;
  const next: LocationQueryRaw = { ...route.query, membership_state: val || undefined, page: "1" };
  if (val === "unconfirmed") next.include_unconfirmed = "true";
  router.replace({ query: next });
}

function setPage(target: number) {
  router.replace({ query: { ...route.query, page: String(target) } });
}

function copyText(val: string) {
  void navigator.clipboard.writeText(val);
}

function navigateToDetail(dataset: "personas" | "groups" | "stickers", id: string) {
  if (dataset === "personas") router.push({ path: "/persona/personas/detail", query: { user_id: id } });
  else if (dataset === "groups") router.push({ path: "/persona/groups/detail", query: { group_id: id } });
  else router.push({ path: "/persona/stickers/upload", query: { sticker: id } });
}

// 列表 Query
const listQuery = useQuery<Page<any> & { index?: { state: string; detail_code: string; indexed_at: number } }>({
  queryKey: computed(() => ["management-data", props.dataset, pageQuery.value, searchQuery.value, groupIdQuery.value, favorabilityLevelQuery.value, sortByQuery.value, membershipStateQuery.value, includeUnconfirmedQuery.value, enabledQuery.value, botStore.selectedBotId]),
  queryFn: async ({ signal }) => {
    if (props.dataset === "personas") {
      return await resources.personasFiltered(pageQuery.value, 20, { search: searchQuery.value, group_id: groupIdQuery.value, favorability_level: favorabilityLevelQuery.value, sort_by: sortByQuery.value, direction: sortByQuery.value === "user_id" ? "asc" : "desc" }, signal);
    }
    if (props.dataset === "groups") {
      return await resources.groupsFiltered(pageQuery.value, 20, { search: searchQuery.value, membership_state: membershipStateQuery.value, include_unconfirmed: includeUnconfirmedQuery.value, enabled: enabledQuery.value, bot_id: botStore.selectedBotId, sort_by: sortByQuery.value === "updated_at" ? "group_id" : sortByQuery.value, direction: "asc" }, signal);
    }
    return await resources.stickers(pageQuery.value, 20, searchQuery.value, signal);
  },
});

const managementData = computed(() => listQuery.data.value as any);

// 画像详情 Query & Mutations
const detailUserId = computed(() => String(route.query.user_id ?? ""));
const detailGroupId = computed(() => String(route.query.group_id ?? ""));
const correctionField = ref("");
const correctionValue = ref("");

const personaDetailQuery = useQuery({
  queryKey: computed(() => ["persona-detail", detailUserId.value, detailGroupId.value]),
  queryFn: ({ signal }) => resources.personaDetail(detailUserId.value, detailGroupId.value, signal),
  enabled: computed(() => Boolean(detailUserId.value && props.dataset === "personas")),
});

const personaCoreProfile = computed(() => record(personaDetailQuery.data.value?.core_profile));
const personaQqProfile = computed(() => record(personaCoreProfile.value.qq_profile));
const personaFavorability = computed(() => record(personaDetailQuery.data.value?.favorability));
const personaStructured = computed(() => record(personaCoreProfile.value.structured));
const personaUserCorrections = computed(() => record(personaCoreProfile.value.user_corrections));
const personaAvatarAnalysis = computed(() => record(personaCoreProfile.value.avatar_analysis));

const personaActionMutation = useMutation({
  mutationFn: async (action: "refresh" | "correct" | "avatar" | "clear-avatar") => {
    if (action === "refresh") return resources.refreshPersona(detailUserId.value, detailGroupId.value, botStore.selectedBotId);
    if (action === "correct") return resources.correctPersona(detailUserId.value, { corrections: { [correctionField.value]: correctionValue.value } });
    if (action === "avatar") return resources.refreshPersonaAvatar(detailUserId.value);
    return resources.clearPersonaAvatar(detailUserId.value);
  },
  onSuccess: () => { void personaDetailQuery.refetch(); },
});

function confirmClearAvatar() {
  if (window.confirm(`确认清除 QQ ${detailUserId.value} 的头像分析？`)) {
    personaActionMutation.mutate("clear-avatar");
  }
}

// 群详情 Query & Mutations
const isSpecializedGroupSection = computed(() => currentSection.value === "peer-bots" || currentSection.value === "qzone-agent");
const groupApiSections = computed(() => {
  if (currentSection.value === "knowledge") return ["knowledge", "style", "memes"] as const;
  if (currentSection.value === "members") return ["personas", "aliases"] as const;
  if (isSpecializedGroupSection.value) return [];
  return ["schedule", "agent-state", "memes"] as const;
});

const groupDetailQuery = useQuery({
  queryKey: computed(() => ["group-business", detailGroupId.value, groupApiSections.value.join("+")]),
  queryFn: async ({ signal }) => {
    const entries = await Promise.all(
      groupApiSections.value.map(async (name) => [name, await resources.groupBusiness(detailGroupId.value, name, signal)])
    );
    return Object.fromEntries(entries);
  },
  enabled: computed(() => Boolean(detailGroupId.value && props.dataset === "groups" && !isSpecializedGroupSection.value)),
});

const groupDetailData = computed(() => record(groupDetailQuery.data.value));
const groupAliases = computed(() => records(record(groupDetailData.value.aliases).aliases));
const groupMemberProfiles = computed(() => records(record(groupDetailData.value.personas).profiles));
const groupMemberFavorability = computed(() => record(record(groupDetailData.value.personas).group_favorability));
const groupMemes = computed(() => records(record(groupDetailData.value.memes).items ?? record(groupDetailData.value.memes).memes));
const groupKnowledge = computed(() => record(groupDetailData.value.knowledge));
const groupStyle = computed(() => record(groupDetailData.value.style));
const groupAgentState = computed(() => record(groupDetailData.value["agent-state"]));

const aliasMemberId = ref("");
const aliasText = ref("");
const scheduleText = ref("");
const scheduleEnabled = ref(false);

watch(() => groupDetailData.value.schedule, (scheduleRaw) => {
  const s = record(scheduleRaw);
  if (typeof s.schedule_prompt === "string") scheduleText.value = s.schedule_prompt;
  if (typeof s.enabled === "boolean") scheduleEnabled.value = s.enabled;
}, { immediate: true });

const groupActionMutation = useMutation({
  mutationFn: async (action: string) => {
    if (action === "knowledge" || action === "style") return resources.rebuildGroup(detailGroupId.value, action);
    if (action === "alias-save") return resources.saveGroupAliases(detailGroupId.value, aliasMemberId.value, aliasText.value);
    if (action === "alias-delete") return resources.deleteGroupAliases(detailGroupId.value, aliasMemberId.value);
    if (action === "schedule-save") return resources.saveGroupSchedule(detailGroupId.value, scheduleEnabled.value, scheduleText.value);
    return resources.generateGroupSchedule(detailGroupId.value);
  },
  onSuccess: () => { void groupDetailQuery.refetch(); },
});

function confirmDeleteAlias() {
  if (window.confirm(`确认删除群 ${detailGroupId.value} 中成员 ${aliasMemberId.value} 的别名？`)) {
    groupActionMutation.mutate("alias-delete");
  }
}
function confirmRebuildKnowledge() {
  if (window.confirm(`确认重建群 ${detailGroupId.value} 的知识？`)) groupActionMutation.mutate("knowledge");
}
function confirmRebuildStyle() {
  if (window.confirm(`确认重建群 ${detailGroupId.value} 的风格？`)) groupActionMutation.mutate("style");
}

// 表情包操作
const selectedStickerParam = computed(() => String(route.query.sticker ?? ""));
const stickerUploadFile = ref<File | null>(null);
const stickerUploadDesc = ref("");
const stickerEditDesc = ref("");
const stickerMoodTags = ref("");
const stickerSceneTags = ref("");

const stickerItemQuery = useQuery({
  queryKey: computed(() => ["sticker-selected", selectedStickerParam.value]),
  queryFn: ({ signal }) => resources.stickers(1, 20, selectedStickerParam.value, signal),
  enabled: computed(() => Boolean(selectedStickerParam.value && props.dataset === "stickers")),
});

const selectedStickerItem = computed(() => stickerItemQuery.data.value?.items.find((it) => it.filename === selectedStickerParam.value));

watch(selectedStickerItem, (it) => {
  if (it) {
    stickerEditDesc.value = it.description || "";
    stickerMoodTags.value = it.mood_tags.join(", ");
    stickerSceneTags.value = it.scene_tags.join(", ");
  }
}, { immediate: true });

function onStickerFileChange(e: Event) {
  const files = (e.target as HTMLInputElement).files;
  stickerUploadFile.value = files?.[0] ?? null;
}

const stickerMutation = useMutation({
  mutationFn: async (action: "upload" | "save" | "delete" | "rescan" | "rebuild") => {
    if (action === "upload") {
      if (!stickerUploadFile.value) throw new Error("file_required");
      return resources.uploadSticker(stickerUploadFile.value, stickerUploadDesc.value);
    }
    if (action === "save") {
      return resources.updateSticker(selectedStickerParam.value, {
        description: stickerEditDesc.value,
        mood_tags: stickerMoodTags.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
        scene_tags: stickerSceneTags.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      });
    }
    if (action === "delete") return resources.deleteSticker(selectedStickerParam.value);
    if (action === "rescan") return resources.rescanStickers();
    return resources.rebuildStickerIndex();
  },
  onSuccess: () => {
    void queryClient.invalidateQueries({ queryKey: ["management-data", "stickers"] });
    void stickerItemQuery.refetch();
  },
});

function confirmDeleteSticker() {
  if (window.confirm(`确认将 ${selectedStickerParam.value} 移到回收目录？`)) {
    stickerMutation.mutate("delete");
  }
}
</script>
