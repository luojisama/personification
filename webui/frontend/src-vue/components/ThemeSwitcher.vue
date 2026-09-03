<template>
  <div v-if="compact" class="theme-switcher is-compact" role="region" aria-label="主题选择器">
    <button
      type="button"
      class="compact-theme-cycle"
      :aria-label="`当前${currentThemeName}主题，点击切换主题`"
      :title="`当前${currentThemeName}主题，点击切换`"
      @click="cycleTheme"
    >
      <span :class="['theme-swatch', `swatch-${themeStore.theme}`]" aria-hidden="true" />
    </button>
  </div>
  <div v-else class="theme-switcher" role="region" aria-label="主题选择器">
    <div class="theme-select-group" role="radiogroup" aria-label="界面风格主题">
      <button
        v-for="item in themes"
        :key="item.id"
        type="button"
        role="radio"
        :aria-checked="themeStore.theme === item.id"
        :class="['theme-option-btn', { active: themeStore.theme === item.id }]"
        :title="`${item.name}：${item.description}`"
        @click="themeStore.setTheme(item.id)"
      >
        <span :class="['theme-swatch', `swatch-${item.id}`]" aria-hidden="true" />
        <span>{{ item.name }}</span>
      </button>
    </div>

    <div class="theme-mode-panel">
      <div v-if="themeStore.theme === 'minimal'" class="color-mode-segmented" role="radiogroup" aria-label="色彩模式">
        <button
          v-for="mode in colorModes"
          :key="mode.id"
          type="button"
          role="radio"
          :aria-checked="themeStore.colorMode === mode.id"
          :class="['mode-option-btn', { active: themeStore.colorMode === mode.id }]"
          @click="themeStore.setColorMode(mode.id)"
        >
          {{ mode.label }}
        </button>
      </div>
      <div v-else-if="themeStore.theme === 'schale'" class="theme-mode-badge fixed-light">固定浅色模式</div>
      <div v-else class="theme-mode-badge fixed-dark">固定深色模式</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useThemeStore, type ColorMode, type ThemeName } from "@vue-app/stores/theme";

withDefaults(defineProps<{ compact?: boolean }>(), { compact: false });

const themeStore = useThemeStore();
const themes: readonly { id: ThemeName; name: string; description: string }[] = [
  { id: "minimal", name: "简约", description: "中性高密度，支持明暗与系统跟随" },
  { id: "schale", name: "夏莱", description: "明亮清透的蔚蓝学院终端" },
  { id: "prts", name: "PRTS", description: "高对比工业战术终端" },
];
const colorModes: readonly { id: ColorMode; label: string }[] = [
  { id: "system", label: "跟随系统" },
  { id: "light", label: "浅色" },
  { id: "dark", label: "深色" },
];
const currentThemeName = computed(() => themes.find((item) => item.id === themeStore.theme)?.name ?? "简约");

function cycleTheme(): void {
  const order: readonly ThemeName[] = ["minimal", "schale", "prts"];
  const index = order.indexOf(themeStore.theme);
  themeStore.setTheme(order[(index + 1) % order.length] ?? "minimal");
}
</script>
