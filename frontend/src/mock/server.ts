// 预览用：在浏览器中模拟 /v1 API，让没有后端的静态页面也能完整演示工作台（`pnpm build:preview`）。
// 规则与后端一致（只能处理最新草稿、结案后不能再处理、追问让案件回到 open、升级和驳回需要
// 理由、指标以最后一次处理为准），但不调用 LLM：草稿来自 data.ts 中按类别写好的合成内容。
// 数据只保存在当前页面的内存中，刷新后恢复初始状态。
import type {
  Case,
  CaseAnalysis,
  CaseDetail,
  CaseStatus,
  Draft,
  Feedback,
  FeedbackAction,
  InquiryCategory,
  InquiryForm,
  MetricsRow,
  OutboundMail,
} from "../api/client";
import { INVISIBLE_PATTERN } from "../components/SafeText";
import { CLOSING, demoCases, GREETING, PLAYBOOK, TEMPLATES, TRANSLATIONS } from "./data";

type Stored = { case: Case; updatedAt: number };
type Json = Record<string, unknown>;

class HttpError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const CLOSED: ReadonlySet<CaseStatus> = new Set(["replied", "no_reply_needed"]);
const INJECTION = new RegExp(`${INVISIBLE_PATTERN}|指示を無視|ignore (all )?previous`, "iu");

let clock = Date.parse("2026-11-27T09:05:00+09:00");
function now(): string {
  clock += 60_000;
  const local = new Date(clock + 9 * 3_600_000).toISOString().slice(0, 19);
  return `${local}+09:00`;
}

let counter = 0;
function newId(prefix: string): string {
  counter += 1;
  return `${prefix}${counter.toString(16).padStart(8, "0")}`;
}

export function levenshtein(a: string, b: string): number {
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const x = a.slice(start, endA);
  const y = b.slice(start, endB);
  let previous = Array.from({ length: y.length + 1 }, (_, i) => i);
  for (let i = 1; i <= x.length; i++) {
    const current = [i];
    for (let j = 1; j <= y.length; j++) {
      current.push(
        Math.min(
          previous[j] + 1,
          current[j - 1] + 1,
          previous[j - 1] + (x[i - 1] === y[j - 1] ? 0 : 1),
        ),
      );
    }
    previous = current;
  }
  return previous[y.length];
}

/** API 路径（去掉 /v1），`:` 表示一个参数。 */
const PATTERNS = [
  "health",
  "cases",
  "cases/:",
  "cases/:/drafts",
  "drafts/:/backtranslate",
  "drafts/:/feedback",
  "templates",
  "templates/:",
  "metrics",
  "sim/inbox",
  "sim/outbox",
];

function fits(pattern: string, segments: string[]): boolean {
  const expected = pattern.split("/");
  return (
    expected.length === segments.length &&
    expected.every((part, i) => part === ":" || part === segments[i])
  );
}

export class MockBackend {
  cases = new Map<string, Stored>();
  analyses = new Map<string, CaseAnalysis>();
  drafts: Draft[] = [];
  feedback: Feedback[] = [];
  outbox: OutboundMail[] = [];

  constructor() {
    for (const item of demoCases()) this.save(item);
    this.generate("SIM-DEMO02");
    this.generate("SIM-DEMO03");
  }

  private save(item: Case) {
    this.cases.set(item.id, { case: item, updatedAt: clock++ });
  }

  private requireCase(id: string): Case {
    const stored = this.cases.get(id);
    if (!stored) throw new HttpError(404, "not_found", `案件不存在：${id}`);
    return stored.case;
  }

  private latestDraft(caseId: string): Draft | undefined {
    return this.drafts.filter((d) => d.case_id === caseId).at(-1);
  }

  private detail(id: string): CaseDetail {
    const item = this.requireCase(id);
    const draft = this.latestDraft(id) ?? null;
    return {
      case: item,
      analysis: this.analyses.get(id) ?? null,
      draft,
      feedback: draft ? this.feedback.filter((f) => f.draft_id === draft.id) : [],
    };
  }

