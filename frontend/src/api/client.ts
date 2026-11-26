// 后端 REST API（/v1）的类型化客户端。类型由 scripts/gen-api-types.sh 从后端 OpenAPI 生成，不要手写。
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type Health = components["schemas"]["Health"];

// 同源访问：开发时由 Vite 代理、部署时由 nginx 把 /v1 转发到后端。
// 用完整的 origin 而不是空字符串：Node 的 fetch（测试环境）不接受相对 URL。
// fetch 在每次调用时再取全局对象，测试中用 vi.stubGlobal 替换才会生效。
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});
