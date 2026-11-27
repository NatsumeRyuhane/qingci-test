// 基于 location.hash 的简单路由：静态托管（预览）和开发服务器都不需要额外配置。
import { useEffect, useState } from "react";

export type Route =
  | { page: "inbox" }
  | { page: "case"; caseId: string }
  | { page: "metrics" }
  | { page: "sim" };

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, "");
  const [first, second] = path.split("/");
  if (first === "cases" && second) return { page: "case", caseId: decodeURIComponent(second) };
  if (first === "metrics") return { page: "metrics" };
  if (first === "sim") return { page: "sim" };
  return { page: "inbox" };
}

export function caseHref(caseId: string): string {
  return `#/cases/${encodeURIComponent(caseId)}`;
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
