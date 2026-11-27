import { useEffect, useRef, useState } from "react";
import {
  api,
  type CaseDetail,
  call,
  type Draft,
  errorText,
  type FeedbackAction,
  type ReplyTemplate,
} from "../api/client";
import { ErrorNotice, RouteBadge } from "../components/Badges";
import { SafeText } from "../components/SafeText";
import { ACTION_LABELS, CHECK_LABELS, CLOSED_STATUSES, formatTime, label } from "../lib/labels";
import { caseHref } from "../lib/route";

export const BACKTRANSLATE_DELAY_MS = 800;

function useTemplate(templateId: string | undefined, version: number | undefined) {
  const [template, setTemplate] = useState<ReplyTemplate | null>(null);
  useEffect(() => {
    setTemplate(null);
    if (!templateId) return;
    let cancelled = false;
    call(
      api.GET("/v1/templates/{template_id}", {
        params: { path: { template_id: templateId }, query: { version } },
      }),
    )
      .then((data) => !cancelled && setTemplate(data))
      .catch(() => undefined); // 模板只是参考信息，取不到时不影响处理
    return () => {
      cancelled = true;
    };
  }, [templateId, version]);
  return template;
}

function useAlternatives(intent: string | undefined, exclude: string | undefined) {
  const [items, setItems] = useState<ReplyTemplate[]>([]);
  useEffect(() => {
    setItems([]);
    if (!intent) return;
    let cancelled = false;
    call(api.GET("/v1/templates", { params: { query: { intent } } }))
      .then((data) => !cancelled && setItems(data.items.filter((t) => t.id !== exclude)))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [intent, exclude]);
  return items;
}

/** 编辑后的正文在停顿一段时间后重新回译；正文与草稿相同时直接用草稿的回译。 */
function useBackTranslation(draft: Draft, body: string) {
  const [state, setState] = useState({
    text: draft.back_translation_zh,
    pending: false,
    error: "",
  });
  const latest = useRef(body);
  latest.current = body;

  useEffect(() => {
    if (body === draft.body_ja) {
      setState({ text: draft.back_translation_zh, pending: false, error: "" });
      return;
    }
    setState((s) => ({ ...s, pending: true, error: "" }));
    const timer = setTimeout(() => {
      call(
        api.POST("/v1/drafts/{draft_id}/backtranslate", {
          params: { path: { draft_id: draft.id } },
          body: { body_ja: body },
        }),
      )
        .then((data) => {
          if (latest.current === body) {
            setState({ text: data.back_translation_zh, pending: false, error: "" });
          }
        })
        .catch((e: unknown) => {
          if (latest.current === body) {
            setState((s) => ({ ...s, pending: false, error: errorText(e) }));
          }
        });
    }, BACKTRANSLATE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [body, draft.id, draft.body_ja, draft.back_translation_zh]);
  return state;
}

function DraftDetails({ draft, intent }: { draft: Draft; intent: string | undefined }) {
  const ref = draft.template_refs?.[0];
  const template = useTemplate(ref?.template_id, ref?.template_version);
  const alternatives = useAlternatives(intent, ref?.template_id);
  const checks = draft.checks ?? [];
  return (
    <>
      <section className="card">
        <h4>
          分流：
          <RouteBadge route={draft.route} />
          <span className="meta">信心值 {draft.confidence.toFixed(2)}</span>
        </h4>
        <ul className="reasons">
          {draft.route_reasons.map((reason) => (
            <li key={reason}>
              <SafeText text={reason} />
            </li>
          ))}
        </ul>
        {draft.route === "auto_send" && (
          <p className="notice notice-info">建议可以直接发送，仍需要你确认后点击发送。</p>
        )}
      </section>

      <section className="card">
        <h4>模板</h4>
        {ref ? (
          <>
            <p>
              <strong>{template?.title_zh ?? ref.template_id}</strong>{" "}
              <span className="meta">
                {ref.template_id} · 版本 {ref.template_version}
              </span>
            </p>
            <p className="muted">
              <SafeText text={ref.reason_zh} />
            </p>
          </>
        ) : (
          <p className="muted">没有使用模板。</p>
        )}
        {alternatives.length > 0 && (
          <details>
            <summary>同一意图的其他模板（{alternatives.length}）</summary>
            <ul>
              {alternatives.map((t) => (
                <li key={t.id}>
                  <strong>{t.title_zh}</strong> <span className="meta">{t.id}</span>
                  <p className="muted">
                    适用：
                    <SafeText text={t.use_when_zh} />
                  </p>
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      {checks.length > 0 && (
        <section className="card">
          <h4>自检</h4>
          <ul className="checks">
            {checks.map((check) => (
              <li key={check.kind} className={check.passed ? "check-pass" : "check-fail"}>
                {check.passed ? "✓" : "✗"} {label(CHECK_LABELS, check.kind)}
                {check.detail_zh && (
                  <>
                    ：<SafeText text={check.detail_zh} />
                  </>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {((draft.citations ?? []).length > 0 || (draft.similar_case_refs ?? []).length > 0) && (
        <section className="card">
          {(draft.citations ?? []).length > 0 && (
            <>
              <h4>引用的知识</h4>
              <ul>
                {(draft.citations ?? []).map((c) => (
                  <li key={`${c.entry_type}:${c.entry_id}:${c.claim_zh}`}>
                    <SafeText text={c.claim_zh} />{" "}
                    <span className="meta">
                      {c.entry_type} {c.entry_id} v{c.entry_version}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {(draft.similar_case_refs ?? []).length > 0 && (
            <>
              <h4>相似的历史案件</h4>
              <ul>
                {(draft.similar_case_refs ?? []).map((s) => (
                  <li key={s.case_id}>
                    <a href={caseHref(s.case_id)}>{s.case_id}</a>{" "}
                    <span className="meta">相似度 {s.score.toFixed(3)}</span>
                    {s.reason_zh && (
                      <>
                        {" "}
                        <SafeText text={s.reason_zh} />
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {(draft.trace ?? []).length > 0 && (
        <details className="card">
          <summary>工具调用轨迹（{(draft.trace ?? []).length} 步）</summary>
          <ol>
            {(draft.trace ?? []).map((step) => (
              <li key={step.step}>
                <code>{step.tool}</code> {JSON.stringify(step.arguments ?? {})}
                {step.result_summary_zh && (
                  <p className="muted">
                    <SafeText text={step.result_summary_zh} />
                  </p>
                )}
                {step.error && <p className="check-fail">{step.error}</p>}
              </li>
            ))}
          </ol>
          {draft.fallback_reason && <p className="muted">回退原因：{draft.fallback_reason}</p>}
        </details>
      )}
    </>
  );
}

export function DraftPanel({
  detail,
  actor,
  onChanged,
}: {
  detail: CaseDetail;
  actor: string;
  onChanged: () => Promise<void>;
}) {
  const { case: thread, draft, analysis } = detail;
  const closed = CLOSED_STATUSES.has(thread.status ?? "open");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      await call(
        api.POST("/v1/cases/{case_id}/drafts", { params: { path: { case_id: thread.id } } }),
      );
      await onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="draft-panel">
      <div className="toolbar">
        <h3>回复草稿</h3>
        {!closed && (
          <button type="button" onClick={generate} disabled={busy}>
            {busy ? "正在起草…" : draft ? "重新生成" : "生成草稿"}
          </button>
        )}
      </div>
      <ErrorNotice message={error} />
      {notice && <p className="notice notice-ok">{notice}</p>}
      {!draft && !closed && <p className="muted">还没有草稿。点击“生成草稿”开始分析和起草。</p>}
      {draft && (
        <>
          <DraftDetails draft={draft} intent={analysis?.intent} />
          <DraftEditor
            key={draft.id}
            draft={draft}
            actor={actor}
            closed={closed}
            onDone={async (message) => {
              setNotice(message);
              await onChanged();
            }}
          />
          {(detail.feedback ?? []).length > 0 && (
            <section className="card">
              <h4>处理记录</h4>
              <ul>
                {(detail.feedback ?? []).map((f) => (
                  <li key={`${f.created_at}:${f.action}`}>
                    {formatTime(f.created_at)} {f.actor}：{label(ACTION_LABELS, f.action)}
                    {f.reason && (
                      <>
                        （<SafeText text={f.reason} />）
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function DraftEditor({
  draft,
  actor,
  closed,
  onDone,
}: {
  draft: Draft;
  actor: string;
  closed: boolean;
  onDone: (message: string) => Promise<void>;
}) {
  const [body, setBody] = useState(draft.body_ja);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const back = useBackTranslation(draft, body);
  const edited = body !== draft.body_ja;
  const hasReason = reason.trim().length > 0;
  const disabled = closed || busy || !actor.trim();

  const submit = async (action: FeedbackAction) => {
    setBusy(true);
    setError(null);
    try {
      const result = await call(
        api.POST("/v1/drafts/{draft_id}/feedback", {
          params: { path: { draft_id: draft.id } },
          body: {
            actor: actor.trim(),
            action,
            final_body_ja: action === "edited" ? body : null,
            reason: hasReason ? reason.trim() : null,
          },
        }),
      );
      await onDone(
        result.mail
          ? `已发送：回复已进入发件箱（${formatTime(result.mail.sent_at)}）。`
          : `已记录：${label(ACTION_LABELS, action)}。`,
      );
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <div className="side-by-side">
        <label className="field">
          <span>日语回复（可编辑）</span>
          <textarea
            lang="ja"
            rows={14}
            value={body}
            readOnly={closed}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
        <div className="field">
          <span>
            中文回译{back.pending && <span className="meta">（正在更新…）</span>}
            {edited && !back.pending && !back.error && (
              <span className="meta">（已按修改更新）</span>
            )}
          </span>
          <p className="body backtranslation" aria-live="polite" data-testid="backtranslation">
            <SafeText text={back.text} />
          </p>
          <ErrorNotice message={back.error || null} />
        </div>
      </div>
      {!closed && (
        <>
          <label className="field">
            <span>理由（升级和驳回时必填，修改后发送时可选）</span>
            <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <ErrorNotice message={error} />
          {!actor.trim() && <p className="notice notice-warn">请先在右上角填写当前客服。</p>}
          <div className="actions">
            {edited ? (
              <button
                type="button"
                className="primary"
                disabled={disabled || !body.trim()}
                onClick={() => submit("edited")}
              >
                修改后发送
              </button>
            ) : (
              <button
                type="button"
                className="primary"
                disabled={disabled}
                onClick={() => submit("approved")}
              >
                批准发送
              </button>
            )}
            {edited && (
              <button type="button" disabled={busy} onClick={() => setBody(draft.body_ja)}>
                撤销修改
              </button>
            )}
            <button
              type="button"
              disabled={disabled || !hasReason}
              onClick={() => submit("escalated")}
            >
              升级处理
            </button>
            <button
              type="button"
              className="danger"
              disabled={disabled || !hasReason}
              onClick={() => submit("rejected")}
            >
              驳回
            </button>
          </div>
        </>
      )}
    </section>
  );
}
