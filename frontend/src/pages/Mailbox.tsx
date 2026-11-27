// 模拟邮箱（演示用）：代替真实的邮件系统。来信从这里投递，系统发出的回复都留在发件箱，
// 不会真正发出任何邮件。
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
import { CATEGORY_LABELS, formatTime, label, STATUS_LABELS } from "../lib/labels";
import { caseHref } from "../lib/route";

/** 示例来信。SYNTHETIC：全部是手写的合成内容，不来自样本（D-002）。 */
export const EXAMPLES: { name: string; category: InquiryCategory; body: string }[] = [
  {
    name: "道谢（常规）",
    category: "feedback",
    body: "いつも楽しく遊ばせていただいています。\n先日のイベントもとても面白かったです。これからも応援しています！",
  },
  {
    name: "游戏故障（需要修改）",
    category: "bug",
    body: "昨日のアップデートの後から、ステージ3をクリアするとアプリが落ちてしまいます。\n何度か再起動しましたが直りません。",
  },
  {
    name: "购买未到账（升级）",
    category: "purchase",
    body: "ジェムを購入したのですが、アイテムに反映されていません。\n代金は引き落とされているので、至急確認をお願いします。",
  },
];

type Draft = {
  target: string;
  body: string;
  category: InquiryCategory | "";
  device: string;
  appVersion: string;
  occurredAt: string;
};

const EMPTY: Draft = {
  target: "",
  body: "",
  category: "",
  device: "",
  appVersion: "",
  occurredAt: "",
};

function Compose({ cases, onSent }: { cases: CaseSummary[]; onSent: () => void }) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ caseId: string; seq: number } | null>(null);
  const followUp = draft.target !== "";
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));

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
      const sent = await call(
        api.POST("/v1/sim/inbox", {
          body: {
            body: draft.body,
            case_id: followUp ? draft.target : null,
            form: !followUp && Object.keys(form).length > 0 ? form : null,
          },
        }),
      );
      setResult({ caseId: sent.case_id, seq: sent.message_seq });
      setDraft({ ...EMPTY, target: draft.target });
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
      <div className="examples">
        示例：
        {EXAMPLES.map((example) => (
          <button
            key={example.name}
            type="button"
            onClick={() => set({ target: "", body: example.body, category: example.category })}
          >
            {example.name}
          </button>
        ))}
      </div>
      <label className="field">
        <span>投递到</span>
        <select value={draft.target} onChange={(e) => set({ target: e.target.value })}>
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
          lang="ja"
          rows={6}
          required
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
            <input value={draft.device} onChange={(e) => set({ device: e.target.value })} />
          </label>
          <label className="field">
            <span>应用版本</span>
            <input value={draft.appVersion} onChange={(e) => set({ appVersion: e.target.value })} />
          </label>
          <label className="field">
            <span>发生时间</span>
            <input
              placeholder="例如 2026-11-27 09:00"
              value={draft.occurredAt}
              onChange={(e) => set({ occurredAt: e.target.value })}
            />
          </label>
        </fieldset>
      )}
      <ErrorNotice message={error} />
      {result && (
        <p className="notice notice-ok">
          已投递到 <a href={caseHref(result.caseId)}>{result.caseId}</a>（第 {result.seq} 封）。
        </p>
      )}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy || !draft.body.trim()}>
          投递
        </button>
      </div>
    </form>
  );
}

function Outbox({ items, total }: { items: OutboundMail[]; total: number }) {
  return (
    <section className="card">
      <h3>发件箱（{total}）</h3>
      {items.length === 0 && <p className="muted">还没有发出的回复。</p>}
      <ul className="outbox">
        {items.map((mail) => (
          <li key={mail.id}>
            <header>
              <a href={caseHref(mail.case_id)}>{mail.case_id}</a>
              <span className="meta">
                #{mail.message_seq} · {mail.sent_by} · {formatTime(mail.sent_at)}
              </span>
            </header>
            <details>
              <summary>正文</summary>
              <p className="body" lang="ja">
                <SafeText text={mail.body_ja} />
              </p>
            </details>
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

  return (
    <section>
      <div className="toolbar">
        <h2>模拟邮箱</h2>
        <span className="badge badge-warn">演示用</span>
        <span className="meta">来信进入工作台收件箱；系统发出的回复只进发件箱，不会真正发送。</span>
      </div>
      <ErrorNotice message={error} />
      <div className="two-columns">
        <Compose cases={cases} onSent={reload} />
        <Outbox {...outbox} />
      </div>
    </section>
  );
}
