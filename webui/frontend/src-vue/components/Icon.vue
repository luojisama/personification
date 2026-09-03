<template>
  <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <template v-if="name === 'overview'"><path d="M3 4h7v7H3zM14 4h7v4h-7zM14 12h7v8h-7zM3 15h7v5H3z" /></template>
    <template v-else-if="name === 'route'"><circle cx="5" cy="6" r="2" /><circle cx="19" cy="6" r="2" /><circle cx="12" cy="18" r="2" /><path d="M7 6h10M6.5 8l4.2 8M17.5 8l-4.2 8" /></template>
    <template v-else-if="name === 'trace'"><path d="M5 3v18M5 7h6l2 3h6M5 14h5l2 3h7" /><circle cx="19" cy="10" r="1.5" /><circle cx="19" cy="17" r="1.5" /></template>
    <template v-else-if="name === 'recovery'"><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.6" /><path d="M4 4v4.6h4.6M12 8v5l3 2" /></template>
    <template v-else-if="name === 'settings'"><path d="M9.3 3.6 10 2h4l.7 1.6 1.7.7 1.6-.6 2.3 2.3-.6 1.6.7 1.7L22 10v4l-1.6.7-.7 1.7.6 1.6-2.3 2.3-1.6-.6-1.7.7L14 22h-4l-.7-1.6-1.7-.7-1.6.6L3.7 18l.6-1.6-.7-1.7L2 14v-4l1.6-.7.7-1.7L3.7 6 6 3.7l1.6.6z" /><circle cx="12" cy="12" r="3" /></template>
    <template v-else-if="name === 'data'"><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></template>
    <template v-else-if="name === 'system'"><rect x="3" y="4" width="18" height="12" rx="1" /><path d="M8 20h8M12 16v4M7 9h3M14 9h3" /></template>
    <template v-else-if="name === 'signal'"><path d="M4 18V13M9.3 18V9M14.7 18V5M20 18V2" /></template>
    <template v-else-if="name === 'search'"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></template>
    <template v-else-if="name === 'refresh'"><path d="M20 7V3l-2 2a8 8 0 1 0 1.2 11" /></template>
    <template v-else-if="name === 'chevron'"><path d="m9 5 7 7-7 7" /></template>
    <template v-else-if="name === 'check'"><path d="m4 12 5 5L20 6" /></template>
    <template v-else-if="name === 'close'"><path d="M5 5l14 14M19 5 5 19" /></template>
    <template v-else-if="name === 'clock'"><circle cx="12" cy="12" r="9" /><path d="M12 7v6l4 2" /></template>
    <template v-else-if="name === 'tool'"><path d="m14.5 5.5 4-2-2 4 2 2 2-1-1 4-4 1-1-2-6.5 6.5-2-2 6.5-6.5-2-2-4 1 1-4 4-1z" /></template>
    <template v-else-if="name === 'shield'"><path d="M12 2 20 5v6c0 5-3.5 8.5-8 11-4.5-2.5-8-6-8-11V5z" /><path d="m8 12 2.5 2.5L16 9" /></template>
    <template v-else><circle cx="12" cy="12" r="9" /><path d="M9.8 9a2.4 2.4 0 1 1 3 2.3c-.8.3-.8 1.1-.8 1.7M12 17h.01" /></template>
  </svg>
</template>

<script setup lang="ts">
export type IconName = "overview" | "route" | "trace" | "recovery" | "settings" | "data" | "system" | "signal" | "search" | "refresh" | "chevron" | "check" | "close" | "unknown" | "clock" | "tool" | "shield";
defineProps<{ name: IconName }>();
</script>
