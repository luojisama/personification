<script setup lang="ts">
import { Sheet, SheetContent } from "@vue-app/components/ui/sheet";
import { useSidebar } from "./utils";
defineOptions({ inheritAttrs: false });
const { isMobile, openMobile, setOpenMobile } = useSidebar();
</script>
<template><Sheet v-if="isMobile" :open="openMobile" @update:open="setOpenMobile"><SheetContent v-bind="$attrs" aria-label="管理台导航" @click="($event.target as Element).closest('a') && setOpenMobile(false)"><slot /></SheetContent></Sheet><aside v-else v-bind="$attrs" data-slot="sidebar"><slot /></aside></template>
