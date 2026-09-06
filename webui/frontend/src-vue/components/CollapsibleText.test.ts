import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import CollapsibleText from "./CollapsibleText.vue";

describe("CollapsibleText", () => {
  it("默认折叠长文本并能展开全文", async () => {
    const text = "记忆".repeat(200);
    const wrapper = mount(CollapsibleText, { props: { text, limit: 20 } });
    expect(wrapper.text()).toContain("展开全文");
    expect(wrapper.find(".collapsible-text-preview").text()).not.toContain(text);
    await wrapper.get("button").trigger("click");
    expect(wrapper.text()).toContain(text);
    expect(wrapper.text()).toContain("收起");
  });
});
