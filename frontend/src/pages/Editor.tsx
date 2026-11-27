// 回复编辑器（工作台右栏）。客服用中文工作：中文在左、日语在右，从左到右完成一个案件。
//
// - 编辑中文后点“翻译为日语”，由 LLM 译成日语，并对译文自检。
// - 直接改日语会先弹出提醒：改过的日语以客服为准，不会发给 LLM，也不会被中文的翻译覆盖；
//   保存时只运行代码自检。
// - 发送前，日语正文有未通过的自检时，需要逐项确认后才能发送（后端同样会检查）。
import { type KeyboardEvent, type ReactNode, useEffect, useState } from "react";
import {
  api,
  type CaseDetail,
  call,
  type Draft,
  type DraftCheck,
  errorText,
  type FeedbackAction,
  type ReplyTemplate,
} from "../api/client";
import { ErrorNotice, RouteBadge } from "../components/Badges";
import { ParagraphPairs } from "../components/ParagraphPairs";
import { SafeText } from "../components/SafeText";
import { ACTION_LABELS, CHECK_LABELS, CLOSED_STATUSES, formatTime, label } from "../lib/labels";
import { joinParagraphs, replaceParagraph, splitParagraphs } from "../lib/paragraphs";

type SecondaryAction = Extract<FeedbackAction, "escalated" | "rejected" | "no_reply">;

const SECONDARY: { action: SecondaryAction; text: string; hint: string }[] = [
  { action: "escalated", text: "升级处理", hint: "交给高级客服或主管" },
  { action: "rejected", text: "驳回草稿", hint: "草稿不可用，案件回到待处理，可重新生成" },
  { action: "no_reply", text: "无需回复", hint: "例如重复提交、垃圾邮件，直接结案" },
];

function useTemplate(draft: Draft) {
  const ref = draft.template_refs?.[0];
  const [template, setTemplate] = useState<ReplyTemplate | null>(null);
  useEffect(() => {
    if (!ref) return;
    let cancelled = false;
    call(
      api.GET("/v1/templates/{template_id}", {
        params: {
          path: { template_id: ref.template_id },
          query: { version: ref.template_version },
        },
      }),
    )
      .then((data) => !cancelled && setTemplate(data))
      .catch(() => undefined); // 模板只是参考信息
    return () => {
      cancelled = true;
    };
  }, [ref]);
  return template;
}

