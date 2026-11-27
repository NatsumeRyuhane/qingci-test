import { useCallback, useEffect, useState } from "react";
import {
  api,
  type CaseAnalysis,
  type CaseDetail,
  call,
  errorText,
  type InquiryForm,
  type Message,
} from "../api/client";
import { ErrorNotice, StatusBadge } from "../components/Badges";
import { SafeText } from "../components/SafeText";
import {
  CATEGORY_LABELS,
  formatTime,
  label,
  ROLE_LABELS,
  SENTIMENT_LABELS,
  URGENCY_LABELS,
} from "../lib/labels";
import { DraftPanel } from "./DraftPanel";

const FORM_FIELDS: [keyof InquiryForm, string][] = [
  ["category", "类别"],
  ["device", "设备"],
  ["os_version", "系统版本"],
  ["app_version", "应用版本"],
  ["platform", "平台"],
  ["occurred_at", "发生时间"],
  ["has_attachment", "附件"],
];

function formValue(form: InquiryForm, key: keyof InquiryForm): string | null {
  const value = form[key];
  if (value == null || value === "") return null;
  if (key === "category") return label(CATEGORY_LABELS, String(value));
  if (typeof value === "boolean") return value ? "有" : "无";
  return String(value);
}

function MessageCard({ message, analysis }: { message: Message; analysis: CaseAnalysis | null }) {
  const analyzed = analysis?.message_seq === message.seq;
  return (
    <article className={`message message-${message.role}${analyzed ? " message-analyzed" : ""}`}>
      <header>
        <strong>
          #{message.seq} {label(ROLE_LABELS, message.role)}
        </strong>
        <span className="meta">{formatTime(message.sent_at)}</span>
        {analyzed && <span className="badge badge-muted">本次分析的邮件</span>}
      </header>
      <div className={analyzed ? "side-by-side" : undefined}>
        <div lang="ja">
          {analyzed && <h4>日语原文</h4>}
          <p className="body">
            <SafeText text={message.body || "（正文为空）"} />
          </p>
        </div>
        {analyzed && analysis && (
          <div>
            <h4>中文翻译</h4>
            <p className="body">
              <SafeText text={analysis.translation_zh} />
            </p>
          </div>
        )}
      </div>
      {message.quoted && (
        <details>
          <summary>引用的历史邮件</summary>
          <p className="body muted" lang="ja">
            <SafeText text={message.quoted} />
          </p>
        </details>
      )}
    </article>
  );
}

function AnalysisCard({ analysis }: { analysis: CaseAnalysis }) {
  const facts = Object.entries(analysis.facts ?? {});
  return (
    <section className="card">
      <h3>分析</h3>
      <p>
        <SafeText text={analysis.summary_zh} />
      </p>
      <dl className="facts">
        <dt>类别</dt>
        <dd>{label(CATEGORY_LABELS, analysis.category)}</dd>
        <dt>意图</dt>
        <dd>
          <code>{analysis.intent}</code>
        </dd>
        <dt>情绪</dt>
        <dd>{label(SENTIMENT_LABELS, analysis.sentiment)}</dd>
        <dt>紧急程度</dt>
        <dd>{label(URGENCY_LABELS, analysis.urgency)}</dd>
        {facts.map(([key, value]) => (
          <span key={key} className="contents">
            <dt>{key}</dt>
            <dd>
              <SafeText text={value} />
            </dd>
          </span>
        ))}
      </dl>
      {(analysis.sensitivity_tags ?? []).length > 0 && (
        <p>
          敏感标签：
          {(analysis.sensitivity_tags ?? []).map((tag) => (
            <span key={tag} className="badge badge-warn">
              {tag}
            </span>
          ))}
        </p>
      )}
      {(analysis.missing_info ?? []).length > 0 && (
        <div className="notice notice-warn">
          <strong>回复前需要向玩家确认：</strong>
          <ul>
            {(analysis.missing_info ?? []).map((item) => (
              <li key={item}>
                <SafeText text={item} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {analysis.no_reply && (
        <p className="notice notice-info">
          不回复：
          <SafeText text={analysis.no_reply.reason_zh} />
          {analysis.no_reply.duplicate_of &&
            `（重复提交，对应线程 ${analysis.no_reply.duplicate_of}）`}
        </p>
      )}
    </section>
  );
}

export function CaseView({ caseId, actor }: { caseId: string; actor: string }) {
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setDetail(
        await call(api.GET("/v1/cases/{case_id}", { params: { path: { case_id: caseId } } })),
      );
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [caseId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (!detail) {
    return (
      <section>
        <a href="#/inbox">← 返回收件箱</a>
        <ErrorNotice message={error} />
        {!error && <p>正在加载…</p>}
      </section>
    );
  }
  const { case: thread, analysis } = detail;
  const form = thread.form;
  return (
    <section>
      <div className="toolbar">
        <a href="#/inbox">← 返回收件箱</a>
        <h2>案件 {thread.id}</h2>
        <StatusBadge status={thread.status ?? "open"} />
        <span className="meta">{label(CATEGORY_LABELS, thread.category)}</span>
      </div>
      <ErrorNotice message={error} />
      <div className="case-layout">
        <div className="case-main">
          {form && (
            <section className="card">
              <h3>游戏内表单</h3>
              <dl className="facts">
                {FORM_FIELDS.map(([key, name]) => {
                  const value = formValue(form, key);
                  return value ? (
                    <span key={key} className="contents">
                      <dt>{name}</dt>
                      <dd>
                        <SafeText text={value} />
                      </dd>
                    </span>
                  ) : null;
                })}
              </dl>
              {form.content && (
                <p className="body" lang="ja">
                  <SafeText text={form.content} />
                </p>
              )}
            </section>
          )}
          {analysis && <AnalysisCard analysis={analysis} />}
          <section>
            <h3>邮件线程</h3>
            {thread.messages.map((message) => (
              <MessageCard key={message.seq} message={message} analysis={analysis ?? null} />
            ))}
          </section>
        </div>
        <aside className="case-side">
          <DraftPanel detail={detail} actor={actor} onChanged={reload} />
        </aside>
      </div>
    </section>
  );
}
