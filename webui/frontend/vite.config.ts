import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  base: "/personification/frontend/",
  plugins: [vue()],
  resolve: {
    alias: {
      "@vue-app": fileURLToPath(new URL("./src-vue", import.meta.url)),
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../frontend_dist",
    assetsDir: "assets",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    host: "127.0.0.1",
    port: 5178,
    proxy: {
      "/personification/api": "http://127.0.0.1:8088",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: [fileURLToPath(new URL("./src-vue/test/setup.ts", import.meta.url))],
    include: ["src/**/*.{test,spec}.ts", "src-vue/**/*.{test,spec}.ts"],
    restoreMocks: true,
    clearMocks: true,
    css: true,
  },
});
