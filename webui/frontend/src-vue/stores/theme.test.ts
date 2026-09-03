import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { COLOR_MODE_STORAGE_KEY, THEME_STORAGE_KEY, useThemeStore } from "./theme";

type ChangeCallback = (event: MediaQueryListEvent) => void;

function createMatchMediaMock(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners = new Set<ChangeCallback>();
  const mql: MediaQueryList = {
    matches,
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: vi.fn((type: string, listener: EventListenerOrEventListenerObject) => {
      if (type === "change" && typeof listener === "function") listeners.add(listener as ChangeCallback);
    }),
    removeEventListener: vi.fn((type: string, listener: EventListenerOrEventListenerObject) => {
      if (type === "change" && typeof listener === "function") listeners.delete(listener as ChangeCallback);
    }),
    addListener: vi.fn((listener: ChangeCallback) => listeners.add(listener)),
    removeListener: vi.fn((listener: ChangeCallback) => listeners.delete(listener)),
    dispatchEvent: vi.fn(),
  };

  const setMatches = (nextMatches: boolean) => {
    matches = nextMatches;
    (mql as { matches: boolean }).matches = nextMatches;
    const event = { matches: nextMatches, media: mql.media } as MediaQueryListEvent;
    listeners.forEach((callback) => callback(event));
  };

  return { mql, setMatches, getListenerCount: () => listeners.size };
}

describe("theme store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    window.localStorage.clear();
    document.documentElement.dataset.theme = "";
    document.documentElement.dataset.colorMode = "";
    document.documentElement.dataset.effectiveScheme = "";
    document.documentElement.style.colorScheme = "";
    if (!document.querySelector('meta[name="theme-color"]')) {
      const meta = document.createElement("meta");
      meta.setAttribute("name", "theme-color");
      meta.setAttribute("content", "#eceae3");
      document.head.appendChild(meta);
    }
  });

  it("默认状态为 minimal + system，并正确同步 DOM", () => {
    const { mql } = createMatchMediaMock(false);
    window.matchMedia = vi.fn().mockReturnValue(mql);
    const store = useThemeStore();
    store.init();
    expect(store.theme).toBe("minimal");
    expect(store.colorMode).toBe("system");
    expect(store.effectiveScheme).toBe("light");
    expect(document.documentElement.dataset.effectiveScheme).toBe("light");
    store.dispose();
  });

  it("持久化存储正确读取与写入", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "prts");
    window.localStorage.setItem(COLOR_MODE_STORAGE_KEY, "light");
    const { mql } = createMatchMediaMock(false);
    window.matchMedia = vi.fn().mockReturnValue(mql);
    const store = useThemeStore();
    store.init();
    expect(store.theme).toBe("prts");
    expect(store.effectiveScheme).toBe("dark");
    store.setTheme("minimal");
    store.setColorMode("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("minimal");
    expect(window.localStorage.getItem(COLOR_MODE_STORAGE_KEY)).toBe("dark");
    store.dispose();
  });

  it("Schale 固定为 light，PRTS 固定为 dark", () => {
    const { mql } = createMatchMediaMock(true);
    window.matchMedia = vi.fn().mockReturnValue(mql);
    const store = useThemeStore();
    store.init();
    store.setColorMode("dark");
    store.setTheme("schale");
    expect(store.effectiveScheme).toBe("light");
    store.setColorMode("light");
    store.setTheme("prts");
    expect(store.effectiveScheme).toBe("dark");
    store.dispose();
  });

  it("Minimal 主题下响应系统深色模式变化", () => {
    const { mql, setMatches } = createMatchMediaMock(false);
    window.matchMedia = vi.fn().mockReturnValue(mql);
    const store = useThemeStore();
    store.init();
    store.setTheme("minimal");
    store.setColorMode("system");
    setMatches(true);
    expect(store.systemIsDark).toBe(true);
    expect(store.effectiveScheme).toBe("dark");
    setMatches(false);
    expect(store.effectiveScheme).toBe("light");
    store.dispose();
  });

  it("重复调用 init 不会重复注册监听器，dispose 会注销监听器", () => {
    const { mql, getListenerCount } = createMatchMediaMock(false);
    window.matchMedia = vi.fn().mockReturnValue(mql);
    const store = useThemeStore();
    store.init();
    expect(getListenerCount()).toBe(1);
    store.init();
    expect(getListenerCount()).toBe(1);
    store.dispose();
    expect(getListenerCount()).toBe(0);
    expect(mql.removeEventListener).toHaveBeenCalled();
  });
});
