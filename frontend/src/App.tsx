import { type ReactNode, useEffect, useState } from "react";
import { api, type Health } from "./api/client";
import { DEFAULT_AGENTS, useActor } from "./lib/agent";
import { useRoute } from "./lib/route";
import { Inbox } from "./pages/Inbox";
import { Mailbox } from "./pages/Mailbox";
import { Metrics } from "./pages/Metrics";
import { Workspace } from "./pages/Workspace";

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

function HealthStatus() {
  const health = useHealth();
  return (
    <span className="health">
      {health.kind === "loading" && "正在连接后端…"}
      {health.kind === "ok" && `后端状态：${health.health.status}（版本 ${health.health.version}）`}
      {health.kind === "error" && <span role="alert">无法连接后端，请确认后端服务已启动。</span>}
    </span>
  );
}

/** 模拟邮箱和指标是独立的页面（在新标签页中打开），演示时可以和工作台并排摆放。 */
function StandalonePage({ title, children }: { title: string; children: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · 客服坐席助手`;
  }, [title]);
  return (
    <div className="app">
      <header className="app-header">
        <h1>{title}</h1>
        <a href="#inbox" target="_blank" rel="noopener">
          打开工作台 ↗
        </a>
        <HealthStatus />
      </header>
      <main>{children}</main>
    </div>
  );
}

export function App() {
  const route = useRoute();
  const [actor, setActor] = useActor();

  if (route.page === "sim") {
    return (
      <StandalonePage title="模拟邮箱（演示用）">
        <Mailbox />
      </StandalonePage>
    );
  }
  if (route.page === "metrics") {
    return (
      <StandalonePage title="指标">
        <Metrics />
      </StandalonePage>
    );
  }
  return (
    <div className={route.page === "case" ? "app app-workspace" : "app"}>
      <header className="app-header">
        <h1>
          <a href="#inbox">客服坐席助手</a>
        </h1>
        <nav>
          <a href="#inbox" aria-current={route.page === "inbox" ? "page" : undefined}>
            收件箱
          </a>
          <a href="#sim" target="_blank" rel="noopener">
            模拟邮箱 ↗
          </a>
          <a href="#metrics" target="_blank" rel="noopener">
            指标 ↗
          </a>
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
        <HealthStatus />
      </header>
      {route.page === "case" ? (
        <Workspace key={route.caseId} caseId={route.caseId} actor={actor} />
      ) : (
        <main>
          <Inbox />
        </main>
      )}
    </div>
  );
}
