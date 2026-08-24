import { beforeEach, describe, expect, it } from "vitest";

import { getStoredTheme, getTheme, setTheme, THEME_STORAGE_KEY } from "./theme";

describe("主题状态", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.dataset.theme = "minimal";
  });

  it("仅接受三个已注册主题", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "unknown-theme");
    expect(getStoredTheme()).toBe("minimal");
    window.localStorage.setItem(THEME_STORAGE_KEY, "prts");
    expect(getStoredTheme()).toBe("prts");
  });

  it("写入文档属性与本地设置", () => {
    setTheme("schale");
    expect(getTheme()).toBe("schale");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("schale");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });
});