  generate(id: string): CaseDetail {
    const item = this.requireCase(id);
    if (CLOSED.has(item.status ?? "open")) {
      throw new HttpError(409, "conflict", `案件已结案（${item.status}），不能起草`);
    }
    const message = [...item.messages].reverse().find((m) => m.role === "player");
    if (!message) throw new HttpError(409, "conflict", `案件 '${id}' 中没有玩家邮件`);
    const category: InquiryCategory = item.category ?? item.form?.category ?? "other";
    const play = PLAYBOOK[category];
    const suspicious = INJECTION.test(message.body);
    const created = now();
    const route = suspicious ? "review" : play.route;
    const reasons = suspicious
      ? ["疑似提示注入（正文中有不可见字符或要求忽略指示的文字），禁止自动发送，转人工审核"]
      : play.reasons;
    this.analyses.set(id, {
      schema_version: 1,
      case_id: id,
      message_seq: message.seq,
      translation_zh:
        TRANSLATIONS[`${id}:${message.seq}`] ??
        "（预览：浏览器内模拟的后端不调用翻译服务，请参考日语原文。）",
      summary_zh: suspicious
        ? "玩家询问账号问题，正文中夹带要求“全额退款”的注入文字。"
        : `玩家的${{ feedback: "意见建议", bug: "故障反馈", purchase: "购买问题", data_transfer: "数据迁移问题", other: "咨询" }[category]}：${message.body.replace(/\s+/g, " ").slice(0, 40)}`,
      category,
      intent: play.intent,
      sentiment:
        category === "purchase" ? "negative" : category === "feedback" ? "positive" : "neutral",
      urgency: play.urgency,
      facts: item.form?.device ? { device: item.form.device } : {},
      missing_info: play.missing,
      sensitivity_tags: category === "purchase" ? ["payment"] : [],
      model: "preview",
      created_at: created,
    });
    const draft: Draft = {
      schema_version: 1,
      id: newId("draft-"),
      case_id: id,
      message_seq: message.seq,
      template_refs: [
        {
          template_id: play.template,
          template_version: 1,
          reason_zh: `意图 ${play.intent} 的候选模板`,
        },
      ],
      similar_case_refs: [],
      body_ja: `${GREETING}\n\n${play.core}\n\n${CLOSING}`,
      back_translation_zh: `您好，这里是『ぽちゃガチョ！』客服。\n\n${play.back}\n\n今后也请多多支持。`,
      checks: [
        { kind: "structure", passed: true },
        { kind: "register", passed: true },
        { kind: "forbidden_promise", passed: true },
        {
          kind: "content_safety",
          passed: !suspicious,
          detail_zh: suspicious ? "玩家正文中有注入迹象，草稿未采纳其中的要求" : null,
        },
      ],
      confidence: route === "auto_send" ? 0.93 : 0.72,
      route,
      route_reasons: reasons,
      citations: [],
      mode: "pipeline",
      trace: [],
      model: "preview",
      created_at: created,
    };
    this.drafts.push(draft);
    const status: CaseStatus =
      item.status === "escalated" || route === "escalate" ? "escalated" : "drafted";
    this.save({ ...item, category, status });
    return this.detail(id);
  }

