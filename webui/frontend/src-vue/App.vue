<template>
  <div id="app-root" class="app-root">
    <AppShell v-if="auth.phase === 'authenticated'">
      <RouterView />
    </AppShell>
    <AuthGate v-else />
  </div>
</template>

<script setup lang="ts">
import { watch } from "vue";
import { RouterView } from "vue-router";

import { queryClient } from "@vue-app/app/queryClient";
import AppShell from "@vue-app/components/AppShell.vue";
import AuthGate from "@vue-app/components/AuthGate.vue";
import { useRuntimeEvents } from "@vue-app/realtime/runtimeEvents";
import { useAuthStore } from "@vue-app/stores/auth";
import { useBotStore } from "@vue-app/stores/bot";
import { useCurrentGroupStore } from "@vue-app/stores/currentGroup";
import "@vue-app/styles/tokens.css";
import "@vue-app/styles/shadcn.css";
import "@vue-app/styles/base.css";
import "@vue-app/styles/layout.css";
import "@vue-app/styles/components.css";
import "@vue-app/styles/pages.css";

const auth = useAuthStore();
const realtime = useRuntimeEvents();
const botStore = useBotStore();
const currentGroupStore = useCurrentGroupStore();

watch(() => auth.phase, (phase, previous) => {
  if (phase === "authenticated") {
    realtime.start();
    return;
  }
  realtime.stop();
  if (previous === "authenticated") {
    queryClient.clear();
    botStore.setBotId("");
    currentGroupStore.clear();
    const url = new URL(window.location.href);
    url.search = "";
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.hash}`);
  }
}, { immediate: true });
</script>

<style scoped>
.app-root {
  min-height: 100vh;
}
</style>
