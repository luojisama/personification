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
      <input
        v-bind="attrs"
        :id="controlId"
        class="form-control"
        type="number"
        :value="modelValue ?? ''"
        :min="min"
        :max="max"
        :step="step"
        :placeholder="placeholder"
        :disabled="disabled"
        :required="required"
        inputmode="decimal"
        :aria-invalid="invalid ? 'true' : undefined"
        :aria-describedby="describedBy || undefined"
        @input="updateValue"
      />
    </template>
  </FormField>
</template>

<script setup lang="ts">
import { useAttrs, useId } from "vue";

import FormField from "./FormField.vue";

defineOptions({ inheritAttrs: false });

const props = withDefaults(defineProps<{
  modelValue: number | null;
  label: string;
  id?: string;
  description?: string;
  error?: string;
  hideLabel?: boolean;
  required?: boolean;
  disabled?: boolean;
  min?: number;
  max?: number;
  step?: number | "any";
  placeholder?: string;
}>(), {
  id: undefined,
  description: "",
  error: "",
  hideLabel: false,
  required: false,
  disabled: false,
  min: undefined,
  max: undefined,
  step: 1,
  placeholder: undefined,
});

const emit = defineEmits<{ "update:modelValue": [value: number | null] }>();
const attrs = useAttrs();
const generatedId = useId();
const resolvedControlId = props.id ?? `number-${generatedId}`;

function updateValue(event: Event): void {
  const rawValue = (event.target as HTMLInputElement).value;
  if (rawValue === "") {
    emit("update:modelValue", null);
    return;
  }
  const parsedValue = Number(rawValue);
  if (Number.isFinite(parsedValue)) emit("update:modelValue", parsedValue);
}
</script>
