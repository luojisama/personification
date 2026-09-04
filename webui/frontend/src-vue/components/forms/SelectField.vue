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
      <select
        v-bind="attrs"
        :id="controlId"
        class="form-control"
        :value="modelValue"
        :disabled="disabled"
        :required="required"
        :aria-invalid="invalid ? 'true' : undefined"
        :aria-describedby="describedBy || undefined"
        @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
      >
        <option v-if="placeholder" value="" disabled>{{ placeholder }}</option>
        <option v-for="option in options" :key="option.value" :value="option.value" :disabled="option.disabled">{{ option.label }}</option>
      </select>
    </template>
  </FormField>
</template>

<script setup lang="ts">
import { useAttrs, useId } from "vue";

import FormField from "./FormField.vue";

defineOptions({ inheritAttrs: false });

export interface SelectFieldOption {
  value: string;
  label: string;
  disabled?: boolean;
}

const props = withDefaults(defineProps<{
  modelValue: string;
  options: readonly SelectFieldOption[];
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
  placeholder: undefined,
});

const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const attrs = useAttrs();
const generatedId = useId();
const resolvedControlId = props.id ?? `select-${generatedId}`;
</script>
