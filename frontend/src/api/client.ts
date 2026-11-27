// 后端 REST API（/v1）的类型化客户端。类型由 scripts/gen-api-types.sh 从后端 OpenAPI 生成，不要手写。
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type Health = Schemas["Health"];
export type CaseSummary = Schemas["CaseSummary"];
export type CaseList = Schemas["CaseList"];
export type CaseDetail = Schemas["CaseDetail"];
export type Case = Schemas["Case"];
export type Message = Schemas["Message"];
export type CaseAnalysis = Schemas["CaseAnalysis"];
export type Draft = Schemas["Draft"];
export type DraftCheck = Schemas["DraftCheck"];
export type Feedback = Schemas["Feedback"];
export type FeedbackAction = Schemas["FeedbackAction"];
export type OutboundMail = Schemas["OutboundMail"];
export type ReplyTemplate = Schemas["ReplyTemplate"];
export type MetricsResponse = Schemas["MetricsResponse"];
export type MetricsRow = Schemas["MetricsRow"];
export type InquiryForm = Schemas["InquiryForm"];
export type CaseStatus = Schemas["CaseStatus"];
export type Route = Schemas["Route"];
export type InquiryCategory = Schemas["InquiryCategory"];
type ErrorResponse = Schemas["ErrorResponse"];

// 同源访问：开发时由 Vite 代理、部署时由 nginx 把 /v1 转发到后端。
// 用完整的 origin 而不是空字符串：Node 的 fetch（测试环境）不接受相对 URL。
// fetch 在每次调用时再取全局对象，测试中用 vi.stubGlobal 替换才会生效。
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request),
});

/** 后端返回的错误（统一格式 `{error: {code, message, request_id}}`），或网络错误（status 为 0）。 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | undefined;

  constructor(status: number, code: string, message: string, requestId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/** 取出成功响应的数据，否则抛出 `ApiError`。 */
export async function call<T>(request: Promise<Result<T>>): Promise<T> {
  let result: Result<T>;
  try {
    result = await request;
  } catch {
    throw new ApiError(0, "network", "无法连接后端，请确认后端服务已启动。");
  }
  if (result.response.ok && result.data !== undefined) {
    return result.data;
  }
  const detail = (result.error as ErrorResponse | undefined)?.error;
  throw new ApiError(
    result.response.status,
    detail?.code ?? "http_error",
    detail?.message ?? `请求失败（HTTP ${result.response.status}）`,
    detail?.request_id,
  );
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return error.requestId ? `${error.message}（请求 ID：${error.requestId}）` : error.message;
  }
  return "发生了意外错误";
}
