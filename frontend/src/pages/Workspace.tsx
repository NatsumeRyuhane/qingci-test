// 案件工作区：左栏是案件信息和分析，中栏是邮件线程（最新的在底部），右栏是回复编辑器，
// 底部是案件栏。处理完（发送、升级、无需回复）自动跳到队列中的下一个案件。
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  api,
  type CaseAnalysis,
  type CaseDetail,
  type CaseSummary,
  call,
  errorText,
  type InquiryForm,
  type Message,
} from "../api/client";
import { ErrorNotice, RouteBadge, StatusBadge } from "../components/Badges";
import { SafeText } from "../components/SafeText";
import { setFlash, takeFlash } from "../lib/flash";
import {
  CATEGORY_LABELS,
  CLOSED_STATUSES,
  formatTime,
  label,
  ROLE_LABELS,
  SENTIMENT_LABELS,
  STATUS_LABELS,
  URGENCY_LABELS,
} from "../lib/labels";
import { usePolling } from "../lib/polling";
import { fetchQueue, nextCaseId } from "../lib/queue";
import { caseHref } from "../lib/route";
import { DraftingProgress, Editor, GeneratePrompt } from "./Editor";

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

function CaseInfo({ detail }: { detail: CaseDetail }) {
  const { case: thread, analysis, draft } = detail;
  const form = thread.form;
  const facts = Object.entries(analysis?.facts ?? {});
  return (
    <>
      <header className="case-title">
        <h2>{thread.id}</h2>
        <StatusBadge status={thread.status ?? "open"} />
        {draft && <RouteBadge route={draft.route} />}
      </header>
      {analysis ? (
        <section className="card">
          <h3>摘要</h3>
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
            <dd className={analysis.urgency === "high" ? "urgency-high" : undefined}>
              {label(URGENCY_LABELS, analysis.urgency)}
            </dd>
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
              建议不回复：
              <SafeText text={analysis.no_reply.reason_zh} />
              {analysis.no_reply.duplicate_of &&
                `（重复提交，对应线程 ${analysis.no_reply.duplicate_of}）`}
            </p>
          )}
        </section>
      ) : (
        <p className="muted">还没有分析。生成草稿时会一起分析。</p>
      )}
      <section className="card">
        <h3>游戏内表单</h3>
        {form ? (
          <>
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
          </>
        ) : (
          <p className="muted">没有表单（不是通过游戏内表单提交的）。</p>
        )}
      </section>
    </>
  );
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
        {analyzed && <span className="badge badge-muted">本次回复的邮件</span>}
      </header>
      <p className="body" lang="ja">
        <SafeText text={message.body || "（正文为空）"} />
      </p>
      {analyzed && analysis && (
        <div className="translation">
          <span className="meta">中文翻译</span>
          <p className="body">
            <SafeText text={analysis.translation_zh} />
          </p>
        </div>
      )}
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

function Thread({ detail }: { detail: CaseDetail }) {
  const end = useRef<HTMLDivElement>(null);
  const count = detail.case.messages.length;
  // 默认停在最新的邮件（底部）；邮件数变化时（追问）再滚到底部
  useLayoutEffect(() => {
    if (count > 0) end.current?.scrollIntoView?.({ block: "end" });
  }, [count]);
  return (
    <>
      <h3 className="column-title">邮件线程（{count} 封）</h3>
      {detail.case.messages.map((message) => (
        <MessageCard key={message.seq} message={message} analysis={detail.analysis ?? null} />
      ))}
      <div ref={end} />
    </>
  );
}

export function CaseBar({ currentId, queue }: { currentId: string; queue: CaseSummary[] }) {
  const index = queue.findIndex((item) => item.id === currentId);
  const previous = index > 0 ? queue[index - 1] : null;
  const next = index >= 0 ? queue[index + 1] : queue.find((item) => item.id !== currentId);
  const nextTodo = queue.find((item) => item.id !== currentId);
  const go = (id: string) => {
    window.location.hash = caseHref(id);
  };
  return (
    <nav className="case-bar" aria-label="案件导航">
      <a href="#inbox">全部案件</a>
      <button type="button" disabled={!previous} onClick={() => previous && go(previous.id)}>
        ← 上一个
      </button>
      <label className="field-inline grow">
        <span className="visually-hidden">切换案件</span>
        <select
          value={index >= 0 ? currentId : ""}
          onChange={(e) => e.target.value && go(e.target.value)}
        >
          {index < 0 && <option value="">（当前案件不在待处理队列中）</option>}
          {queue.map((item) => (
            <option key={item.id} value={item.id}>
              {item.id} · {label(STATUS_LABELS, item.status)}
              {item.summary_zh ? ` · ${item.summary_zh.slice(0, 30)}` : ""}
            </option>
          ))}
        </select>
      </label>
      <button type="button" disabled={!next} onClick={() => next && go(next.id)}>
        下一个 →
      </button>
      <span className="meta">待处理 {queue.length} 个</span>
      <button
        type="button"
        className="primary"
        disabled={!nextTodo}
        onClick={() => nextTodo && go(nextTodo.id)}
      >
        下一个待处理 ⏭
      </button>
    </nav>
  );
}

