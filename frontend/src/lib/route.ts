// 基于 location.hash 的简单路由：静态托管（演示模式）和开发服务器都不需要额外配置。
// hash 只用 ASCII 字母、数字和连字符（`#inbox`、`#case-<id>`、`#metrics`、`#sim`），嵌入其他页面
// 时也能保留；案件 ID 本身只含 `[A-Za-z0-9_-]`（契约限制）。旧的 `#/cases/<id>` 形式仍然可用。
import { useEffect, useState } from "react";

export type Route =
  | { page: "inbox" }
  | { page: "case"; caseId: string }
  | { page: "metrics" }
  | { page: "sim" };

const CASE_PREFIX = "case-";

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, "");
  if (path.startsWith(CASE_PREFIX) && path.length > CASE_PREFIX.length) {
    return { page: "case", caseId: decodeURIComponent(path.slice(CASE_PREFIX.length)) };
  }
  const [first, second] = path.split("/");
  if (first === "cases" && second) return { page: "case", caseId: decodeURIComponent(second) };
  if (first === "metrics") return { page: "metrics" };
  if (first === "sim") return { page: "sim" };
  return { page: "inbox" };
}

export function caseHref(caseId: string): string {
  return `#${CASE_PREFIX}${encodeURIComponent(caseId)}`;
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
