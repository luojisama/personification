<template>
  <div class="form-field" :class="{ 'has-error': Boolean(error) }">
    <component :is="group ? 'span' : 'label'" :id="labelId" :for="group ? undefined : resolvedControlId" :class="{ 'sr-only': hideLabel }">
      <span>{{ label }}</span><span v-if="required" class="form-required" aria-hidden="true"> *</span>
    </component>
    <p v-if="description" :id="descriptionId" class="form-description">{{ description }}</p>
    <slot :control-id="resolvedControlId" :labelled-by="labelId" :described-by="describedBy" :invalid="Boolean(error)" />
    <p v-if="error" :id="errorId" class="form-error" role="alert">{{ error }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, useId } from "vue";

const props = withDefaults(defineProps<{
  label: string;
  controlId?: string;
  description?: string;
  error?: string;
  hideLabel?: boolean;
  required?: boolean;
  group?: boolean;
}>(), {
  controlId: undefined,
  description: "",
  error: "",
  hideLabel: false,
  required: false,
  group: false,
});

const generatedId = useId();
const resolvedControlId = computed(() => props.controlId ?? `field-${generatedId}`);
const labelId = computed(() => `${resolvedControlId.value}-label`);
const descriptionId = computed(() => `${resolvedControlId.value}-description`);
const errorId = computed(() => `${resolvedControlId.value}-error`);
const describedBy = computed(() => [
  props.description ? descriptionId.value : "",
  props.error ? errorId.value : "",
].filter(Boolean).join(" "));
</script>
