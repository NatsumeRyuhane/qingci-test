import { useCallback, useEffect, useState } from "react";
import { api, call, errorText, type MetricsResponse, type MetricsRow } from "../api/client";
import { ErrorNotice } from "../components/Badges";
import { label, percent, ROUTE_LABELS } from "../lib/labels";
import { usePolling } from "../lib/polling";

function Table({
  title,
  rows,
  keyLabel,
}: {
  title: string;
  rows: MetricsRow[];
  keyLabel: (key: string) => string;
}) {
  return (
    <section className="card">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="muted">还没有处理过的草稿。</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">项目</th>
                <th scope="col">处理数</th>
                <th scope="col">原样批准率</th>
                <th scope="col">修改后批准率</th>
                <th scope="col">升级率</th>
                <th scope="col">驳回率</th>
                <th scope="col">无需回复</th>
                <th scope="col">平均编辑距离</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row">{keyLabel(row.key)}</th>
                  <td>{row.handled}</td>
                  <td>{percent(row.approved_rate)}</td>
                  <td>{percent(row.edited_rate)}</td>
                  <td>{percent(row.escalated_rate)}</td>
                  <td>{percent(row.rejected_rate)}</td>
                  <td>{percent(row.no_reply_rate)}</td>
                  <td>{row.avg_edit_distance == null ? "—" : row.avg_edit_distance.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function Metrics() {
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    call(api.GET("/v1/metrics"))
      .then((data) => {
        setMetrics(data);
        setError(null);
      })
      .catch((e: unknown) => setError(errorText(e)));
  }, []);
  useEffect(load, [load]);
  usePolling(load);
  return (
    <section>
      <div className="toolbar">
        <span className="meta">每封草稿以最后一次处理为准；编辑距离只算发出的回复（字符）。</span>
      </div>
      <ErrorNotice message={error} />
      {metrics && (
        <>
          <Table
            title="总体"
            rows={metrics.total.handled ? [metrics.total] : []}
            keyLabel={() => "全部"}
          />
          <Table
            title="按分流结果"
            rows={metrics.by_route}
            keyLabel={(k) => label(ROUTE_LABELS, k)}
          />
          <Table title="按模板" rows={metrics.by_template} keyLabel={(k) => k} />
        </>
      )}
    </section>
  );
}
