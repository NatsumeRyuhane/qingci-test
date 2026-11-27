// 定时刷新：模拟邮箱在另一个标签页中投递来信时，工作台不用手动刷新也能看到。
import { useEffect, useRef } from "react";

export const POLL_MS = 5000;

export function usePolling(callback: () => void, intervalMs = POLL_MS, enabled = true) {
  const saved = useRef(callback);
  saved.current = callback;
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => saved.current(), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, enabled]);
}
