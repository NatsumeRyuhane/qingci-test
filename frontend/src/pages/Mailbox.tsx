// 模拟邮箱（演示用）：代替真实的邮件系统。来信从这里投递，系统发出的回复都留在发件箱，
// 不会真正发出任何邮件。作为独立页面打开，演示时可以和工作台并排摆放。
import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  api,
  type CaseSummary,
  call,
  errorText,
  type InquiryCategory,
  type OutboundMail,
} from "../api/client";
import { ErrorNotice } from "../components/Badges";
import { SafeText } from "../components/SafeText";
import { EXAMPLE_GROUPS, EXAMPLES } from "../lib/examples";
import { CATEGORY_LABELS, formatTime, label, STATUS_LABELS } from "../lib/labels";
import { usePolling } from "../lib/polling";
import { caseHref } from "../lib/route";

type Compose = {
  target: string;
  body: string;
  category: InquiryCategory | "";
  device: string;
  appVersion: string;
  occurredAt: string;
};

const EMPTY: Compose = {
  target: "",
  body: "",
  category: "",
  device: "",
  appVersion: "",
  occurredAt: "",
};

function ComposeForm({ cases, onSent }: { cases: CaseSummary[]; onSent: () => void }) {
  const [draft, setDraft] = useState<Compose>(EMPTY);
  const [example, setExample] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<{ caseId: string; seq: number }[]>([]);
  const followUp = draft.target !== "";
  const set = (patch: Partial<Compose>) => setDraft((d) => ({ ...d, ...patch }));
  const note = EXAMPLES.find((e) => e.name === example)?.note;

  const pick = (name: string) => {
    setExample(name);
    const chosen = EXAMPLES.find((e) => e.name === name);
    if (!chosen) return;
    setDraft({
      ...EMPTY,
      body: chosen.body,
      category: chosen.form?.category ?? "",
      device: chosen.form?.device ?? "",
      appVersion: chosen.form?.app_version ?? "",
      occurredAt: chosen.form?.occurred_at ?? "",
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const form = {
      ...(draft.category && { category: draft.category }),
      ...(draft.device && { device: draft.device }),
      ...(draft.appVersion && { app_version: draft.appVersion }),
      ...(draft.occurredAt && { occurred_at: draft.occurredAt }),
    };
    try {
      const result = await call(
        api.POST("/v1/sim/inbox", {
          body: {
            body: draft.body,
            case_id: followUp ? draft.target : null,
            form: !followUp && Object.keys(form).length > 0 ? form : null,
          },
        }),
      );
      setSent((list) => [{ caseId: result.case_id, seq: result.message_seq }, ...list].slice(0, 8));
      onSent();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit}>
      <h3>模拟玩家来信</h3>
      <label className="field">
        <span>示例来信（合成数据）</span>
        <select id="example" value={example} onChange={(e) => pick(e.target.value)}>
          <option value="">— 选择一个示例，或直接手写 —</option>
          {EXAMPLE_GROUPS.map((group) => (
            <optgroup key={group} label={group}>
              {EXAMPLES.filter((e) => e.group === group).map((e) => (
                <option key={e.name} value={e.name}>
                  {e.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      {note && <p className="notice notice-info">演示要点：{note}</p>}
      <label className="field">
        <span>投递到</span>
        <select id="target" value={draft.target} onChange={(e) => set({ target: e.target.value })}>
          <option value="">新线程</option>
          {cases.map((c) => (
            <option key={c.id} value={c.id}>
              追问 {c.id}（{label(STATUS_LABELS, c.status)}）
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>正文（日语）</span>
        <textarea
          id="body"
          lang="ja"
          rows={8}
          value={draft.body}
          onChange={(e) => set({ body: e.target.value })}
        />
      </label>
      {!followUp && (
        <fieldset className="form-grid">
          <legend>游戏内表单（可选，只能附在新线程的第 1 封）</legend>
          <label className="field">
            <span>类别</span>
            <select
              id="category"
              value={draft.category}
              onChange={(e) => set({ category: e.target.value as InquiryCategory | "" })}
            >
              <option value="">（不填）</option>
              {(Object.entries(CATEGORY_LABELS) as [InquiryCategory, string][]).map(([v, t]) => (
                <option key={v} value={v}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>设备</span>
            <input
              id="device"
              value={draft.device}
              onChange={(e) => set({ device: e.target.value })}
            />
          </label>
          <label className="field">
            <span>应用版本</span>
            <input
              id="app-version"
              value={draft.appVersion}
              onChange={(e) => set({ appVersion: e.target.value })}
            />
          </label>
          <label className="field">
            <span>发生时间</span>
            <input
              id="occurred-at"
              placeholder="例如 2026-11-27 09:00"
              value={draft.occurredAt}
              onChange={(e) => set({ occurredAt: e.target.value })}
            />
          </label>
        </fieldset>
      )}
      <ErrorNotice message={error} />
      <div className="actions end">
        <button type="submit" className="primary" disabled={busy || !draft.body.trim()}>
          投递
        </button>
      </div>
      {sent.length > 0 && (
        <div className="sent-log">
          <span className="meta">本页投递的来信：</span>
          <ul>
            {sent.map((s) => (
              <li key={`${s.caseId}:${s.seq}`}>
                <a href={caseHref(s.caseId)} target="_blank" rel="noopener">
                  {s.caseId}
                </a>{" "}
                第 {s.seq} 封
              </li>
            ))}
          </ul>
        </div>
      )}
    </form>
  );
}

function Outbox({ items, total }: { items: OutboundMail[]; total: number }) {
  return (
    <section className="card">
      <h3>发件箱（{total}）</h3>
      <p className="meta">系统发出的回复都在这里，不会真正发送。每 5 秒自动刷新。</p>
      {items.length === 0 && <p className="muted">还没有发出的回复。</p>}
      <ul className="outbox">
        {items.map((mail) => (
          <li key={mail.id}>
            <header>
              <a href={caseHref(mail.case_id)} target="_blank" rel="noopener">
                {mail.case_id}
              </a>
              <span className="meta">
                #{mail.message_seq} · {mail.sent_by} · {formatTime(mail.sent_at)}
              </span>
            </header>
            <p className="body" lang="ja">
              <SafeText text={mail.body_ja} />
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Mailbox() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [outbox, setOutbox] = useState<{ items: OutboundMail[]; total: number }>({
    items: [],
    total: 0,
  });
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([
      call(api.GET("/v1/cases", { params: { query: { limit: 200 } } })),
      call(api.GET("/v1/sim/outbox", { params: { query: { limit: 50 } } })),
    ])
      .then(([caseList, mails]) => {
        setCases(caseList.items);
        setOutbox(mails);
        setError(null);
      })
      .catch((e: unknown) => setError(errorText(e)));
  }, []);

  useEffect(reload, [reload]);
  usePolling(reload);

  return (
    <section>
      <p className="meta">
        来信进入工作台的收件箱；系统发出的回复只进发件箱，不会真正发送。示例都是合成数据。
      </p>
      <ErrorNotice message={error} />
      <div className="two-columns">
        <ComposeForm cases={cases} onSent={reload} />
        <Outbox {...outbox} />
      </div>
    </section>
  );
}
