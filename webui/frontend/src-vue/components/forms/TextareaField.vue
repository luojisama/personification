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
      <textarea
        v-bind="attrs"
        :id="controlId"
        class="form-control"
        :value="modelValue"
        :rows="rows"
        :placeholder="placeholder"
        :autocomplete="autocomplete"
        :spellcheck="spellcheck"
        :disabled="disabled"
        :required="required"
        :aria-invalid="invalid ? 'true' : undefined"
        :aria-describedby="describedBy || undefined"
        @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
      />
    </template>
  </FormField>
</template>

<script setup lang="ts">
import { useAttrs, useId } from "vue";

import FormField from "./FormField.vue";

defineOptions({ inheritAttrs: false });

const props = withDefaults(defineProps<{
  modelValue?: string;
  label: string;
  id?: string;
  description?: string;
  error?: string;
  hideLabel?: boolean;
  required?: boolean;
  disabled?: boolean;
  placeholder?: string;
  autocomplete?: string;
  rows?: number;
  spellcheck?: boolean;
}>(), {
  modelValue: "",
  id: undefined,
  description: "",
  error: "",
  hideLabel: false,
  required: false,
  disabled: false,
  placeholder: undefined,
  autocomplete: undefined,
  rows: 3,
  spellcheck: undefined,
});

const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const attrs = useAttrs();
const generatedId = useId();
const resolvedControlId = props.id ?? `textarea-${generatedId}`;
</script>
