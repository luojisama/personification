import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/personification/frontend/",
  plugins: [react()],
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
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
    clearMocks: true,
    css: true,
  },
});
