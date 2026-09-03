import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import ThemeSwitcher from "./ThemeSwitcher.vue";
import { useThemeStore } from "@vue-app/stores/theme";

describe("ThemeSwitcher", () => {
  beforeEach(() => {
    const pinia = createPinia();
    setActivePinia(pinia);
    window.localStorage.clear();
  });

  it("提供三套主题，并仅在简约主题显示三种色彩模式", async () => {
    const wrapper = mount(ThemeSwitcher);
    const store = useThemeStore();
    store.init();
    expect(wrapper.findAll(".theme-option-btn")).toHaveLength(3);
    expect(wrapper.text()).toContain("简约");
    expect(wrapper.text()).toContain("夏莱");
    expect(wrapper.text()).toContain("PRTS");
    expect(wrapper.findAll(".mode-option-btn")).toHaveLength(3);

    await wrapper.findAll(".theme-option-btn")[1]!.trigger("click");
    expect(store.theme).toBe("schale");
    expect(store.effectiveScheme).toBe("light");
    expect(wrapper.text()).toContain("固定浅色模式");
    expect(wrapper.find(".color-mode-segmented").exists()).toBe(false);

    await wrapper.findAll(".theme-option-btn")[2]!.trigger("click");
    expect(store.theme).toBe("prts");
    expect(store.effectiveScheme).toBe("dark");
    expect(wrapper.text()).toContain("固定深色模式");
    store.dispose();
  });

  it("折叠模式用单个按钮循环主题", async () => {
    const wrapper = mount(ThemeSwitcher, { props: { compact: true } });
    const store = useThemeStore();
    expect(wrapper.findAll("button")).toHaveLength(1);
    await wrapper.get(".compact-theme-cycle").trigger("click");
    expect(store.theme).toBe("schale");
    await wrapper.get(".compact-theme-cycle").trigger("click");
    expect(store.theme).toBe("prts");
  });
});
