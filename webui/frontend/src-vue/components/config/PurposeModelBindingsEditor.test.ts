import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { resources } from "@/api/resources";
import PurposeModelBindingsEditor from "./PurposeModelBindingsEditor.vue";

vi.mock("@/api/resources", () => ({ resources: { config: vi.fn() } }));

describe("PurposeModelBindingsEditor", () => {
  it("以供应商 ID 和模型 ID（而不是同名模型）产生跨供应商用途绑定", async () => {
    vi.mocked(resources.config).mockResolvedValue({
      items: [{ field_name: "personification_api_pools", value: [
        { provider_id: "first", name: "供应商一", models: [{ model_id: "flash", display_name: "Flash" }] },
        { provider_id: "second", name: "供应商二", models: [{ model_id: "flash", display_name: "Flash" }] },
      ] }], page: 1, page_size: 100, total: 1, total_pages: 1,
    } as never);
    const wrapper = mount(PurposeModelBindingsEditor, { props: { id: "bindings", label: "用途模型绑定", modelValue: {} } });
    await flushPromises();
    const input = wrapper.get("#bindings-main") as ReturnType<typeof wrapper.get>;
    await input.trigger("focus");
    await input.setValue("供应商二／Flash");
    await input.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("update:modelValue")?.at(-1)?.[0]).toEqual({ main: { provider_id: "second", model_id: "flash" } });
  });

  it("目录读取失败时保留绑定而不把它清空", async () => {
    vi.mocked(resources.config).mockRejectedValue(new Error("offline"));
    const wrapper = mount(PurposeModelBindingsEditor, { props: { id: "bindings", label: "用途模型绑定", modelValue: { vision: { provider_id: "a", model_id: "v" } } } });
    await flushPromises();
    expect(wrapper.text()).toContain("当前绑定已保留");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  });

  it("逐用途显示明确绑定、严格轻量继承和已知能力不兼容", async () => {
    vi.mocked(resources.config).mockResolvedValue({
      items: [
        { field_name: "personification_api_pools", value: [{ provider_id: "p", name: "P", models: [
          { model_id: "main", capabilities: { function_call: true } },
          { model_id: "vision-text-only", capabilities: { image_input: false } },
        ] }] },
        { field_name: "personification_strict_main_model", value: true },
      ], page: 1, page_size: 100, total: 2, total_pages: 1,
    } as never);
    const wrapper = mount(PurposeModelBindingsEditor, {
      props: { id: "bindings", label: "用途模型绑定", modelValue: {
        main: { provider_id: "p", model_id: "main" },
        lite: { provider_id: "p", model_id: "ignored-lite" },
        vision: { provider_id: "p", model_id: "vision-text-only" },
      } },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("明确绑定：p／main");
    expect(wrapper.text()).toContain("严格主模型模式已开启");
    expect(wrapper.text()).toContain("图片输入");
  });

  it("用途状态以遗留阶段覆盖优先，并将画像旧字段标为回退设置而非确定路由", async () => {
    vi.mocked(resources.config).mockImplementation(async (_page, _size, filters) => ({
      items: filters?.search === "personification_model_overrides"
        ? [{ field_name: "personification_model_overrides", value: { agent: "override-agent" } }]
        : filters?.search === "personification_persona_model"
          ? [{ field_name: "personification_persona_model", value: "legacy-persona" }]
          : filters?.search === "personification_api_pools"
            ? [{ field_name: "personification_api_pools", value: [{ provider_id: "p", default_model_id: "m", models: [{ model_id: "m" }] }] }]
            : [], page: 1, page_size: 10, total: 1, total_pages: 1,
    } as never));
    const wrapper = mount(PurposeModelBindingsEditor, { props: { id: "bindings", label: "用途模型绑定", modelValue: { labeler: { provider_id: "p", model_id: "m" } } } });
    await flushPromises();
    expect(wrapper.text()).toContain("沿用遗留阶段覆盖 “override-agent”");
    expect(wrapper.text()).toContain("已配置旧项 “legacy-persona”；实际仍继承已有路由／回退设置");
    expect(wrapper.text()).toContain("图片输入能力尚未经验证");
  });
});
