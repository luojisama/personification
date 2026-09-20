<script setup lang="ts">
import { useEventListener, useMediaQuery } from "@vueuse/core";
import { computed, ref } from "vue";
import { TooltipProvider } from "reka-ui";
import { provideSidebarContext } from "./utils";
defineOptions({ inheritAttrs: false });
const props = withDefaults(defineProps<{ open?: boolean; defaultOpen?: boolean }>(), { open: undefined, defaultOpen: true });
const emit = defineEmits<{ "update:open": [value: boolean] }>();
const uncontrolledOpen = ref(props.defaultOpen);
const open = computed({
  get: () => props.open ?? uncontrolledOpen.value,
  set: (value: boolean) => { if (props.open === undefined) uncontrolledOpen.value = value; emit("update:open", value); },
});
const openMobile = ref(false);
const isMobile = useMediaQuery("(max-width: 760px)");
const state = computed(() => open.value ? "expanded" : "collapsed");
function setOpen(value: boolean) { open.value = value; }
function setOpenMobile(value: boolean) { openMobile.value = value; }
function toggleSidebar() { isMobile.value ? setOpenMobile(!openMobile.value) : setOpen(!open.value); }
useEventListener("keydown", (event: KeyboardEvent) => {
  if (event.key.toLowerCase() === "b" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); toggleSidebar(); }
});
provideSidebarContext({ state, open, setOpen, isMobile, openMobile, setOpenMobile, toggleSidebar });
</script>
<template><TooltipProvider :delay-duration="0"><div v-bind="$attrs" class="sidebar-provider" data-slot="sidebar-wrapper" :data-state="state"><slot /></div></TooltipProvider></template>
