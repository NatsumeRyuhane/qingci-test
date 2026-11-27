// 枚举值的中文显示名。取值本身是稳定的英文标识（后端契约），这里只负责显示。
import type { CaseStatus, FeedbackAction, InquiryCategory, Route } from "../api/client";

export const ROUTE_LABELS: Record<Route, string> = {
  auto_send: "可直接发送",
  review: "人工审核",
  escalate: "升级处理",
};

export const STATUS_LABELS: Record<CaseStatus, string> = {
  open: "待处理",
  drafted: "已起草",
  escalated: "已升级",
  replied: "已回复",
  no_reply_needed: "无需回复",
  undetermined: "待判断",
};

export const CATEGORY_LABELS: Record<InquiryCategory, string> = {
  bug: "故障",
  purchase: "购买问题",
  feedback: "意见建议",
  data_transfer: "数据迁移",
  other: "其他",
};

export const ACTION_LABELS: Record<FeedbackAction, string> = {
  approved: "批准发送",
  edited: "修改后发送",
  escalated: "升级处理",
  rejected: "驳回",
  no_reply: "无需回复",
};

export const URGENCY_LABELS: Record<string, string> = { low: "低", normal: "普通", high: "高" };

export const SENTIMENT_LABELS: Record<string, string> = {
  positive: "积极",
  neutral: "平静",
  negative: "不满",
  angry: "愤怒",
};

export const ROLE_LABELS: Record<string, string> = {
  player: "玩家",
  agent: "客服",
  system: "系统",
  unknown: "未知",
};

export const CHECK_LABELS: Record<string, string> = {
  structure: "必需结构",
  register: "语体",
  forbidden_promise: "不允许的承诺",
  template_window: "模板有效期",
  slots_filled: "占位符已填写",
  citations: "引用",
  content_safety: "内容安全",
};

export const CLOSED_STATUSES: ReadonlySet<CaseStatus> = new Set(["replied", "no_reply_needed"]);

export function label(map: Record<string, string>, value: string | null | undefined): string {
  if (value == null) return "—";
  return map[value] ?? value;
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}
