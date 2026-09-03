import { defineStore } from "pinia";
import { computed, ref } from "vue";

export type ThemeName = "minimal" | "schale" | "prts";
export type ColorMode = "system" | "light" | "dark";
export type EffectiveScheme = "light" | "dark";

export const THEME_STORAGE_KEY = "personification.console.theme";
export const COLOR_MODE_STORAGE_KEY = "personification.console.color-mode";

const VALID_THEMES: readonly ThemeName[] = ["minimal", "schale", "prts"];
const VALID_COLOR_MODES: readonly ColorMode[] = ["system", "light", "dark"];

const THEME_HEADER_COLORS: Record<ThemeName, { light: string; dark: string }> = {
  minimal: { light: "#eceae3", dark: "#171717" },
  schale: { light: "#f4f9fd", dark: "#f4f9fd" },
  prts: { light: "#0b0f14", dark: "#0b0f14" },
};

function readStorage<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw && (allowed as readonly string[]).includes(raw)) {
      return raw as T;
    }
  } catch {
    /* 本地存储不可用时使用降级值 */
  }
  return fallback;
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* 忽略本地存储写入失败 */
  }
}

function readSystemDark(): boolean {
  try {
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      return window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
  } catch {
    /* 缺少 matchMedia 默认返回 false */
  }
  return false;
}

export const useThemeStore = defineStore("theme", () => {
  const theme = ref<ThemeName>(readStorage(THEME_STORAGE_KEY, VALID_THEMES, "minimal"));
  const colorMode = ref<ColorMode>(readStorage(COLOR_MODE_STORAGE_KEY, VALID_COLOR_MODES, "system"));
  const systemIsDark = ref<boolean>(readSystemDark());

  let mediaQueryList: MediaQueryList | null = null;
  let mediaQueryListener: ((event: MediaQueryListEvent) => void) | null = null;
  let initialized = false;

  const effectiveScheme = computed<EffectiveScheme>(() => {
    if (theme.value === "schale") return "light";
    if (theme.value === "prts") return "dark";
    if (colorMode.value === "dark") return "dark";
    if (colorMode.value === "light") return "light";
    return systemIsDark.value ? "dark" : "light";
  });

  function applyDomState(): void {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    const currentTheme = theme.value;
    const currentMode = colorMode.value;
    const currentScheme = effectiveScheme.value;

    root.dataset.theme = currentTheme;
    root.dataset.colorMode = currentMode;
    root.dataset.effectiveScheme = currentScheme;
    root.style.colorScheme = currentScheme;

    const metaThemeColor = document.querySelector('meta[name="theme-color"]');
    if (metaThemeColor) {
      metaThemeColor.setAttribute("content", THEME_HEADER_COLORS[currentTheme][currentScheme]);
    }
  }

  function setTheme(nextTheme: ThemeName): void {
    if (!VALID_THEMES.includes(nextTheme)) return;
    theme.value = nextTheme;
    writeStorage(THEME_STORAGE_KEY, nextTheme);
    applyDomState();
  }

  function setColorMode(nextMode: ColorMode): void {
    if (!VALID_COLOR_MODES.includes(nextMode)) return;
    colorMode.value = nextMode;
    writeStorage(COLOR_MODE_STORAGE_KEY, nextMode);
    applyDomState();
  }

  function handleMediaChange(event: MediaQueryListEvent): void {
    systemIsDark.value = Boolean(event.matches);
    applyDomState();
  }

  function init(): void {
    if (initialized) {
      applyDomState();
      return;
    }
    initialized = true;
    systemIsDark.value = readSystemDark();

    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      try {
        mediaQueryList = window.matchMedia("(prefers-color-scheme: dark)");
        mediaQueryListener = (event: MediaQueryListEvent) => handleMediaChange(event);
        if (typeof mediaQueryList.addEventListener === "function") {
          mediaQueryList.addEventListener("change", mediaQueryListener);
        } else if (typeof (mediaQueryList as unknown as { addListener: (cb: unknown) => void }).addListener === "function") {
          (mediaQueryList as unknown as { addListener: (cb: (e: MediaQueryListEvent) => void) => void }).addListener(mediaQueryListener);
        }
      } catch {
        mediaQueryList = null;
        mediaQueryListener = null;
      }
    }

    applyDomState();
  }

  function dispose(): void {
    if (mediaQueryList && mediaQueryListener) {
      try {
        if (typeof mediaQueryList.removeEventListener === "function") {
          mediaQueryList.removeEventListener("change", mediaQueryListener);
        } else if (typeof (mediaQueryList as unknown as { removeListener: (cb: unknown) => void }).removeListener === "function") {
          (mediaQueryList as unknown as { removeListener: (cb: (e: MediaQueryListEvent) => void) => void }).removeListener(mediaQueryListener);
        }
      } catch {
        /* 忽略清理异常 */
      }
    }
    mediaQueryList = null;
    mediaQueryListener = null;
    initialized = false;
  }

  return {
    theme,
    colorMode,
    systemIsDark,
    effectiveScheme,
    setTheme,
    setColorMode,
    init,
    dispose,
    applyDomState,
  };
});
