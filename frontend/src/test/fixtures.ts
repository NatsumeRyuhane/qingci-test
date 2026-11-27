// SYNTHETIC：合成测试数据，不来自样本（D-002）。
import type { CaseDetail, Draft } from "../api/client";

/** 零宽空格。用 fromCodePoint 构造，源码中不出现不可见字符。 */
export const ZWSP = String.fromCodePoint(0x200b);

export const DRAFT: Draft = {
  schema_version: 1,
  id: "draft-1",
  case_id: "SIM-1",
  message_seq: 1,
  body_ja: "【合成データ】返信です。",
  back_translation_zh: "【合成】这是回复。",
  confidence: 0.9,
  route: "review",
  route_reasons: ["【合成】需要审核"],
  checks: [{ kind: "register", passed: true }],
  template_refs: [{ template_id: "tpl-syn", template_version: 1, reason_zh: "【合成】理由" }],
  mode: "pipeline",
  model: "fake",
  created_at: "2099-01-01T00:00:00+09:00",
};

export function detail(overrides: Partial<CaseDetail> = {}): CaseDetail {
  return {
    case: {
      schema_version: 2,
      id: "SIM-1",
      status: "drafted",
      messages: [
        {
          thread_id: "SIM-1",
          seq: 1,
          role: "player",
          raw_body: "【合成データ】問い合わせ",
          body: `【合成データ】問い合わせ${ZWSP}`,
          quoted: "",
          greeting: "",
          closing: "",
        },
      ],
    },
    analysis: null,
    draft: DRAFT,
    feedback: [],
    ...overrides,
  };
}
