<template>
  <span :class="avatarClass" :aria-label="label" role="img">
    <img v-if="src && !imageFailed" :src="src" alt="" loading="lazy" referrerpolicy="no-referrer" @error="imageFailed = true" />
    <span v-else>{{ fallback }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";

const props = withDefaults(defineProps<{
  src?: string | null;
  label: string;
  size?: "small" | "normal" | "large";
  square?: boolean;
}>(), { src: null, size: "normal", square: false });

const imageFailed = ref(false);
watch(() => props.src, () => { imageFailed.value = false; });
const fallback = computed(() => Array.from(props.label.trim())[0] ?? "?");
const avatarClass = computed(() => [
  "identity-avatar",
  `identity-avatar-${props.size}`,
  { "identity-avatar-square": props.square },
]);
</script>