function Checks({ checks, stale }: { checks: DraftCheck[]; stale: boolean }) {
  const failed = checks.filter((c) => !c.passed);
  return (
    <section className={`checks-panel${failed.length ? " has-failures" : ""}`} aria-label="自检">
      <header>
        <strong>自检</strong>
        {stale ? (
          <span className="meta">日语已修改，保存后更新</span>
        ) : failed.length ? (
          <span className="check-fail">{failed.length} 项未通过</span>
        ) : (
          <span className="check-pass">全部通过</span>
        )}
      </header>
      <ul>
        {checks.map((check) => (
          <li key={check.kind} className={check.passed ? "check-pass" : "check-fail"}>
            {check.passed ? "✓" : "✗"} {label(CHECK_LABELS, check.kind)}
            {check.detail_zh && !check.passed && (
              <>
                ：<SafeText text={check.detail_zh} />
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function DraftSummary({ draft }: { draft: Draft }) {
  const template = useTemplate(draft);
  const ref = draft.template_refs?.[0];
  return (
    <details className="draft-summary">
      <summary>
        <RouteBadge route={draft.route} />
        <span className="meta">信心值 {draft.confidence.toFixed(2)}</span>
        {ref && <span className="meta">模板：{template?.title_zh ?? ref.template_id}</span>}
      </summary>
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
      {ref && (
        <p className="muted">
          {ref.template_id} · 版本 {ref.template_version}：<SafeText text={ref.reason_zh} />
        </p>
      )}
      {(draft.citations ?? []).length > 0 && (
        <ul>
          {(draft.citations ?? []).map((c) => (
            <li key={`${c.entry_id}:${c.claim_zh}`}>
              <SafeText text={c.claim_zh} />{" "}
              <span className="meta">
                {c.entry_type} {c.entry_id} v{c.entry_version}
              </span>
            </li>
          ))}
        </ul>
      )}
      {(draft.trace ?? []).length > 0 && (
        <p className="meta">
          工具调用 {(draft.trace ?? []).length} 步
          {draft.fallback_reason && `，已回退：${draft.fallback_reason}`}
        </p>
      )}
    </details>
  );
}

function Dialog({
  title,
  children,
  confirm,
  onConfirm,
  onCancel,
}: {
  title: string;
  children: ReactNode;
  confirm: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="dialog-backdrop">
      <div className="dialog" role="alertdialog" aria-modal="true" aria-label={title}>
        <h4>{title}</h4>
        <div>{children}</div>
        <div className="actions end">
          <button type="button" onClick={onCancel}>
            取消
          </button>
          <button type="button" className="primary" onClick={onConfirm}>
            {confirm}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 后台自动起草进行中。完成后工作区的定时刷新会自动换成草稿。 */
export function DraftingProgress({ since, queued }: { since: string; queued: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const seconds = Math.max(0, Math.round((now - new Date(since).getTime()) / 1000));
  return (
    <div className="editor-empty drafting" role="status">
      <span className="spinner large" aria-hidden="true" />
      <p>
        <strong>{queued ? "排队等待自动起草…" : "正在自动分析和起草…"}</strong>
        <span className="meta"> 已等待 {seconds} 秒</span>
      </p>
      <p className="meta">
        来信后会自动开始，通常需要 1 分钟左右。完成后草稿会自动显示，不需要刷新。
      </p>
    </div>
  );
}

export function GeneratePrompt({
  caseId,
  onGenerated,
  failure = null,
}: {
  caseId: string;
  onGenerated: () => Promise<void>;
  failure?: string | null;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      await call(api.POST("/v1/cases/{case_id}/drafts", { params: { path: { case_id: caseId } } }));
      await onGenerated();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <div className="editor-empty">
      {failure ? (
        <p className="notice notice-error">自动起草失败：{failure}</p>
      ) : (
        <p>还没有草稿。</p>
      )}
      <ErrorNotice message={error} />
      <button type="button" className="primary" onClick={generate} disabled={busy}>
        {busy ? (
          <>
            <span className="spinner" aria-hidden="true" /> 正在分析和起草…
          </>
        ) : failure ? (
          "重试"
        ) : (
          "生成草稿"
        )}
      </button>
    </div>
  );
}

export function Editor({
  detail,
  actor,
  onResolved,
  onChanged,
}: {
  detail: CaseDetail;
  actor: string;
  onResolved: (message: string) => Promise<void>;
  onChanged: () => Promise<void>;
}) {
  const draft = detail.draft as Draft;
  const closed = CLOSED_STATUSES.has(detail.case.status ?? "open");
  const [zhParts, setZhParts] = useState(() => splitParagraphs(draft.back_translation_zh));
  const [jaParts, setJaParts] = useState(() => splitParagraphs(draft.body_ja));
  const zh = joinParagraphs(zhParts);
  const ja = joinParagraphs(jaParts);
  const [zhDirty, setZhDirty] = useState(false);
  const [jaUnlocked, setJaUnlocked] = useState(false);
  const [jaManual, setJaManual] = useState(false);
  const [checks, setChecks] = useState<DraftCheck[]>(draft.checks ?? []);
  const [checksStale, setChecksStale] = useState(false);
  const [dialog, setDialog] = useState<"unlock-ja" | "overwrite-ja" | null>(null);
  const [pending, setPending] = useState<SecondaryAction | null>(null);
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [justTranslated, setJustTranslated] = useState(0);
  useEffect(() => {
    if (!justTranslated) return;
    const id = setTimeout(() => setJustTranslated(0), 1200);
    return () => clearTimeout(id);
  }, [justTranslated]);

  const failed = checks.filter((c) => !c.passed);
  const disabled = closed || busy !== null || !actor.trim();

  const run = async <T,>(what: string, task: () => Promise<T>): Promise<T | undefined> => {
    setBusy(what);
    setError(null);
    try {
      return await task();
    } catch (e) {
      setError(errorText(e));
      return undefined;
    } finally {
      setBusy(null);
    }
  };

  const translate = async () => {
    setDialog(null);
    const result = await run("正在翻译…", () =>
      call(
        api.POST("/v1/drafts/{draft_id}/translate", {
          params: { path: { draft_id: draft.id } },
          body: { body_zh: zh },
        }),
      ),
    );
    if (!result) return;
    setJaParts(splitParagraphs(result.body_ja));
    setChecks(result.checks);
    setChecksStale(false);
    setZhDirty(false);
    setJaManual(false);
    setJaUnlocked(false);
    setConfirming(false);
    setJustTranslated(Date.now());
  };

  const requestTranslate = () => {
    if (!zh.trim()) return;
    if (jaManual) setDialog("overwrite-ja");
    else void translate();
  };

  /** 只运行代码自检，不调用 LLM。返回最新的自检结果。 */
  const recheck = async (): Promise<DraftCheck[] | undefined> => {
    if (!checksStale) return checks;
    const result = await run("正在自检…", () =>
      call(
        api.POST("/v1/drafts/{draft_id}/check", {
          params: { path: { draft_id: draft.id } },
          body: { body_ja: ja },
        }),
      ),
    );
    if (!result) return undefined;
    setChecks(result.checks);
    setChecksStale(false);
    return result.checks;
  };

  const submit = async (action: FeedbackAction, confirmedChecks: DraftCheck[] = []) => {
    const sends = action === "approved" || action === "edited";
    const result = await run("正在提交…", () =>
      call(
        api.POST("/v1/drafts/{draft_id}/feedback", {
          params: { path: { draft_id: draft.id } },
          body: {
            actor: actor.trim(),
            action,
            final_body_ja: action === "edited" ? ja : null,
            final_body_zh: sends && zh !== draft.back_translation_zh ? zh : null,
            ja_edited_directly: sends && jaManual,
            confirmed_failed_checks: confirmedChecks.map((c) => c.kind),
            reason: reason.trim() || null,
          },
        }),
      ),
    );
    if (!result) return;
    if (action === "rejected") {
      await onChanged();
      return;
    }
    const what =
      action === "approved" || action === "edited"
        ? `已发送 ${draft.case_id}（回复已进入发件箱）`
        : `${label(ACTION_LABELS, action)}：${draft.case_id}`;
    await onResolved(what);
  };

  const send = async () => {
    if (zhDirty) {
      setError("中文有修改还没有翻译成日语。请先点“翻译为日语”，或撤销中文的修改。");
      return;
    }
    const latest = await recheck();
    if (!latest) return;
    const failing = latest.filter((c) => !c.passed);
    if (failing.length && !(confirming && confirmed)) {
      setConfirming(true);
      setConfirmed(false);
      return;
    }
    await submit(ja === draft.body_ja ? "approved" : "edited", failing);
  };

  const onZhKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      requestTranslate();
    }
  };

  if (closed) {
    return (
      <div className="editor-empty">
        <p>案件已结案。</p>
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
      </div>
    );
  }

  return (
    <div className="editor">
      <Checks checks={checks} stale={checksStale} />
      <DraftSummary draft={draft} />

      <ParagraphPairs
        zh={zhParts.blocks}
        ja={jaParts.blocks}
        onZhChange={(i, value) => {
          setZhParts((p) => replaceParagraph(p, i, value));
          setZhDirty(true);
        }}
        onZhBlur={() => setZhParts((p) => splitParagraphs(joinParagraphs(p)))}
        onZhKeyDown={onZhKey}
        onJaChange={(i, value) => {
          setJaParts((p) => replaceParagraph(p, i, value));
          setJaManual(true);
          setChecksStale(true);
          setConfirming(false);
        }}
        onJaBlur={() => {
          setJaParts((p) => splitParagraphs(joinParagraphs(p)));
          void recheck();
        }}
        jaLocked={!jaUnlocked}
        onJaLockedAttempt={() => setDialog("unlock-ja")}
        jaClassName={justTranslated ? "just-updated" : undefined}
        zhHeader={
          <>
            中文（编辑这里）
            {zhDirty && <span className="badge badge-warn">未翻译</span>}
          </>
        }
        jaHeader={
          <>
            日语（发送的正文）
            {jaManual && <span className="badge badge-warn">已直接修改</span>}
          </>
        }
      />
      <div className="pair-foot">
        <div className="actions">
          <button
            type="button"
            className="primary"
            disabled={!zhDirty || !zh.trim() || busy !== null}
            onClick={requestTranslate}
          >
            翻译为日语 →
          </button>
          {zhDirty && (
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => {
                setZhParts(splitParagraphs(draft.back_translation_zh));
                setZhDirty(false);
              }}
            >
              撤销中文修改
            </button>
          )}
          <span className="meta">Ctrl / ⌘ + Enter</span>
        </div>
        <div className="actions">
          {jaManual && (
            <button type="button" disabled={busy !== null || !checksStale} onClick={recheck}>
              保存日语修改并自检
            </button>
          )}
          {!jaUnlocked && <span className="meta">日语由中文翻译生成；点击可直接修改</span>}
        </div>
      </div>

      <div className="editor-footer">
        <ErrorNotice message={error} />
        {!actor.trim() && <p className="notice notice-warn">请先在页头填写当前客服。</p>}
        {pending && (
          <div className="reason-box">
            <label className="field">
              <span>{SECONDARY.find((s) => s.action === pending)?.text}的理由（必填）</span>
              <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
            <div className="actions end">
              <button type="button" onClick={() => setPending(null)}>
                取消
              </button>
              <button
                type="button"
                className={pending === "escalated" ? "primary" : "danger"}
                disabled={disabled || !reason.trim()}
                onClick={() => void submit(pending)}
              >
                确认{SECONDARY.find((s) => s.action === pending)?.text}
              </button>
            </div>
          </div>
        )}
        {confirming && failed.length > 0 && (
          <div className="notice notice-error confirm-box">
            <strong>以下自检未通过：</strong>
            <ul>
              {failed.map((c) => (
                <li key={c.kind}>
                  {label(CHECK_LABELS, c.kind)}
                  {c.detail_zh && (
                    <>
                      ：<SafeText text={c.detail_zh} />
                    </>
                  )}
                </li>
              ))}
            </ul>
            <label className="field-inline">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              我已核对，仍然发送
            </label>
          </div>
        )}
        <div className="action-bar">
          <div className="secondary-actions">
            {SECONDARY.map((s) => (
              <button
                key={s.action}
                type="button"
                title={s.hint}
                disabled={disabled}
                aria-pressed={pending === s.action}
                onClick={() => {
                  setPending(s.action);
                  setConfirming(false);
                }}
              >
                {s.text}
              </button>
            ))}
          </div>
          {busy && <span className="meta">{busy}</span>}
          <button
            type="button"
            className="primary send"
            disabled={disabled || (confirming && failed.length > 0 && !confirmed)}
            onClick={() => void send()}
          >
            {confirming && failed.length > 0 ? "仍然发送" : "确认发送"}
          </button>
        </div>
      </div>

      {dialog === "unlock-ja" && (
        <Dialog
          title="直接修改日语？"
          confirm="直接修改日语"
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            setJaUnlocked(true);
            setDialog(null);
          }}
        >
          <p>直接修改后，日语以你的修改为准：</p>
          <ul>
            <li>不会发给 LLM，也不会被中文的翻译覆盖；</li>
            <li>中文一侧不会自动更新，可能与日语不一致；</li>
            <li>保存时只运行代码自检。</li>
          </ul>
          <p>如果只是想改内容，建议在左边改中文后再翻译。</p>
        </Dialog>
      )}
      {dialog === "overwrite-ja" && (
        <Dialog
          title="覆盖对日语的直接修改？"
          confirm="翻译并覆盖"
          onCancel={() => setDialog(null)}
          onConfirm={() => void translate()}
        >
          <p>你直接改过日语。按中文重新翻译会覆盖这些修改。</p>
        </Dialog>
      )}
    </div>
  );
}
