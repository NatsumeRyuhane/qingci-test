// 测试用的 API mock：按“方法 + 路径模式”返回 JSON，并记录每个请求。
import { vi } from "vitest";

export type Call = { method: string; path: string; query: URLSearchParams; body: unknown };
type Handler = (call: Call, params: Record<string, string>) => unknown;

function match(pattern: string, path: string): Record<string, string> | null {
  const a = pattern.split("/");
  const b = path.split("/");
  if (a.length !== b.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < a.length; i++) {
    if (a[i].startsWith(":")) params[a[i].slice(1)] = decodeURIComponent(b[i]);
    else if (a[i] !== b[i]) return null;
  }
  return params;
}

export function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const text = request.method === "GET" ? "" : await request.text();
    const call: Call = {
      method: request.method,
      path: url.pathname,
      query: url.searchParams,
      body: text ? JSON.parse(text) : undefined,
    };
    calls.push(call);
    for (const [key, handler] of Object.entries(routes)) {
      const [method, pattern] = key.split(" ");
      const params = method === request.method ? match(pattern, url.pathname) : null;
      if (params) {
        const result = handler(call, params);
        if (result instanceof Response) return result;
        return new Response(JSON.stringify(result), {
          status: request.method === "POST" ? 201 : 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    return new Response(
      JSON.stringify({ error: { code: "not_found", message: "Not Found", request_id: "t" } }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export const HEALTH = { "GET /v1/health": () => ({ status: "ok", version: "0.1.0" }) };
