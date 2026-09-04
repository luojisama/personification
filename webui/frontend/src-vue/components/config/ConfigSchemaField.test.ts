import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import type { ConfigListItem } from "@/api/types";
import ConfigSchemaField from "./ConfigSchemaField.vue";

function configItem(overrides: Partial<ConfigListItem> = {}): ConfigListItem {
  return {
    key: "unrelated_field",
    field_name: "unrelated_field",
    display_name: "测试配置",
    description: "用于验证注册表 UI schema。",
    group: "测试",
    category: "test",
    scope: "global",
    kind: "string",
    value_type: "str",
    value: "",
    default: "",
    secret: false,
    advanced: false,
    hot_reloadable: true,
    restart_required: false,
    required: false,
    modified: false,
    aliases: [],
    choices: [],
    min_value: null,
    max_value: null,
    ...overrides,
  };
}

describe("ConfigSchemaField.vue", () => {
  it("依据 ui_schema 渲染开关，而不是从字段名或 value_type 猜测", () => {
    const wrapper = mount(ConfigSchemaField, {
      props: {
        item: configItem({ value_type: "str", ui_schema: { control_kind: "switch" } }),
        modelValue: false,
      },
    });

    expect(wrapper.find('input[role="switch"]').exists()).toBe(true);
    expect(wrapper.find("input[type=\"text\"]").exists()).toBe(false);
  });

  it("按已知 key_value schema 使用结构化字段并回传对象", async () => {
    const wrapper = mount(ConfigSchemaField, {
      props: {
        item: configItem({
          value_type: "dict",
          ui_schema: {
            control_kind: "key_value",
            item_schema: {
              fields: [
                { key: "key", label: "名称", control_kind: "text", required: true },
                { key: "value", label: "说明", control_kind: "textarea" },
              ],
            },
          },
        }),
        modelValue: { casual: "轻松回应" },
      },
    });

    expect(wrapper.find("textarea").exists()).toBe(true);
    await wrapper.get("#config-unrelated_field-item-0-key").setValue("serious");
    const emitted = wrapper.emitted("update:modelValue");
    expect(emitted?.at(-1)?.[0]).toEqual({ serious: "轻松回应" });
  });
});