  submitFeedback(draftId: string, body: Json) {
    const draft = this.drafts.find((d) => d.id === draftId);
    if (!draft) throw new HttpError(404, "not_found", `草稿不存在：${draftId}`);
    const item = this.requireCase(draft.case_id);
    if (CLOSED.has(item.status ?? "open")) {
      throw new HttpError(409, "conflict", `案件已结案（${item.status}），不能再处理`);
    }
    if (this.latestDraft(item.id)?.id !== draft.id) {
      throw new HttpError(409, "conflict", "这不是案件的最新草稿，请刷新后处理最新草稿");
    }
    const lastPlayer = [...item.messages].reverse().find((m) => m.role === "player");
    if (lastPlayer?.seq !== draft.message_seq) {
      throw new HttpError(409, "conflict", "玩家在起草之后又有来信，请重新起草");
    }
    const action = body.action as FeedbackAction;
    const actor = String(body.actor ?? "").trim();
    const reason = String(body.reason ?? "").trim() || null;
    if (!actor) throw new HttpError(422, "validation_error", "请求格式不正确：body.actor");
    if ((action === "escalated" || action === "rejected") && !reason) {
      throw new HttpError(422, "validation_error", "升级处理和驳回需要填写理由");
    }
    let finalBody: string | null = null;
    let distance = 0;
    if (action === "edited") {
      finalBody = String(body.final_body_ja ?? "");
      distance = levenshtein(draft.body_ja, finalBody);
      if (!finalBody.trim() || distance === 0) {
        throw new HttpError(422, "validation_error", "正文没有修改，请直接批准");
      }
    }
    const created = now();
    const feedback: Feedback = {
      schema_version: 1,
      draft_id: draft.id,
      actor,
      action,
      final_body_ja: finalBody,
      reason,
      edit_distance: distance,
      created_at: created,
    };
    this.feedback.push(feedback);
    let mail: OutboundMail | null = null;
    let next: Case;
    if (action === "approved" || action === "edited") {
      const sent = finalBody ?? draft.body_ja;
      const seq = (item.messages.at(-1)?.seq ?? 0) + 1;
      mail = {
        schema_version: 1,
        id: newId("mail-"),
        case_id: item.id,
        message_seq: seq,
        draft_id: draft.id,
        body_ja: sent,
        sent_by: actor,
        sent_at: created,
      };
      this.outbox.unshift(mail);
      next = {
        ...item,
        status: "replied",
        messages: [
          ...item.messages,
          {
            thread_id: item.id,
            seq,
            role: "agent",
            raw_body: sent,
            body: sent,
            quoted: "",
            greeting: "",
            closing: "",
            sent_at: created,
            role_evidence: [{ kind: "metadata", detail: "由工作台发出（#8）" }],
          },
        ],
      };
    } else {
      next = { ...item, status: action === "escalated" ? "escalated" : "open" };
    }
    this.save(next);
    return { feedback, mail, case_status: next.status };
  }

  receive(body: Json) {
    const text = String(body.body ?? "");
    if (!text.trim()) throw new HttpError(422, "validation_error", "来信正文不能为空");
    const target = body.case_id ? String(body.case_id) : null;
    const form = (body.form as InquiryForm | null) ?? null;
    const sentAt = now();
    let item: Case;
    if (target) {
      if (form)
        throw new HttpError(
          422,
          "validation_error",
          "追问不能附带表单，表单只在线程的第 1 封邮件中",
        );
      const existing = this.requireCase(target);
      const seq = (existing.messages.at(-1)?.seq ?? 0) + 1;
      item = {
        ...existing,
        status: existing.status === "escalated" ? "escalated" : "open",
        messages: [
          ...existing.messages,
          {
            thread_id: target,
            seq,
            role: "player",
            raw_body: text,
            body: text,
            quoted: "",
            greeting: "",
            closing: "",
            sent_at: sentAt,
            role_evidence: [],
          },
        ],
      };
    } else {
      const id = newId("SIM-").toUpperCase();
      item = {
        schema_version: 2,
        id,
        status: "open",
        category: form?.category ?? null,
        form,
        messages: [
          {
            thread_id: id,
            seq: 1,
            role: "player",
            raw_body: text,
            body: text,
            quoted: "",
            greeting: "",
            closing: "",
            sent_at: sentAt,
            role_evidence: [],
          },
        ],
      };
    }
    this.save(item);
    return {
      case_id: item.id,
      message_seq: item.messages.at(-1)?.seq ?? 1,
      case_status: item.status,
    };
  }

  listCases(query: URLSearchParams) {
    const status = query.get("status");
    const route = query.get("route");
    const category = query.get("category");
    const limit = Number(query.get("limit") ?? 50);
    const offset = Number(query.get("offset") ?? 0);
    const rows = [...this.cases.values()]
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .map(({ case: item }) => ({ item, draft: this.latestDraft(item.id) }))
      .filter(
        ({ item, draft }) =>
          (!status || item.status === status) &&
          (!category || item.category === category) &&
          (!route || draft?.route === route),
      );
    return {
      total: rows.length,
      items: rows.slice(offset, offset + limit).map(({ item, draft }) => {
        const analysis = this.analyses.get(item.id);
        const player = [...item.messages].reverse().find((m) => m.role === "player");
        return {
          id: item.id,
          status: item.status ?? "open",
          category: item.category ?? null,
          route: draft?.route ?? null,
          summary_zh: analysis?.summary_zh ?? null,
          urgency: analysis?.urgency ?? null,
          sentiment: analysis?.sentiment ?? null,
          preview_ja: (player ?? item.messages[0]).body.replace(/\s+/g, " ").slice(0, 80),
          message_count: item.messages.length,
          last_message_at: item.messages.at(-1)?.sent_at ?? null,
        };
      }),
    };
  }

