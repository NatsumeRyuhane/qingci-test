import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.tsx";
import "./styles.css";

async function start() {
  const root = document.getElementById("root");
  if (!root) {
    throw new Error("找不到 #root 元素");
  }
  // 演示模式（`pnpm dev:demo`、`pnpm build:demo`）：用浏览器内模拟的后端，静态托管也能演示。
  // MODE 在构建时被替换为常量，普通构建中不包含模拟后端的代码。
  if (import.meta.env.MODE === "demo") {
    const { installMockBackend } = await import("./mock/server");
    installMockBackend();
  }
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void start();
