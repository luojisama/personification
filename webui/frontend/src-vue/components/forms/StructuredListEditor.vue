<template>
  <FormField
    :label="label"
    :control-id="resolvedControlId"
    :description="description"
    :error="error"
    :hide-label="hideLabel"
    :required="required"
    group
  >
    <template #default="{ controlId, labelledBy, describedBy, invalid }">
      <div :id="controlId" class="structured-list-editor" role="group" :aria-labelledby="labelledBy" :aria-describedby="describedBy || undefined">
        <ol v-if="modelValue.length" class="structured-list-editor-items">
          <li v-for="(item, index) in modelValue" :key="`${index}-${item}`">
            <label class="sr-only" :for="itemControlId(index)">{{ `${label}第 ${index + 1} 项` }}</label>
            <input
              :id="itemControlId(index)"
              class="form-control"
              type="text"
              :value="item"
              :aria-invalid="invalid ? 'true' : undefined"
              :aria-describedby="describedBy || undefined"
              @input="updateItem(index, ($event.target as HTMLInputElement).value)"
            />
            <button class="button button-secondary" type="button" :aria-label="`删除${label}第 ${index + 1} 项`" @click="removeItem(index)">{{ removeLabel }}</button>
          </li>
        </ol>
        <p v-else class="structured-list-editor-empty">{{ emptyLabel }}</p>
        <button class="button button-secondary" type="button" @click="addItem">{{ addLabel }}</button>
      </div>
    </template>
  </FormField>
</template>

<script setup lang="ts">
import { nextTick, useId } from "vue";

import FormField from "./FormField.vue";

const props = withDefaults(defineProps<{
  modelValue: readonly string[];
  label: string;
  id?: string;
  description?: string;
  error?: string;
  hideLabel?: boolean;
  required?: boolean;
  addLabel?: string;
  removeLabel?: string;
  emptyLabel?: string;
}>(), {
  id: undefined,
  description: "",
  error: "",
  hideLabel: false,
  required: false,
  addLabel: "添加一项",
  removeLabel: "删除",
  emptyLabel: "暂未添加内容",
});

const emit = defineEmits<{ "update:modelValue": [value: string[]] }>();
const generatedId = useId();
const resolvedControlId = props.id ?? `structured-list-${generatedId}`;

function itemControlId(index: number): string {
  return `${resolvedControlId}-item-${index}`;
}

function updateItem(index: number, value: string): void {
  const nextItems = [...props.modelValue];
  nextItems[index] = value;
  emit("update:modelValue", nextItems);
}

function removeItem(index: number): void {
  emit("update:modelValue", props.modelValue.filter((_, itemIndex) => itemIndex !== index));
}

async function addItem(): Promise<void> {
  const nextIndex = props.modelValue.length;
  emit("update:modelValue", [...props.modelValue, ""]);
  await nextTick();
  document.getElementById(itemControlId(nextIndex))?.focus();
}
</script>
