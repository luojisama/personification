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
        <select v-if="bots.length > 1 && !railCollapsed" class="bot-selector" aria-label="选择 Bot" :value="selectedBot?.bot_id ?? ''" @change="onBotChange">
          <option v-for="bot in bots" :key="bot.bot_id" :value="bot.bot_id">{{ bot.nickname }} · {{ bot.bot_id }}</option>
        </select>
      </div>

      <div class="global-page-search rail-expandable">
        <Icon name="search" />
        <input v-model="searchQuery" placeholder="搜索页面或功能" aria-label="搜索页面或功能" @keydown.esc="searchQuery = ''" />
        <div v-if="searchResults.length" class="page-search-results" role="listbox" aria-label="页面搜索结果">
          <button v-for="item in searchResults" :key="item.id" type="button" role="option" @click="visit(item.path || '/runtime/overview/summary')">
            <Icon :name="item.icon" />
            <span>{{ item.label }}</span>
            <small>{{ item.aliases[0] }}</small>
          </button>
        </div>
      </div>

      <nav class="primary-nav" aria-label="一级分类">
        <button
          v-for="group in NAVIGATION_GROUPS"
          :key="group.id"
          type="button"
          :class="{ active: currentContext?.group.id === group.id }"
          :aria-current="currentContext?.group.id === group.id ? 'page' : undefined"
          :title="railCollapsed ? group.label : undefined"
          @click="visitGroup(group.id, group.path)"
        >
          <Icon :name="group.icon" />
          <span class="nav-label rail-expandable">{{ group.label }}</span>
        </button>
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
        <span v-if="realtime.resyncCount.value">REST 重同步 {{ realtime.resyncCount.value }} 次</span>
        <code>{{ route.path }}</code>
      </header>

      <div v-if="currentContext" class="workspace-navigation">
        <nav class="secondary-navigation" :aria-label="`${currentContext.group.label}二级导航`">
          <RouterLink v-for="item in currentContext.group.children" :key="item.id" :to="item.path || '#'" :class="{ active: currentContext.page.id === item.id }">{{ item.label }}</RouterLink>
        </nav>
        <nav class="tertiary-navigation" :aria-label="`${currentContext.page.label}三级导航`">
          <span class="workspace-breadcrumb">{{ currentContext.group.label }} / {{ currentContext.page.label }}</span>
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
import { NAVIGATION_GROUPS, NAVIGATION_ITEMS, NAVIGATION_LEAVES, navigationContext, type NavigationNode } from "@/app/navigation";
import Icon from "@vue-app/components/Icon.vue";
import IdentityAvatar from "@vue-app/components/IdentityAvatar.vue";
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
const bots = computed<BotIdentity[]>(() => botsQuery.data.value?.items ?? []);
const selectedBot = computed(() => resolveSelectedBot(bots.value, botStore.selectedBotId));
const currentContext = computed(() => navigationContext(route.path));

watch([bots, () => botStore.selectedBotId], ([currentBots, selectedId]) => {
  const resolved = resolveSelectedBot(currentBots, selectedId);
  if (resolved && resolved.bot_id !== selectedId) botStore.setBotId(resolved.bot_id);
}, { immediate: true });

watch(() => currentContext.value?.leaf.path, (leafPath) => {
  const groupId = currentContext.value?.group.id;
  if (!leafPath || !groupId) return;
  try { window.localStorage.setItem(`personification.nav.last.${groupId}`, leafPath); } catch { /* 页面路径记忆失败不影响导航 */ }
});

watch(() => route.fullPath, () => {
  drawerOpen.value = false;
});

function onBotChange(event: Event): void {
  botStore.setBotId((event.target as HTMLSelectElement).value);
}

function toggleRail(): void {
  uiStore.toggleSidebar();
  try { window.localStorage.setItem("personification.nav.collapsed", railCollapsed.value ? "1" : "0"); } catch { /* 页面内状态仍然有效 */ }
}

function visit(path: string): void {
  searchQuery.value = "";
  drawerOpen.value = false;
  void router.push(path);
}

function visitGroup(groupId: string, fallback: string | null): void {
  let remembered = "";
  try { remembered = window.localStorage.getItem(`personification.nav.last.${groupId}`) ?? ""; } catch { /* 使用默认入口 */ }
  visit(remembered || fallback || "/runtime/overview/summary");
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
  return [group?.label ?? "", page.label, item.label, ...page.aliases, ...item.aliases].join(" ");
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
