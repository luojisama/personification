<template>
  <header class="page-heading">
    <div class="page-title-block">
      <span class="page-kicker">ADMIN / {{ index }}</span>
      <h1>{{ title }}</h1>
      <p v-if="description">{{ description }}</p>
    </div>
    <div v-if="hasActions" class="page-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed, useSlots } from "vue";

defineProps<{
  index: string;
  title: string;
  description?: string;
}>();

const slots = useSlots();
const hasActions = computed(() => Boolean(slots.actions));
</script>
