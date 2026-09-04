import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { describe, expect, it } from "vitest";

import ConfirmDialog from "@vue-app/components/ConfirmDialog.vue";
import Drawer from "@vue-app/components/Drawer.vue";
import NumberField from "./NumberField.vue";
import SearchableSelect from "./SearchableSelect.vue";
import SelectField from "./SelectField.vue";
import StructuredListEditor from "./StructuredListEditor.vue";
import SwitchField from "./SwitchField.vue";
import TextareaField from "./TextareaField.vue";
import TextField from "./TextField.vue";

describe("通用表单控件的可访问性合同", () => {
  it("文本、数字与下拉字段使用稳定标签、说明和错误关联", async () => {
    const textField = mount(TextField, {
      props: {
        modelValue: "原值",
        label: "显示名称",
        id: "display-name",
        description: "用于管理台展示",
        error: "请输入显示名称",
      },
    });
    const textInput = textField.get("input");
    expect(textField.get("label").attributes("for")).toBe("display-name");
    expect(textInput.attributes("aria-invalid")).toBe("true");
    expect(textInput.attributes("aria-describedby")).toBe("display-name-description display-name-error");
    await textInput.setValue("新名称");
    expect(textField.emitted("update:modelValue")?.[0]).toEqual(["新名称"]);

    const numberField = mount(NumberField, { props: { modelValue: 1, label: "重试次数", id: "retry-count" } });
    await numberField.get("input").setValue("2.5");
    expect(numberField.emitted("update:modelValue")?.[0]).toEqual([2.5]);

    const selectField = mount(SelectField, {
      props: {
        modelValue: "unknown",
        label: "结果状态",
        id: "outcome",
        error: "未知结果不能当作成功",
        options: [{ value: "unknown", label: "未知" }, { value: "ok", label: "成功" }],
      },
    });
    expect(selectField.get("select").attributes("aria-invalid")).toBe("true");
    expect(selectField.get("[role=alert]").text()).toBe("未知结果不能当作成功");

    const textareaField = mount(TextareaField, {
      props: {
        modelValue: "初始说明",
        label: "详细说明",
        id: "detail-copy",
        description: "请填写可见说明",
        error: "详细说明不能为空",
        rows: 5,
      },
    });
    const textarea = textareaField.get("textarea");
    expect(textarea.attributes("aria-invalid")).toBe("true");
    expect(textarea.attributes("aria-describedby")).toBe("detail-copy-description detail-copy-error");
    expect(textarea.attributes("rows")).toBe("5");
    await textarea.setValue("更新说明");
    expect(textareaField.emitted("update:modelValue")?.[0]).toEqual(["更新说明"]);
  });

  it("可搜索下拉框保留组合框语义，并支持方向键和回车选择", async () => {
    const wrapper = mount(SearchableSelect, {
      props: {
        modelValue: "primary",
        label: "选择 Bot",
        id: "bot-choice",
        options: [
          { value: "primary", label: "测试主号" },
          { value: "secondary", label: "测试副号", description: "未连接" },
        ],
      },
    });
    const input = wrapper.get("input[role=combobox]");
    expect(input.attributes("aria-controls")).toBe("bot-choice-options");
    await input.trigger("focus");
    expect(input.attributes("aria-expanded")).toBe("true");
    await input.trigger("keydown", { key: "ArrowDown" });
    expect(wrapper.get("#bot-choice-options-1").classes()).toContain("active");
    await input.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["secondary"]);
    expect(input.attributes("aria-expanded")).toBe("false");
  });

  it("开关与结构化列表保留键盘可达的原生控件和逐项标签", async () => {
    const switchField = mount(SwitchField, { props: { modelValue: false, label: "启用同步", id: "sync-enabled" } });
    const switchInput = switchField.get("input[role=switch]");
    expect(switchField.get("label").attributes("for")).toBe("sync-enabled");
    expect(switchInput.attributes("aria-checked")).toBe("false");
    await switchInput.setValue(true);
    expect(switchField.emitted("update:modelValue")?.[0]).toEqual([true]);

    const listEditor = mount(StructuredListEditor, { props: { modelValue: ["第一项"], label: "允许命令", id: "command-list" } });
    expect(listEditor.get("[role=group]").attributes("aria-labelledby")).toBe("command-list-label");
    expect(listEditor.get("label.sr-only").attributes("for")).toBe("command-list-item-0");
    await listEditor.get("input").setValue("更新项");
    expect(listEditor.emitted("update:modelValue")?.[0]).toEqual([["更新项"]]);
    await listEditor.get(".structured-list-editor > button").trigger("click");
    expect(listEditor.emitted("update:modelValue")?.[1]).toEqual([["第一项", ""]]);
  });

  it("确认对话框和侧边栏有对话框语义、初始焦点与 Escape 关闭", async () => {
    const trigger = document.createElement("button");
    document.body.append(trigger);
    trigger.focus();
    const dialog = mount(ConfirmDialog, {
      attachTo: document.body,
      props: { open: false, title: "确认清理", description: "此操作不可撤销", dangerous: true },
    });
    await dialog.setProps({ open: true });
    await nextTick();
    expect(dialog.get("[role=dialog]").attributes("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(dialog.get(".button-secondary").element);
    await dialog.get("[role=dialog]").trigger("keydown", { key: "Escape" });
    expect(dialog.emitted("update:open")?.[0]).toEqual([false]);
    expect(dialog.emitted("cancel")).toHaveLength(1);
    dialog.unmount();

    const drawer = mount(Drawer, { attachTo: document.body, props: { open: false, title: "筛选条件" } });
    await drawer.setProps({ open: true });
    await nextTick();
    expect(drawer.get("[role=dialog]").attributes("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(drawer.get(".drawer-close").element);
    await drawer.get(".drawer-panel").trigger("keydown", { key: "Escape" });
    expect(drawer.emitted("update:open")?.[0]).toEqual([false]);
    drawer.unmount();
    trigger.remove();
  });
});
