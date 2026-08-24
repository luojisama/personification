import { useSyncExternalStore } from "react";

export const THEME_STORAGE_KEY = "personification.console.theme";
export const THEMES = ["minimal", "schale", "prts"] as const;
export type ThemeName = (typeof THEMES)[number];

const listeners = new Set<() => void>();

export const THEME_META: Record<
  ThemeName,
  { name: string; description: string; signal: string }
> = {
  minimal: {
    name: "Minimal",
    description: "中性高密度，适合长时间排障与批量核对。",
    signal: "朱砂标记",
  },
  schale: {
    name: "Schale",
    description: "明亮蓝白，使用轻量纸片层级区分任务。",
    signal: "学院蓝",
  },
  prts: {
    name: "PRTS",
    description: "深色工程面板，以青色状态线标识实时信号。",
    signal: "监控青",
  },
};

export function isThemeName(value: unknown): value is ThemeName {
  return typeof value === "string" && THEMES.includes(value as ThemeName);
}

export function getTheme(): ThemeName {
  const current = document.documentElement.dataset.theme;
  return isThemeName(current) ? current : "minimal";
}

export function getStoredTheme(storage: Pick<Storage, "getItem"> = window.localStorage): ThemeName {
  try {
    const value = storage.getItem(THEME_STORAGE_KEY);
    return isThemeName(value) ? value : "minimal";
  } catch {
    return "minimal";
  }
}

export function setTheme(
  theme: ThemeName,
  storage: Pick<Storage, "setItem"> = window.localStorage,
): void {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme === "prts" ? "dark" : "light";
  const themeColor = theme === "prts" ? "#101719" : theme === "schale" ? "#eef7ff" : "#eceae3";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", themeColor);
  try {
    storage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // 主题仍然应用到当前文档；禁用本地存储不应阻止界面使用。
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTheme(): [ThemeName, (theme: ThemeName) => void] {
  const theme = useSyncExternalStore<ThemeName>(subscribe, getTheme, () => "minimal");
  return [theme, setTheme];
}