export function Workspace({ caseId, actor }: { caseId: string; actor: string }) {
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [queue, setQueue] = useState<CaseSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlashText] = useState<string | null>(() => takeFlash());
  const [newMail, setNewMail] = useState(false);

  const load = useCallback(async () => {
    try {
      setDetail(
        await call(api.GET("/v1/cases/{case_id}", { params: { path: { case_id: caseId } } })),
      );
      setNewMail(false);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [caseId]);

  const loadQueue = useCallback(() => {
    fetchQueue()
      .then((q) => setQueue(q.items))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    void load();
    loadQueue();
  }, [load, loadQueue]);

  // 定时刷新。没有在编辑的内容时（还没有草稿、或已结案）直接换成最新数据，自动起草完成后草稿会
  // 自己出现；正在编辑时只更新起草状态，有新来信或新草稿时提示，不打断编辑。
  usePolling(() => {
    loadQueue();
    call(api.GET("/v1/cases/{case_id}", { params: { path: { case_id: caseId } } }))
      .then((fresh) => {
        setDetail((current) => {
          if (!current) return fresh;
          const editing = current.draft && !CLOSED_STATUSES.has(current.case.status ?? "open");
          if (!editing) return fresh;
          if (fresh.case.messages.length > current.case.messages.length) setNewMail(true);
          if (fresh.draft && fresh.draft.id !== current.draft?.id) setNewMail(true);
          return { ...current, drafting: fresh.drafting };
        });
      })
      .catch(() => undefined);
  }, 3000);

  const resolved = async (message: string) => {
    const next = await nextCaseId(caseId).catch(() => null);
    if (next) {
      setFlash(`${message}。已转到下一个待处理的案件。`);
      window.location.hash = caseHref(next);
    } else {
      setFlash(`${message}。待处理队列已清空。`);
      window.location.hash = "#inbox";
    }
  };

  return (
    <div className="workspace">
      {(flash || newMail || error) && (
        <div className="workspace-notices">
          {flash && (
            <p className="notice notice-ok">
              {flash}{" "}
              <button type="button" className="link" onClick={() => setFlashText(null)}>
                知道了
              </button>
            </p>
          )}
          {newMail && (
            <p className="notice notice-warn">
              这个案件有新的来信或新的草稿。{" "}
              <button type="button" className="link" onClick={() => void load()}>
                刷新（未发送的修改会丢失）
              </button>
            </p>
          )}
          <ErrorNotice message={error} />
        </div>
      )}
      {detail ? (
        <div className="ws-columns">
          <aside className="ws-column ws-left" aria-label="案件信息">
            <CaseInfo detail={detail} />
          </aside>
          <section className="ws-column ws-middle" aria-label="邮件线程">
            <Thread detail={detail} />
          </section>
          <section className="ws-column ws-right" aria-label="回复">
            {detail.draft && detail.drafting && detail.drafting.state !== "failed" && (
              <p className="notice notice-info">
                <span className="spinner" aria-hidden="true" /> 正在为新的来信重新起草…
              </p>
            )}
            {detail.draft ? (
              <Editor
                key={detail.draft.id}
                detail={detail}
                actor={actor}
                onResolved={resolved}
                onChanged={load}
              />
            ) : CLOSED_STATUSES.has(detail.case.status ?? "open") ? (
              <p className="editor-empty">
                案件已结案（{label(STATUS_LABELS, detail.case.status ?? "open")}），没有草稿。
              </p>
            ) : detail.drafting && detail.drafting.state !== "failed" ? (
              <DraftingProgress
                since={detail.drafting.since}
                queued={detail.drafting.state === "queued"}
              />
            ) : (
              <GeneratePrompt
                caseId={caseId}
                onGenerated={load}
                failure={detail.drafting?.error ?? null}
              />
            )}
          </section>
        </div>
      ) : (
        !error && <p className="ws-loading">正在加载…</p>
      )}
      <CaseBar currentId={caseId} queue={queue} />
    </div>
  );
}