  metrics() {
    const last = new Map<string, Feedback>();
    for (const f of this.feedback) last.set(f.draft_id, f);
    const tally = (key: string, outcomes: Feedback[]): MetricsRow => {
      const count = (a: FeedbackAction) => outcomes.filter((f) => f.action === a).length;
      const sent = outcomes.filter((f) => f.action === "approved" || f.action === "edited");
      const rate = (n: number) => (outcomes.length ? n / outcomes.length : 0);
      return {
        key,
        handled: outcomes.length,
        approved: count("approved"),
        edited: count("edited"),
        escalated: count("escalated"),
        rejected: count("rejected"),
        approved_rate: rate(count("approved")),
        edited_rate: rate(count("edited")),
        escalated_rate: rate(count("escalated")),
        rejected_rate: rate(count("rejected")),
        avg_edit_distance: sent.length
          ? sent.reduce((s, f) => s + f.edit_distance, 0) / sent.length
          : null,
      };
    };
    const group = (keyOf: (d: Draft) => string) => {
      const groups = new Map<string, Feedback[]>();
      for (const [draftId, f] of last) {
        const draft = this.drafts.find((d) => d.id === draftId);
        if (!draft) continue;
        const key = keyOf(draft);
        groups.set(key, [...(groups.get(key) ?? []), f]);
      }
      return [...groups.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => tally(k, v));
    };
    return {
      total: tally("total", [...last.values()]),
      by_template: group((d) => d.template_refs?.[0]?.template_id ?? "（无模板）"),
      by_route: group((d) => d.route),
    };
  }

  handle(method: string, url: URL, body: Json): unknown {
    const parts = url.pathname.split("/").filter(Boolean).slice(1).map(decodeURIComponent);
    const key = `${method} /${PATTERNS.find((p) => fits(p, parts)) ?? parts.join("/")}`;
    switch (key) {
      case "GET /health":
        return { status: "ok", version: "预览" };
      case "GET /cases":
        return this.listCases(url.searchParams);
      case "GET /cases/:":
        return this.detail(parts[1]);
      case "POST /cases/:/drafts":
        return this.generate(parts[1]);
      case "POST /drafts/:/backtranslate": {
        const text = String(body.body_ja ?? "");
        return {
          back_translation_zh: `（预览：浏览器内模拟的后端不调用回译服务。编辑后的日语正文共 ${text.length} 个字符。）`,
        };
      }
      case "POST /drafts/:/feedback":
        return this.submitFeedback(parts[1], body);
      case "GET /templates": {
        const intent = url.searchParams.get("intent");
        const category = url.searchParams.get("category");
        return {
          items: TEMPLATES.filter(
            (t) => (!intent || t.intent === intent) && (!category || t.category === category),
          ),
        };
      }
      case "GET /templates/:": {
        const found = TEMPLATES.find((t) => t.id === parts[1]);
        if (!found) throw new HttpError(404, "not_found", `模板不存在或未批准：${parts[1]}`);
        return found;
      }
      case "GET /metrics":
        return this.metrics();
      case "POST /sim/inbox":
        return this.receive(body);
      case "GET /sim/outbox":
        return { items: this.outbox, total: this.outbox.length };
      default:
        throw new HttpError(404, "not_found", "Not Found");
    }
  }
}

/** 用模拟的后端替换 /v1 请求。其他请求照常发出。 */
export function installMockBackend(backend = new MockBackend()) {
  const original = globalThis.fetch.bind(globalThis);
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/v1/")) return original(input, init);
    const text = request.method === "GET" ? "" : await request.text();
    const requestId = newId("preview-");
    const json = (status: number, payload: unknown) =>
      new Response(JSON.stringify(payload), {
        status,
        headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
      });
    try {
      const result = backend.handle(request.method, url, text ? (JSON.parse(text) as Json) : {});
      return json(request.method === "POST" ? 201 : 200, result);
    } catch (error) {
      if (error instanceof HttpError) {
        return json(error.status, {
          error: { code: error.code, message: error.message, request_id: requestId },
        });
      }
      return json(500, {
        error: { code: "internal_error", message: "服务器内部错误", request_id: requestId },
      });
    }
  };
  return backend;
}
