import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import Pagination from "./Pagination.vue";

describe("Pagination", () => {
  it("提供首页、末页与键盘跳页，并限制页码范围", async () => {
    const wrapper = mount(Pagination, { props: { page: 3, totalPages: 8, total: 151 } });
    await wrapper.get('button[aria-label="首页"]').trigger("click");
    expect(wrapper.emitted("update:page")?.[0]).toEqual([1]);
    await wrapper.get('button[aria-label="末页"]').trigger("click");
    expect(wrapper.emitted("update:page")?.[1]).toEqual([8]);
    await wrapper.get("input").setValue("99");
    await wrapper.get("input").trigger("keydown.enter");
    expect(wrapper.emitted("update:page")?.[2]).toEqual([8]);
  });

  it("在总页数减少时通知父级钳制页码，禁用时不重复派发", async () => {
    const wrapper = mount(Pagination, { props: { page: 4, totalPages: 8 } });
    await wrapper.setProps({ totalPages: 2 });
    expect(wrapper.emitted("update:page")?.[0]).toEqual([2]);
    await wrapper.setProps({ page: 4, disabled: true });
    await wrapper.get("input").trigger("blur");
    expect(wrapper.emitted("update:page")).toHaveLength(1);
  });
});
