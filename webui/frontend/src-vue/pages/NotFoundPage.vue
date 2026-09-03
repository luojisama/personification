<template>
  <div class="not-found-page">
    <span aria-hidden="true">404</span>
    <h1>页面未找到</h1>
    <p>当前地址不存在或已经移动，请从导航重新进入。</p>
    <RouterLink to="/runtime/overview/summary">返回运行摘要</RouterLink>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";
</script>

<style scoped>
.not-found-page { display: grid; min-height: 100vh; place-content: center; justify-items: center; padding: 2rem; text-align: center; }
.not-found-page > span { font: 800 clamp(4rem, 14vw, 9rem) / 1 ui-monospace, monospace; opacity: .16; }
a { margin-top: 1rem; }
</style>
