import { useEffect, useState } from "react";
import { api, type Health } from "./api/client";
import { DEFAULT_AGENTS, useActor } from "./lib/agent";
import { type Route, useRoute } from "./lib/route";
import { CaseView } from "./pages/CaseView";
import { Inbox } from "./pages/Inbox";
import { Mailbox } from "./pages/Mailbox";
import { Metrics } from "./pages/Metrics";

type HealthState = { kind: "loading" } | { kind: "ok"; health: Health } | { kind: "error" };

function useHealth(): HealthState {
  const [state, setState] = useState<HealthState>({ kind: "loading" });
  useEffect(() => {
    let cancelled = false;
    api
      .GET("/v1/health")
      .then(({ data }) => {
        if (!cancelled) setState(data ? { kind: "ok", health: data } : { kind: "error" });
      })
      .catch(() => {
        if (!cancelled) setState({ kind: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}

const NAV: { page: Route["page"]; href: string; text: string }[] = [
  { page: "inbox", href: "#/inbox", text: "收件箱" },
  { page: "metrics", href: "#/metrics", text: "指标" },
  { page: "sim", href: "#/sim", text: "模拟邮箱" },
];

export function App() {
  const route = useRoute();
  const health = useHealth();
  const [actor, setActor] = useActor();
  const current = route.page === "case" ? "inbox" : route.page;

  return (
    <div className="app">
      <header className="app-header">
        <h1>客服坐席助手</h1>
        <nav>
          {NAV.map((item) => (
            <a
              key={item.page}
              href={item.href}
              aria-current={current === item.page ? "page" : undefined}
            >
              {item.text}
            </a>
          ))}
        </nav>
        <label className="field-inline actor">
          当前客服
          <input
            list="agents"
            value={actor}
            maxLength={64}
            onChange={(e) => setActor(e.target.value)}
          />
          <datalist id="agents">
            {DEFAULT_AGENTS.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </label>
        <span className="health">
          {health.kind === "loading" && "正在连接后端…"}
          {health.kind === "ok" &&
            `后端状态：${health.health.status}（版本 ${health.health.version}）`}
          {health.kind === "error" && (
            <span role="alert">无法连接后端，请确认后端服务已启动。</span>
          )}
        </span>
      </header>
      {import.meta.env.MODE === "demo" && (
        <p className="notice notice-warn demo-banner">
          演示模式：后端在浏览器中模拟，数据全部是合成的，刷新页面后恢复初始状态。不调用 LLM，
          草稿是预先写好的示例。
        </p>
      )}
      <main>
        {route.page === "inbox" && <Inbox />}
        {route.page === "case" && (
          <CaseView key={route.caseId} caseId={route.caseId} actor={actor} />
        )}
        {route.page === "metrics" && <Metrics />}
        {route.page === "sim" && <Mailbox />}
      </main>
    </div>
  );
}
