<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <button class="mobile-nav-trigger" type="button" :aria-expanded="drawerOpen" aria-controls="admin-navigation" @click="drawerOpen = !drawerOpen">
    <Icon :name="drawerOpen ? 'close' : 'data'" /> 菜单
  </button>
  <button v-if="drawerOpen" class="drawer-scrim" type="button" aria-label="关闭导航" @click="drawerOpen = false" />

  <div :class="['app-frame', { 'rail-collapsed': railCollapsed }]">
    <aside id="admin-navigation" :class="['evidence-rail', { 'is-open': drawerOpen }]" aria-label="管理台一级导航">
      <div class="brand-plate">
        <IdentityAvatar :src="selectedBot?.avatar_url" :label="selectedBot?.nickname || 'P/F'" size="large" square />
        <div class="brand-copy rail-expandable">
          <strong>{{ selectedBot?.nickname || "拟人插件" }}</strong>
          <small>{{ selectedBot?.bot_id ? `QQ ${selectedBot.bot_id}` : "事件取证台" }}</small>
        </div>
        <div v-if="bots.length > 1 && !railCollapsed" class="bot-selector">
          <SearchableSelect v-model="selectedBotId" label="选择 Bot" :options="botOptions" hide-label />
        </div>
      </div>

      <div class="global-page-search rail-expandable">
        <Icon name="search" />
        <TextField v-model="searchQuery" label="搜索页面或功能" type="search" placeholder="搜索页面或功能" hide-label @keydown.esc="searchQuery = ''" />
        <div v-if="searchResults.length" class="page-search-results" role="listbox" aria-label="页面搜索结果">
          <button v-for="item in searchResults" :key="item.id" type="button" role="option" @click="visit(item.path || '/runtime/overview/summary')">
            <Icon :name="item.icon" />
            <span>{{ item.label }}</span>
            <small>{{ item.aliases[0] }}</small>
          </button>
        </div>
      </div>

      <nav class="visual-section-nav" aria-label="导航分区">
        <button
          v-for="section in NAVIGATION_SECTIONS"
          :key="section.id"
          type="button"
          :class="{ active: currentContext?.section.id === section.id }"
          :aria-pressed="currentContext?.section.id === section.id"
          :title="railCollapsed ? section.label : undefined"
          @click="visitSection(section.id)"
        >
          <Icon :name="section.icon" />
          <span class="section-label">{{ section.label }}</span>
        </button>
      </nav>

      <div class="mobile-section-selector">
        <SelectField
          :model-value="currentSectionId"
          label="选择导航分区"
          :options="sectionOptions"
          @update:model-value="visitSection"
        />
      </div>

      <nav v-if="currentSection" class="section-page-nav" :aria-label="`${currentSection.label}页面`">
        <RouterLink
          v-for="item in currentSection.children"
          :key="item.id"
          :to="item.path || '/runtime/overview/summary'"
          :class="{ active: currentContext?.page.id === item.id }"
          :aria-current="currentContext?.page.id === item.id ? 'page' : undefined"
        >
          <Icon :name="item.icon" />
          <span class="nav-label rail-expandable">{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="rail-foot">
        <button class="rail-collapse" type="button" :title="railCollapsed ? '展开一级导航' : '收起一级导航'" @click="toggleRail">
          <Icon name="chevron" /><span class="rail-expandable">收起导航</span>
        </button>
        <a class="legacy-entry rail-expandable" href="/personification/">进入旧版管理台</a>
        <ThemeSwitcher :compact="railCollapsed" />
        <div class="rail-expandable">
          <StateBadge :tone="realtimeTone" :raw="realtime.state.value">{{ realtimeLabel }}</StateBadge>
        </div>
      </div>
    </aside>

    <div class="workbench">
      <header class="top-status-line">
        <span><Icon name="signal" /> 实时事件 {{ realtime.events.value.length }}/500</span>
        <span>{{ selectedBot?.online ? "Bot 在线" : "Bot 未连接" }}{{ selectedBot?.bot_id ? ` · ${selectedBot.bot_id}` : "" }}</span>
        <span v-if="adminIdentity">当前身份 {{ identitySourceLabel(adminIdentity.identity_source) }} · QQ {{ adminIdentity.qq }}</span>
        <span v-if="realtime.resyncCount.value">REST 重同步 {{ realtime.resyncCount.value }} 次</span>
        <code>{{ route.path }}</code>
      </header>

      <div v-if="currentContext" class="workspace-navigation">
        <nav class="secondary-navigation" :aria-label="`${currentContext.section.label}页面导航`">
          <RouterLink v-for="item in currentContext.section.children" :key="item.id" :to="item.path || '#'" :class="{ active: currentContext.page.id === item.id }">{{ item.label }}</RouterLink>
        </nav>
        <nav class="tertiary-navigation" :aria-label="`${currentContext.page.label}三级导航`">
          <span class="workspace-breadcrumb">{{ currentContext.section.label }} / {{ currentContext.page.label }}</span>
          <RouterLink
            v-for="item in currentContext.page.children"
            :key="item.id"
            :to="item.path || '#'"
            :class="{ active: currentContext.leaf.id === item.id }"
            :aria-current="currentContext.leaf.id === item.id ? 'page' : undefined"
          >{{ item.label }}</RouterLink>
        </nav>
      </div>

      <main id="main-content" class="main-workspace" tabindex="-1"><slot /></main>
    </div>
  </div>
  <div id="operation-live-region" class="sr-only" role="status" aria-live="polite" aria-atomic="true" />
</template>

<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { computed, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { resources } from "@/api/resources";
import type { BotIdentity } from "@/api/types";
import { NAVIGATION_GROUPS, NAVIGATION_ITEMS, NAVIGATION_LEAVES, NAVIGATION_SECTIONS, navigationContext, navigationSectionForPage, type NavigationNode } from "@/app/navigation";
import Icon from "@vue-app/components/Icon.vue";
import IdentityAvatar from "@vue-app/components/IdentityAvatar.vue";
import SearchableSelect from "@vue-app/components/forms/SearchableSelect.vue";
import SelectField from "@vue-app/components/forms/SelectField.vue";
import TextField from "@vue-app/components/forms/TextField.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import ThemeSwitcher from "@vue-app/components/ThemeSwitcher.vue";
import { useRuntimeEvents } from "@vue-app/realtime/runtimeEvents";
import { resolveSelectedBot, useBotStore } from "@vue-app/stores/bot";
import { useUiStore } from "@vue-app/stores/ui";

const route = useRoute();
const router = useRouter();
const botStore = useBotStore();
const uiStore = useUiStore();
const realtime = useRuntimeEvents();
const drawerOpen = ref(false);
const searchQuery = ref("");

try {
  uiStore.setSidebarCollapsed(window.localStorage.getItem("personification.nav.collapsed") === "1");
} catch {
  uiStore.setSidebarCollapsed(false);
}

const railCollapsed = computed(() => uiStore.sidebarCollapsed);
const botsQuery = useQuery({ queryKey: ["bots"], queryFn: ({ signal }) => resources.bots(signal), staleTime: 30_000 });
const adminIdentityQuery = useQuery({ queryKey: ["admin-identity"], queryFn: ({ signal }) => resources.adminIdentity(signal), staleTime: 60_000 });
const adminIdentity = computed(() => adminIdentityQuery.data.value ?? null);
function identitySourceLabel(source: "SUPERUSER" | "plugin_admin"): string {
  return source === "SUPERUSER" ? "SUPERUSER（NoneBot 超级用户）" : "plugin_admin（插件管理员）";
}
const bots = computed<BotIdentity[]>(() => botsQuery.data.value?.items ?? []);
const selectedBot = computed(() => resolveSelectedBot(bots.value, botStore.selectedBotId));
const selectedBotId = computed({
  get: () => selectedBot.value?.bot_id ?? botStore.selectedBotId,
  set: (botId: string) => botStore.setBotId(botId),
});
const botOptions = computed(() => bots.value.map((bot) => ({
  value: bot.bot_id,
  label: `${bot.nickname} · ${bot.bot_id}`,
  description: bot.online ? "在线" : "未连接",
})));
const currentContext = computed(() => navigationContext(route.path));
const currentSection = computed(() => currentContext.value?.section ?? null);
const currentSectionId = computed(() => currentSection.value?.id ?? "runtime-overview");
const sectionOptions = NAVIGATION_SECTIONS.map((section) => ({ value: section.id, label: section.label }));

watch([bots, () => botStore.selectedBotId], ([currentBots, selectedId]) => {
  const resolved = resolveSelectedBot(currentBots, selectedId);
  if (resolved && resolved.bot_id !== selectedId) botStore.setBotId(resolved.bot_id);
}, { immediate: true });

watch(() => currentContext.value?.leaf.path, (leafPath) => {
  const groupId = currentContext.value?.group.id;
  const sectionId = currentContext.value?.section.id;
  if (!leafPath || !groupId || !sectionId) return;
  try {
    window.localStorage.setItem(`personification.nav.last.${groupId}`, leafPath);
    window.localStorage.setItem(`personification.nav.last-section.${sectionId}`, leafPath);
  } catch {
    /* 页面路径记忆失败不影响导航 */
  }
});

watch(() => route.fullPath, () => {
  drawerOpen.value = false;
});

function toggleRail(): void {
  uiStore.toggleSidebar();
  try { window.localStorage.setItem("personification.nav.collapsed", railCollapsed.value ? "1" : "0"); } catch { /* 页面内状态仍然有效 */ }
}

function visit(path: string): void {
  searchQuery.value = "";
  drawerOpen.value = false;
  void router.push(path);
}

function visitSection(sectionId: string): void {
  const section = NAVIGATION_SECTIONS.find((item) => item.id === sectionId);
  if (!section) return;
  let remembered = "";
  try { remembered = window.localStorage.getItem(`personification.nav.last-section.${section.id}`) ?? ""; } catch { /* 使用默认入口 */ }
  const rememberedContext = remembered ? navigationContext(remembered) : null;
  visit(rememberedContext?.section.id === section.id ? remembered : section.default_path || "/runtime/overview/summary");
}

function compactSearchText(value: string): string {
  return value.toLocaleLowerCase("zh-CN").replace(/[\s/·_-]+/g, "");
}

function includesInOrder(haystack: string, needle: string): boolean {
  let cursor = 0;
  for (const character of haystack) {
    if (character === needle[cursor]) cursor += 1;
    if (cursor === needle.length) return true;
  }
  return needle.length === 0;
}

function navigationSearchText(item: NavigationNode): string {
  const parent = NAVIGATION_ITEMS.find((candidate) => candidate.children.some((child) => child.id === item.id));
  const page = parent ?? item;
  const group = NAVIGATION_GROUPS.find((candidate) => candidate.id === page.parent_id);
  const section = navigationSectionForPage(page);
  return [section?.label ?? "", ...(section?.aliases ?? []), group?.label ?? "", page.label, item.label, ...page.aliases, ...item.aliases].join(" ");
}

const searchResults = computed(() => {
  const needle = compactSearchText(searchQuery.value.trim());
  if (!needle) return [];
  return [...NAVIGATION_ITEMS, ...NAVIGATION_LEAVES].filter((item) => {
    const haystack = compactSearchText(navigationSearchText(item));
    return haystack.includes(needle) || includesInOrder(haystack, needle);
  }).slice(0, 10);
});

const realtimeTone = computed<"ok" | "error" | "running">(() =>
  realtime.state.value === "open" ? "ok" : realtime.state.value === "closed" ? "error" : "running",
);
const realtimeLabel = computed(() =>
  realtime.state.value === "open" ? "实时事件已连接" : realtime.state.value === "closed" ? "实时事件已关闭" : "正在连接事件流",
);
</script>
