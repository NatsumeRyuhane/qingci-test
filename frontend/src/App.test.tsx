import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";

function stubFetch(response: Response | Error) {
  const fetchMock = vi.fn(() =>
    response instanceof Error ? Promise.reject(response) : Promise.resolve(response),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("App", () => {
  it("显示后端健康检查结果", async () => {
    const fetchMock = stubFetch(
      new Response(JSON.stringify({ status: "ok", version: "0.1.0" }), {
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(<App />);

    expect(await screen.findByText("后端状态：ok（版本 0.1.0）")).toBeInTheDocument();
    const request = fetchMock.mock.calls[0] as unknown as [Request];
    expect(new URL(request[0].url).pathname).toBe("/v1/health");
  });

  it("后端不可用时提示错误", async () => {
    stubFetch(new TypeError("network error"));

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接后端");
  });

  it("后端返回错误状态时提示错误", async () => {
    stubFetch(new Response("Internal Server Error", { status: 500 }));

    render(<App />);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
