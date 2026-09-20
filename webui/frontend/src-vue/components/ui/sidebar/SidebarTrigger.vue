<script setup lang="ts">
import { useSidebar } from "./utils";
defineProps<{ label?: string }>();
const { openMobile, toggleSidebar } = useSidebar();
</script>
<template><button type="button" data-slot="sidebar-trigger" :aria-label="label" :aria-expanded="openMobile" @click="toggleSidebar"><slot /></button></template>
