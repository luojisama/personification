import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { tokenBillingApi } from "@/api/tokenBilling";
import TokenPriceEditor from "./TokenPriceEditor.vue";

vi.mock("@/api/tokenBilling", () => ({
  tokenBillingApi: { routes: vi.fn(), prices: vi.fn(), createPrice: vi.fn() },
}));

describe("TokenPriceEditor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(tokenBillingApi.routes).mockResolvedValue({
      items: [{ route_id: "route-a", name: "主路由", models: ["model-a"] }],
    });
    vi.mocked(tokenBillingApi.prices).mockResolvedValue({ items: [] });
    vi.mocked(tokenBillingApi.createPrice).mockImplementation(
      async (input) => ({ ...input, version_id: "version-new" }),
    );
  });

  it("以空模型和可空 decimal 字符串创建不可变价格版本", async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const wrapper = mount(TokenPriceEditor, {
      global: { plugins: [[VueQueryPlugin, { queryClient }]] },
    });
    await vi.waitFor(() => expect(wrapper.text()).toContain("主路由"));
    const selects = wrapper.findAll("select");
    await selects[0]!.setValue("route-a");
    const inputs = wrapper.findAll("input");
    await inputs[3]!.setValue("2.50");
    await wrapper.find("form").trigger("submit");
    await vi.waitFor(() =>
      expect(tokenBillingApi.createPrice).toHaveBeenCalled(),
    );
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("已创建不可变价格版本 version-new"),
    );
    expect(
      vi.mocked(tokenBillingApi.createPrice).mock.calls[0]?.[0],
    ).toMatchObject({
      route_id: "route-a",
      model: "",
      currency: "USD",
      input_per_million: "2.50",
      output_per_million: null,
    });
    wrapper.unmount();
    queryClient.clear();
  });

  it("保存结果未知时要求刷新核对且不宣称成功", async () => {
    vi.mocked(tokenBillingApi.createPrice).mockRejectedValueOnce(
      new Error("unknown"),
    );
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const wrapper = mount(TokenPriceEditor, {
      global: { plugins: [[VueQueryPlugin, { queryClient }]] },
    });
    await vi.waitFor(() => expect(wrapper.text()).toContain("主路由"));
    await wrapper.findAll("select")[0]!.setValue("route-a");
    await wrapper.find("form").trigger("submit");
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("服务器未确认保存结果"),
    );
    expect(wrapper.text()).not.toContain("已创建不可变价格版本");
    wrapper.unmount();
    queryClient.clear();
  });

  it("拒绝负数或非十进制价格", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = mount(TokenPriceEditor, {
      global: { plugins: [[VueQueryPlugin, { queryClient }]] },
    });
    await vi.waitFor(() => expect(wrapper.text()).toContain("主路由"));
    await wrapper.findAll("select")[0]!.setValue("route-a");
    await wrapper.findAll("input")[3]!.setValue("-1");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.text()).toContain("价格必须是非负十进制字符串");
    expect(tokenBillingApi.createPrice).not.toHaveBeenCalled();
    wrapper.unmount();
    queryClient.clear();
  });
});
