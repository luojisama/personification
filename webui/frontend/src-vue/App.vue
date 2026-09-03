<template>
  <div id="app-root" class="app-root">
    <AppShell>
      <RouterView />
    </AppShell>
  </div>
</template>

<script setup lang="ts">
import { RouterView } from "vue-router";

import AppShell from "@vue-app/components/AppShell.vue";
import "@vue-app/styles/tokens.css";
import "@vue-app/styles/base.css";
import "@vue-app/styles/layout.css";
import "@vue-app/styles/components.css";
import "@vue-app/styles/pages.css";
</script>

<style scoped>
.app-root {
  min-height: 100vh;
}
</style>
