import { useEffect, useState } from "react";
import {
  api,
  type CaseList,
  type CaseStatus,
  call,
  errorText,
  type InquiryCategory,
  type Route,
} from "../api/client";
import { ErrorNotice, RouteBadge, StatusBadge } from "../components/Badges";
import { SafeText } from "../components/SafeText";
import {
  CATEGORY_LABELS,
  formatTime,
  label,
  ROUTE_LABELS,
  STATUS_LABELS,
  URGENCY_LABELS,
} from "../lib/labels";
import { caseHref } from "../lib/route";

const PAGE_SIZE = 50;

type Filters = { status: CaseStatus | ""; route: Route | ""; category: InquiryCategory | "" };

function Select<T extends string>(props: {
  label: string;
  value: T | "";
  options: Record<T, string>;
  onChange: (value: T | "") => void;
}) {
  return (
    <label className="field-inline">
      {props.label}
      <select value={props.value} onChange={(e) => props.onChange(e.target.value as T | "")}>
        <option value="">全部</option>
        {(Object.entries(props.options) as [T, string][]).map(([value, text]) => (
          <option key={value} value={value}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Inbox() {
  const [filters, setFilters] = useState<Filters>({ status: "", route: "", category: "" });
  const [offset, setOffset] = useState(0);
  const [list, setList] = useState<CaseList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const query = {
      limit: PAGE_SIZE,
      offset,
      ...(filters.status && { status: filters.status }),
      ...(filters.route && { route: filters.route }),
      ...(filters.category && { category: filters.category }),
    };
    call(api.GET("/v1/cases", { params: { query } }))
      .then((data) => {
        if (!cancelled) {
          setList(data);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errorText(e));
      });
    return () => {
      cancelled = true;
    };
  }, [filters, offset]);

  const update = (patch: Partial<Filters>) => {
    setFilters((current) => ({ ...current, ...patch }));
    setOffset(0);
  };

  return (
    <section>
      <div className="toolbar">
        <h2>收件箱</h2>
        <Select
          label="状态"
          value={filters.status}
          options={STATUS_LABELS}
          onChange={(status) => update({ status })}
        />
        <Select
          label="分流"
          value={filters.route}
          options={ROUTE_LABELS}
          onChange={(route) => update({ route })}
        />
        <Select
          label="类别"
          value={filters.category}
          options={CATEGORY_LABELS}
          onChange={(category) => update({ category })}
        />
      </div>
      <ErrorNotice message={error} />
      {list && list.items.length === 0 && (
        <p className="empty">
          没有符合条件的案件。可以在 <a href="#/sim">模拟邮箱</a> 中投递一封来信，或运行{" "}
          <code>copilot seed</code>。
        </p>
      )}
      {list && list.items.length > 0 && (
        <>
          <ul className="case-list">
            {list.items.map((item) => (
              <li key={item.id}>
                <a className="case-row" href={caseHref(item.id)}>
                  <span className="case-row-head">
                    <span className="case-id">{item.id}</span>
                    <StatusBadge status={item.status} />
                    <RouteBadge route={item.route} />
                    <span className="meta">{label(CATEGORY_LABELS, item.category)}</span>
                    {item.urgency && (
                      <span className={`meta urgency-${item.urgency}`}>
                        紧急程度：{label(URGENCY_LABELS, item.urgency)}
                      </span>
                    )}
                    <span className="meta push">
                      {item.message_count} 封 · {formatTime(item.last_message_at)}
                    </span>
                  </span>
                  <span className="case-row-body">
                    {item.summary_zh ? (
                      <SafeText text={item.summary_zh} />
                    ) : (
                      <span lang="ja" className="muted">
                        <SafeText text={item.preview_ja} />
                      </span>
                    )}
                  </span>
                </a>
              </li>
            ))}
          </ul>
          <div className="pager">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              上一页
            </button>
            <span>
              {offset + 1}–{offset + list.items.length} / 共 {list.total} 个
            </span>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= list.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
