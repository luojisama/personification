<template>
  <FormField
    :label="label"
    :control-id="resolvedControlId"
    :description="description"
    :error="error"
    :hide-label="hideLabel"
    :required="required"
  >
    <template #default="{ controlId, describedBy, invalid }">
      <ComboboxRoot
        v-model="selectedValue"
        v-model:open="isOpen"
        class="searchable-select"
        :disabled="disabled"
        :required="required"
        :ignore-filter="true"
        :reset-model-value-on-clear="false"
        open-on-click
        open-on-focus
      >
        <ComboboxAnchor class="searchable-select-anchor">
          <ComboboxInput
            v-bind="attrs"
            :id="controlId"
            ref="inputElement"
            v-model="searchText"
            class="form-control searchable-select-input"
            type="text"
            :display-value="displayValue"
            :placeholder="placeholder"
            :disabled="disabled"
            :required="required"
            :aria-invalid="invalid ? 'true' : undefined"
            :aria-describedby="describedBy || undefined"
          />
          <ComboboxTrigger class="searchable-select-toggle" :disabled="disabled" :aria-label="`${label}选项`">
            <span aria-hidden="true">⌄</span>
          </ComboboxTrigger>
        </ComboboxAnchor>

        <ComboboxPortal>
          <ComboboxContent
            :id="listboxId"
            position="popper"
            class="searchable-select-options"
            :side-offset="6"
            :collision-padding="8"
            :aria-label="`${label}选项`"
          >
            <ComboboxViewport class="searchable-select-viewport">
              <ComboboxItem
                v-for="option in filteredOptions"
                :key="option.value"
                class="searchable-select-option"
                :value="option.value"
                :disabled="option.disabled"
              >
                <span>{{ option.label }}</span>
                <small v-if="option.description">{{ option.description }}</small>
              </ComboboxItem>
              <ComboboxEmpty class="searchable-select-empty">没有匹配的选项</ComboboxEmpty>
            </ComboboxViewport>
          </ComboboxContent>
        </ComboboxPortal>
      </ComboboxRoot>
    </template>
  </FormField>
</template>

<script setup lang="ts">
import {
  ComboboxAnchor,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxPortal,
  ComboboxRoot,
  ComboboxTrigger,
  ComboboxViewport,
} from "reka-ui";
import { computed, ref, useAttrs, useId, watch } from "vue";

import FormField from "./FormField.vue";

defineOptions({ inheritAttrs: false });

export interface SearchableSelectOption {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
}

const props = withDefaults(defineProps<{
  modelValue: string;
  options: readonly SearchableSelectOption[];
  label: string;
  id?: string;
  description?: string;
  error?: string;
  hideLabel?: boolean;
  required?: boolean;
  disabled?: boolean;
  placeholder?: string;
}>(), {
  id: undefined,
  description: "",
  error: "",
  hideLabel: false,
  required: false,
  disabled: false,
  placeholder: "搜索或选择",
});

const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const attrs = useAttrs();
const generatedId = useId();
const resolvedControlId = props.id ?? `searchable-select-${generatedId}`;
const listboxId = `${resolvedControlId}-options`;
const inputElement = ref<InstanceType<typeof ComboboxInput> | null>(null);
const isOpen = ref(false);
const searchText = ref("");

const selectedValue = computed({
  get: () => props.modelValue,
  set: (value: string) => emit("update:modelValue", value),
});
const selectedOption = computed(() => props.options.find((option) => option.value === props.modelValue) ?? null);
const filteredOptions = computed(() => {
  const normalizedQuery = searchText.value.trim().toLocaleLowerCase("zh-CN");
  const normalizedSelectedLabel = selectedOption.value?.label.toLocaleLowerCase("zh-CN") ?? "";
  if (!normalizedQuery || normalizedQuery === normalizedSelectedLabel) return props.options.filter((option) => !option.disabled);
  return props.options.filter((option) => !option.disabled && [option.label, option.description ?? "", option.value]
    .join(" ")
    .toLocaleLowerCase("zh-CN")
    .includes(normalizedQuery));
});

function displayValue(value: unknown): string {
  if (typeof value !== "string") return "";
  return props.options.find((option) => option.value === value)?.label ?? "";
}

watch(selectedOption, (option) => {
  if (!isOpen.value) searchText.value = option?.label ?? "";
}, { immediate: true });

defineExpose({
  focus: () => inputElement.value?.$el?.focus(),
});
</script>
