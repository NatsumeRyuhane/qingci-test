import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { HEALTH, mockApi } from "./test/api";

const EMPTY_INBOX = { "GET /v1/cases": () => ({ items: [], total: 0 }) };

describe("App", () => {
  afterEach(() => {
    window.location.hash = "";
  });

  it("显示后端健康检查结果和收件箱", async () => {
    const calls = mockApi({ ...HEALTH, ...EMPTY_INBOX });
    render(<App />);

    expect(await screen.findByText("后端状态：ok（版本 0.1.0）")).toBeInTheDocument();
    expect(await screen.findByText(/没有符合条件的案件/)).toBeInTheDocument();
    expect(calls.map((c) => c.path)).toContain("/v1/health");
  });

  it("后端不可用时提示错误", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("network error"))),
    );
    render(<App />);

    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((a) => a.textContent?.includes("无法连接后端"))).toBe(true);
  });

  it("按 hash 切换页面", async () => {
    mockApi({
      ...HEALTH,
      ...EMPTY_INBOX,
      "GET /v1/metrics": () => {
        const row = {
          key: "total",
          handled: 0,
          approved: 0,
          edited: 0,
          escalated: 0,
          rejected: 0,
          approved_rate: 0,
          edited_rate: 0,
          escalated_rate: 0,
          rejected_rate: 0,
          avg_edit_distance: null,
        };
        return { total: row, by_template: [], by_route: [] };
      },
    });
    window.location.hash = "#/metrics";
    render(<App />);

    expect(await screen.findByRole("heading", { name: "指标" })).toBeInTheDocument();
  });
});
