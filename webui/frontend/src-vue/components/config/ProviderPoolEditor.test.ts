import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { resources } from "@/api/resources";
import ProviderPoolEditor from "./ProviderPoolEditor.vue";

vi.mock("@/api/resources", () => ({
  resources: { providerModels: vi.fn() },
}));

const baseProvider = {
  provider_id: "gemini-main",
  name: "Gemini",
  api_type: "gemini",
  api_key: "***",
  _secret_ref: "stable-secret-reference",
  default_model_id: "gemini-flash",
  purpose_models: { main: "gemini-flash" },
  models: [
    { model_id: "gemini-flash", display_name: "Flash", enabled: true, context_window_tokens: 1_050_000 },
    { model_id: "gemini-pro", display_name: "Pro", enabled: true },
  ],
};

function mountEditor(value: unknown = [baseProvider]) {
  return mount(ProviderPoolEditor, { props: { id: "providers", label: "模型 Provider 池", modelValue: value } });
}

describe("ProviderPoolEditor", () => {
  it("保留稳定凭据引用，并以供应商 ID 而不是列表位置组织模型", () => {
    const wrapper = mountEditor();
    expect((wrapper.get("#providers-provider-0-provider-id").element as HTMLInputElement).value).toBe("gemini-main");
    expect(wrapper.text()).toContain("gemini-flash");
    expect(wrapper.text()).toContain("主回复");
    expect(wrapper.html()).not.toContain("stable-secret-reference");
  });

  it("阻止删除正在被默认或用途引用的模型", async () => {
    const wrapper = mountEditor();
    await wrapper.findAll("button").find(button => button.text() === "删除模型")!.trigger("click");
    expect(wrapper.emitted("update:error")?.at(-1)?.[0]).toContain("仍被默认模型或用途选择器引用");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  });

  it("删除未引用模型时保留其它模型、用途和供应商凭据引用", async () => {
    const wrapper = mountEditor();
    const deleteButtons = wrapper.findAll("button").filter(button => button.text() === "删除模型");
    await deleteButtons[1]!.trigger("click");
    const emitted = wrapper.emitted("update:modelValue")?.at(-1)?.[0] as Array<Record<string, unknown>>;
    expect(emitted[0]?._secret_ref).toBe("stable-secret-reference");
    expect((emitted[0]?.models as Array<Record<string, unknown>>).map(model => model.model_id)).toEqual(["gemini-flash"]);
    expect((emitted[0]?.purpose_models as Record<string, string>).main).toBe("gemini-flash");
  });

  it("模型发现只显示候选，必须显式勾选后才添加", async () => {
    vi.mocked(resources.providerModels).mockResolvedValue({
      models: [{ id: "gemini-new", name: "New" }, { id: "gemini-other", name: "Other" }],
    });
    const wrapper = mountEditor();
    await wrapper.findAll("button").find(button => button.text() === "获取可用模型")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("探测到的候选模型");
    expect(wrapper.text()).toContain("gemini-new");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    const checks = wrapper.findAll('input[type="checkbox"]');
    await checks[0]!.setValue(true);
    await wrapper.findAll("button").find(button => button.text().startsWith("添加选中的"))!.trigger("click");
    const emitted = wrapper.emitted("update:modelValue")?.at(-1)?.[0] as Array<Record<string, unknown>>;
    expect((emitted[0]?.models as Array<Record<string, unknown>>).map(model => model.model_id)).toContain("gemini-new");
    expect((emitted[0]?.models as Array<Record<string, unknown>>).map(model => model.model_id)).not.toContain("gemini-other");
  });

  it("新增供应商使用非序号稳定 ID，即使删除后再次新增也不会复用", async () => {
    const wrapper = mountEditor([]);
    await wrapper.get("button").trigger("click");
    const first = (wrapper.emitted("update:modelValue")?.at(-1)?.[0] as Array<Record<string, unknown>>)[0]?.provider_id;
    await wrapper.setProps({ modelValue: [] });
    await wrapper.get("button").trigger("click");
    const second = (wrapper.emitted("update:modelValue")?.at(-1)?.[0] as Array<Record<string, unknown>>)[0]?.provider_id;
    expect(String(first)).toMatch(/^provider_[a-z0-9]+$/);
    expect(second).not.toBe(first);
  });
});
