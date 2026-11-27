// 两个文本框按比例同步滚动（像对比工具那样，中文和日语看的是对应的位置）。
import { type RefObject, useCallback, useRef } from "react";

export function useScrollSync(
  a: RefObject<HTMLElement | null>,
  b: RefObject<HTMLElement | null>,
): { onScrollA: () => void; onScrollB: () => void } {
  const source = useRef<HTMLElement | null>(null);

  const sync = useCallback((from: HTMLElement | null, to: HTMLElement | null) => {
    if (!from || !to) return;
    // 由另一侧触发的滚动不再反向同步，避免来回抖动
    if (source.current && source.current !== from) {
      source.current = null;
      return;
    }
    const range = from.scrollHeight - from.clientHeight;
    const ratio = range > 0 ? from.scrollTop / range : 0;
    source.current = from;
    to.scrollTop = ratio * (to.scrollHeight - to.clientHeight);
    requestAnimationFrame(() => {
      source.current = null;
    });
  }, []);

  return {
    onScrollA: () => sync(a.current, b.current),
    onScrollB: () => sync(b.current, a.current),
  };
}
