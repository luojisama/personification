import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { RecoveryItem } from "@/api/types";
import RecoveryPage from "@vue-app/pages/RecoveryPage.vue";

vi.mock("@/api/resources", () => ({
  resources: {
    recovery: vi.fn(),
    abandonRecovery: vi.fn(),
    retryRecovery: vi.fn(),
  },
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({
    params: { section: "queue" },
    query: { page: "1" },
  }),
  useRouter: () => ({
    replace: vi.fn(),
  }),
}));

vi.mock("@tanstack/vue-query", () => {
  const { ref } = require("vue");
  return {
    useQuery: ({ queryFn }: any) => {
      const data = ref(null);
      const isPending = ref(true);
      const error = ref(null);
      Promise.resolve(queryFn({ signal: undefined }))
        .then((res: any) => {
          data.value = res;
          isPending.value = false;
        })
        .catch((err: any) => {
          error.value = err;
          isPending.value = false;
        });
      return { data, isPending, error };
    },
    useMutation: ({ mutationFn, onSuccess }: any) => {
      return {
        mutate: (arg: any) => {
          Promise.resolve(mutationFn(arg)).then((res: any) => {
            if (onSuccess) onSuccess(res);
          });
        },
      };
    },
    useQueryClient: () => ({
      invalidateQueries: vi.fn(),
    }),
  };
});

describe("RecoveryPage.vue", () => {
  const mockItems: RecoveryItem[] = [
    {
      id: 42,
      bot_id: "bot_1",
      session_type: "group",
      session_id: "group_101",
      message_id: "msg_abc1234567890",
      safe_summary: "用户询问天气",
      failure_class: "delivery_unknown",
      failure_stage: "outbound_send",
      status: "quarantined",
      attempts: 1,
      first_failed_at: "2025-01-01T12:00:00Z",
      last_failed_at: "2025-01-01T12:00:00Z",
      expires_at: "2025-01-02T12:00:00Z",
      trace_id: "trace_999888777",
      outcome_unknown: true,
      missing_segments: [1, 2],
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(resources.recovery).mockResolvedValue({
      items: mockItems,
      page: 1,
      page_size: 20,
      total: 1,
      total_pages: 1,
    });
  });

  it("renders quarantined recovery item with unknown warnings and retry action", async () => {
    const wrapper = mount(RecoveryPage);
    await flushPromises();

    expect(wrapper.text()).toContain("恢复队列");
    expect(wrapper.text()).toContain("用户询问天气");
    expect(wrapper.text()).toContain("发送结果未知：已禁止自动恢复。");
    expect(wrapper.text()).toContain("部分发送缺失分段：1、2");
    expect(wrapper.find("button.button-primary").exists()).toBe(true);
    expect(wrapper.find("button.button-danger").exists()).toBe(true);
  });

  it("prompts confirmation before mutation on retry", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(resources.retryRecovery).mockResolvedValue({
      ok: true,
      code: "retry_queued",
      phase: "retry",
      title: "已提交重试",
      message: "正在使用最新上下文生成新回复",
      retryable: false,
      partial: false,
      outcome_unknown: false,
      warnings: [],
      steps: [],
    });

    const wrapper = mount(RecoveryPage);
    await flushPromises();

    const retryBtn = wrapper.find("button.button-primary");
    await retryBtn.trigger("click");

    expect(confirmSpy).toHaveBeenCalled();
    expect(resources.retryRecovery).toHaveBeenCalledWith(42);
  });
});
