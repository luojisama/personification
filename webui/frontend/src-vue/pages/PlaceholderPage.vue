<template>
  <div class="placeholder-page">
    <section class="placeholder-card" aria-labelledby="preview-title">
      <span class="placeholder-mark" aria-hidden="true">i</span>
      <h1 id="preview-title">{{ pageTitle }}</h1>
      <p>{{ pageDescription }}</p>
      <dl>
        <div>
          <dt>当前路由</dt>
          <dd><code>{{ route.fullPath }}</code></dd>
        </div>
        <div>
          <dt>实时连接</dt>
          <dd>{{ connectionStateText }}</dd>
        </div>
      </dl>
      <p class="preview-note">当前功能页处于 Vue 预览状态，完整业务操作将在迁移完成后开放。</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

import { useRuntimeEvents } from "@vue-app/realtime/runtimeEvents";

const route = useRoute();
const runtimeEvents = useRuntimeEvents();

const pageTitle = computed(() => String(route.meta.title ?? "管理台业务页面"));
const pageDescription = computed(() =>
  String(route.meta.description ?? "该业务模块正在迁移至 Vue 管理台。"),
);
const connectionStateText = computed(() => ({
  open: "已连接",
  connecting: "正在连接",
  retrying: "正在重连",
  closed: "已断开",
})[runtimeEvents.state.value]);
</script>

<style scoped>
.placeholder-page { display: grid; min-width: 0; min-height: 60vh; place-items: center; overflow: hidden; padding: 2rem; }
.placeholder-card { box-sizing: border-box; width: min(36rem, 100%); min-width: 0; padding: 2rem; border: 1px solid currentColor; border-radius: var(--radius-lg); }
.placeholder-mark { display: grid; width: 2.5rem; height: 2.5rem; place-items: center; border: 1px solid currentColor; border-radius: 50%; }
h1 { margin: 1rem 0 .5rem; }
dl { display: grid; gap: .75rem; margin: 1.5rem 0; }
dl > div { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .5rem 1rem; }
dt, .preview-note { opacity: .72; }
dd { min-width: 0; margin: 0; text-align: right; overflow-wrap: anywhere; }
dd code { white-space: normal; }
</style>
