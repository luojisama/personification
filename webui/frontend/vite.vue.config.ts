import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  root: fileURLToPath(new URL("./vue-preview", import.meta.url)),
  base: "/personification/frontend-vue-preview/",
  plugins: [vue()],
  resolve: {
    alias: {
      "@vue-app": fileURLToPath(new URL("./src-vue", import.meta.url)),
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: fileURLToPath(new URL("../frontend_vue_preview_dist", import.meta.url)),
    assetsDir: "assets",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    host: "127.0.0.1",
    port: 5179,
    proxy: {
      "/personification/api": "http://127.0.0.1:8088",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: [fileURLToPath(new URL("./src-vue/test/setup.ts", import.meta.url))],
    include: ["../src-vue/**/*.{test,spec}.ts"],
    restoreMocks: true,
    clearMocks: true,
    css: true,
  },
});
