/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 开发时把 /v1 代理到后端（`copilot serve` 默认监听 127.0.0.1:8000），前后端同源，不需要 CORS。
// 部署时由 nginx 做同样的事（#14）。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/v1": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
