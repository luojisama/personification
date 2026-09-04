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
      <label class="switch-field-control" :for="controlId">
        <input
          v-bind="attrs"
          :id="controlId"
          class="switch-field-input"
          type="checkbox"
          role="switch"
          :checked="modelValue"
          :disabled="disabled"
          :required="required"
          :aria-checked="modelValue"
          :aria-invalid="invalid ? 'true' : undefined"
          :aria-describedby="describedBy || undefined"
          @change="emit('update:modelValue', ($event.target as HTMLInputElement).checked)"
        />
        <span class="switch-field-track" aria-hidden="true"><span class="switch-field-thumb" /></span>
        <span v-if="stateLabel" class="switch-field-state">{{ stateLabel }}</span>
      </label>
    </template>
  </FormField>
</template>

<script setup lang="ts">
import { computed, useAttrs, useId } from "vue";

import FormField from "./FormField.vue";

defineOptions({ inheritAttrs: false });

const props = withDefaults(defineProps<{
  modelValue: boolean;
  label: string;
  id?: string;
  description?: string;
  error?: string;
  hideLabel?: boolean;
  required?: boolean;
  disabled?: boolean;
  onLabel?: string;
  offLabel?: string;
}>(), {
  id: undefined,
  description: "",
  error: "",
  hideLabel: false,
  required: false,
  disabled: false,
  onLabel: "已开启",
  offLabel: "已关闭",
});

const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();
const attrs = useAttrs();
const generatedId = useId();
const resolvedControlId = props.id ?? `switch-${generatedId}`;
const stateLabel = computed(() => props.modelValue ? props.onLabel : props.offLabel);
</script>
