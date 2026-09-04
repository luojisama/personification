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
      <div class="searchable-select">
        <input
          v-bind="attrs"
          :id="controlId"
          ref="inputElement"
          class="form-control searchable-select-input"
          type="text"
          role="combobox"
          autocomplete="off"
          :value="searchText"
          :placeholder="placeholder"
          :disabled="disabled"
          :required="required"
          :aria-autocomplete="'list'"
          :aria-controls="listboxId"
          :aria-expanded="isOpen"
          :aria-activedescendant="activeOptionId"
          :aria-invalid="invalid ? 'true' : undefined"
          :aria-describedby="describedBy || undefined"
          @input="onInput"
          @focus="openOptions"
          @blur="closeAfterBlur"
          @keydown="onKeydown"
        />
        <button
          class="searchable-select-toggle"
          type="button"
          :disabled="disabled"
          :aria-label="`${label}选项`"
          :aria-expanded="isOpen"
          :aria-controls="listboxId"
          @mousedown.prevent
          @click="toggleOptions"
        >
          <span aria-hidden="true">⌄</span>
        </button>
        <ul v-if="isOpen" :id="listboxId" class="searchable-select-options" role="listbox" :aria-label="`${label}选项`">
          <li
            v-for="(option, index) in filteredOptions"
            :id="optionId(index)"
            :key="option.value"
            :class="{ active: index === activeIndex, selected: option.value === modelValue }"
            role="option"
            :aria-selected="option.value === modelValue"
            @mousedown.prevent
            @click="chooseOption(option)"
          >
            <span>{{ option.label }}</span>
            <small v-if="option.description">{{ option.description }}</small>
          </li>
          <li v-if="!filteredOptions.length" class="searchable-select-empty" role="status">没有匹配的选项</li>
        </ul>
      </div>
    </template>
  </FormField>
</template>

<script setup lang="ts">
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
const inputElement = ref<HTMLInputElement | null>(null);
const isOpen = ref(false);
const activeIndex = ref(0);
const searchText = ref("");

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
const activeOptionId = computed(() => isOpen.value && filteredOptions.value[activeIndex.value]
  ? optionId(activeIndex.value)
  : undefined);

watch(selectedOption, (option) => {
  if (!isOpen.value) searchText.value = option?.label ?? "";
}, { immediate: true });

watch(filteredOptions, (options) => {
  if (!options.length) {
    activeIndex.value = 0;
  } else if (activeIndex.value >= options.length) {
    activeIndex.value = options.length - 1;
  }
});

function optionId(index: number): string {
  return `${listboxId}-${index}`;
}

function openOptions(): void {
  if (props.disabled) return;
  isOpen.value = true;
  const selectedIndex = filteredOptions.value.findIndex((option) => option.value === props.modelValue);
  activeIndex.value = selectedIndex >= 0 ? selectedIndex : 0;
}

function toggleOptions(): void {
  if (isOpen.value) {
    isOpen.value = false;
    searchText.value = selectedOption.value?.label ?? "";
    inputElement.value?.focus();
    return;
  }
  openOptions();
  inputElement.value?.focus();
}

function onInput(event: Event): void {
  searchText.value = (event.target as HTMLInputElement).value;
  isOpen.value = true;
  activeIndex.value = 0;
}

function closeAfterBlur(): void {
  window.setTimeout(() => {
    isOpen.value = false;
    searchText.value = selectedOption.value?.label ?? "";
  }, 0);
}

function chooseOption(option: SearchableSelectOption): void {
  if (option.disabled) return;
  emit("update:modelValue", option.value);
  searchText.value = option.label;
  isOpen.value = false;
  inputElement.value?.focus();
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (!isOpen.value) openOptions();
    if (filteredOptions.value.length) activeIndex.value = (activeIndex.value + 1) % filteredOptions.value.length;
    return;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    if (!isOpen.value) openOptions();
    if (filteredOptions.value.length) activeIndex.value = (activeIndex.value - 1 + filteredOptions.value.length) % filteredOptions.value.length;
    return;
  }
  if (event.key === "Enter" && isOpen.value) {
    const activeOption = filteredOptions.value[activeIndex.value];
    if (activeOption) {
      event.preventDefault();
      chooseOption(activeOption);
    }
    return;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    isOpen.value = false;
    searchText.value = selectedOption.value?.label ?? "";
  }
  if (event.key === "Tab") isOpen.value = false;
}

defineExpose({
  focus: () => inputElement.value?.focus(),
});
</script>
