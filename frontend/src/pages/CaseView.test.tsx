import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "../test/api";
import { DRAFT, detail } from "../test/fixtures";
import { CaseView } from "./CaseView";
import { BACKTRANSLATE_DELAY_MS } from "./DraftPanel";

function routes(overrides: Record<string, () => unknown> = {}) {
  return {
    "GET /v1/cases/:id": () => detail(),
    "GET /v1/templates/:id": () => new Response("{}", { status: 404 }),
    "POST /v1/drafts/:id/backtranslate": () => ({ back_translation_zh: "【合成】新的回译" }),
    "POST /v1/drafts/:id/feedback": () => ({ feedback: {}, mail: null, case_status: "escalated" }),
    ...overrides,
  };
}

describe("CaseView", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("显示线程，并把不可见字符标出来", async () => {
    mockApi(routes());
    render(<CaseView caseId="SIM-1" actor="客服A" />);

    expect(await screen.findByText("⟨U+200B⟩")).toBeInTheDocument();
    expect(screen.getByText("【合成】这是回复。")).toBeInTheDocument();
  });

  it("编辑草稿后防抖回译，修改后发送带上修改后的正文", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = mockApi(routes());
    render(<CaseView caseId="SIM-1" actor="客服A" />);
    const editor = await screen.findByLabelText("日语回复（可编辑）");

    fireEvent.change(editor, { target: { value: `${DRAFT.body_ja}追記` } });
    fireEvent.change(editor, { target: { value: `${DRAFT.body_ja}追記します` } });
    expect(calls.some((c) => c.path.endsWith("/backtranslate"))).toBe(false);
    await act(async () => {
      vi.advanceTimersByTime(BACKTRANSLATE_DELAY_MS);
    });
    expect(await screen.findByText("【合成】新的回译")).toBeInTheDocument();
    const translated = calls.filter((c) => c.path.endsWith("/backtranslate"));
    expect(translated).toHaveLength(1);
    expect(translated[0].body).toEqual({ body_ja: `${DRAFT.body_ja}追記します` });

    expect(screen.queryByRole("button", { name: "批准发送" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修改后发送" }));
    await waitFor(() => {
      const sent = calls.find((c) => c.path === "/v1/drafts/draft-1/feedback");
      expect(sent?.body).toMatchObject({
        actor: "客服A",
        action: "edited",
        final_body_ja: `${DRAFT.body_ja}追記します`,
      });
    });
  });

  it("升级处理和驳回需要填写理由", async () => {
    const calls = mockApi(routes());
    render(<CaseView caseId="SIM-1" actor="客服A" />);
    const escalate = await screen.findByRole("button", { name: "升级处理" });

    expect(escalate).toBeDisabled();
    expect(screen.getByRole("button", { name: "驳回" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/理由/), { target: { value: "涉及退款" } });
    fireEvent.click(escalate);

    await waitFor(() => {
      const sent = calls.find((c) => c.path === "/v1/drafts/draft-1/feedback");
      expect(sent?.body).toMatchObject({
        action: "escalated",
        reason: "涉及退款",
        final_body_ja: null,
      });
    });
  });

  it("批准时原样发送，结案后不能再操作", async () => {
    let replied = false;
    const calls = mockApi(
      routes({
        "GET /v1/cases/:id": () =>
          replied ? detail({ case: { ...detail().case, status: "replied" } }) : detail(),
        "POST /v1/drafts/:id/feedback": () => {
          replied = true;
          return {
            feedback: {},
            mail: { sent_at: "2099-01-01T00:00:00+09:00" },
            case_status: "replied",
          };
        },
      }),
    );
    render(<CaseView caseId="SIM-1" actor="客服A" />);

    fireEvent.click(await screen.findByRole("button", { name: "批准发送" }));
    expect(await screen.findByText(/回复已进入发件箱/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ action: "approved" });
    expect(screen.queryByRole("button", { name: "批准发送" })).not.toBeInTheDocument();
  });

  it("没有当前客服时不能操作", async () => {
    mockApi(routes());
    render(<CaseView caseId="SIM-1" actor=" " />);
    expect(await screen.findByRole("button", { name: "批准发送" })).toBeDisabled();
  });
});
