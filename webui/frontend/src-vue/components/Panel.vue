<template>
  <section class="panel">
    <header v-if="hasHeading" class="panel-heading">
      <div v-if="hasHeadingCopy" class="panel-heading-copy">
        <div v-if="hasEyebrow" class="panel-eyebrow"><slot name="eyebrow">{{ eyebrow }}</slot></div>
        <h2 v-if="hasTitle" class="panel-title"><slot name="title">{{ title }}</slot></h2>
      </div>
      <div v-if="hasActions" class="panel-actions"><slot name="actions" /></div>
    </header>
    <div class="panel-body"><slot /></div>
  </section>
</template>

<script setup lang="ts">
import { computed, useSlots } from "vue";

const props = defineProps<{ eyebrow?: string; title?: string }>();
const slots = useSlots();
const hasEyebrow = computed(() => Boolean(props.eyebrow || slots.eyebrow));
const hasTitle = computed(() => Boolean(props.title || slots.title));
const hasHeadingCopy = computed(() => hasEyebrow.value || hasTitle.value);
const hasActions = computed(() => Boolean(slots.actions));
const hasHeading = computed(() => hasHeadingCopy.value || hasActions.value);
</script>
