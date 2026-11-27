import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/api";
import { Inbox } from "./Inbox";

const ITEM = {
  id: "SIM-1",
  status: "drafted",
  category: "purchase",
  route: "escalate",
  summary_zh: "【合成】购买后未到账",
  urgency: "high",
  sentiment: "negative",
  preview_ja: "【合成データ】",
  message_count: 1,
  last_message_at: null,
};

describe("Inbox", () => {
  it("列出案件，筛选时带上查询参数并回到第一页", async () => {
    const calls = mockApi({ "GET /v1/cases": () => ({ items: [ITEM], total: 1 }) });
    render(<Inbox />);

    expect(await screen.findByText("【合成】购买后未到账")).toBeInTheDocument();
    expect(screen.getByText("升级处理", { selector: ".badge" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /SIM-1/ })).toHaveAttribute("href", "#case-SIM-1");

    fireEvent.change(screen.getByLabelText("分流"), { target: { value: "escalate" } });
    fireEvent.change(screen.getByLabelText("类别"), { target: { value: "purchase" } });

    await waitFor(() => {
      const last = calls.at(-1);
      expect(last?.query.get("route")).toBe("escalate");
      expect(last?.query.get("category")).toBe("purchase");
      expect(last?.query.get("offset")).toBe("0");
      expect(last?.query.has("status")).toBe(false);
    });
  });

  it("显示后端的错误信息和请求 ID", async () => {
    mockApi({
      "GET /v1/cases": () =>
        new Response(
          JSON.stringify({
            error: { code: "internal_error", message: "服务器内部错误", request_id: "rid-1" },
          }),
          { status: 500, headers: { "Content-Type": "application/json" } },
        ),
    });
    render(<Inbox />);

    expect(await screen.findByRole("alert")).toHaveTextContent("服务器内部错误（请求 ID：rid-1）");
  });
});
