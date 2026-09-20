<script setup lang="ts">
import type { DialogContentEmits, DialogContentProps } from "reka-ui";
import { DialogContent, DialogOverlay, DialogPortal, DialogTitle, VisuallyHidden, useForwardPropsEmits } from "reka-ui";
const props = defineProps<DialogContentProps>();
const emits = defineEmits<DialogContentEmits>();
const forwarded = useForwardPropsEmits(props, emits);
</script>
<template><DialogPortal><DialogOverlay class="sidebar-sheet-overlay" /><DialogContent class="sidebar-sheet-content" :aria-describedby="undefined" v-bind="{ ...forwarded, ...$attrs }"><VisuallyHidden><DialogTitle>管理台导航</DialogTitle></VisuallyHidden><slot /></DialogContent></DialogPortal></template>
