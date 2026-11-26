import { useEffect, useState } from "react";
import { api, type Health } from "./api/client";

type State = { kind: "loading" } | { kind: "ok"; health: Health } | { kind: "error" };

export function App() {
  const [state, setState] = useState<State>({ kind: "loading" });

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

  return (
    <main>
      <h1>客服坐席助手</h1>
      {state.kind === "loading" && <p>正在连接后端…</p>}
      {state.kind === "ok" && (
        <p>
          后端状态：{state.health.status}（版本 {state.health.version}）
        </p>
      )}
      {state.kind === "error" && <p role="alert">无法连接后端，请确认后端服务已启动。</p>}
    </main>
  );
}
