import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { DraftCheck } from "../api/client";
import { type Call, mockApi } from "../test/api";
import { DRAFT, detail } from "../test/fixtures";
import { Workspace } from "./Workspace";

const PASS: DraftCheck[] = [{ kind: "structure", passed: true }];
const FAIL: DraftCheck[] = [
  { kind: "structure", passed: true },
  { kind: "forbidden_promise", passed: false, detail_zh: "【合成】出现退款承诺" },
];

function routes(overrides: Record<string, (call: Call) => unknown> = {}) {
  return {
    "GET /v1/cases/:id": () => detail(),
    "GET /v1/cases": () => ({
      items: [
        { id: "SIM-1", status: "drafted", message_count: 1, preview_ja: "" },
        { id: "SIM-2", status: "open", message_count: 1, preview_ja: "" },
      ],
      total: 2,
    }),
    "GET /v1/templates/:id": () => new Response("{}", { status: 404 }),
    "POST /v1/drafts/:id/translate": () => ({ body_ja: "【合成データ】訳文です。", checks: PASS }),
    "POST /v1/drafts/:id/check": () => ({ body_ja: "x", checks: FAIL }),
    "POST /v1/drafts/:id/feedback": () => ({ feedback: {}, mail: null, case_status: "replied" }),
    ...overrides,
  };
}

const posts = (calls: Call[], suffix: string) =>
  calls.filter((c) => c.method === "POST" && c.path.endsWith(suffix));

describe("Workspace", () => {
  afterEach(() => {
    window.location.hash = "";
  });

  it("三栏：案件信息、线程（标出不可见字符）、编辑器；底部有案件栏", async () => {
    mockApi(routes());
    render(<Workspace caseId="SIM-1" actor="客服A" />);

    expect(await screen.findByText("⟨U+200B⟩")).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "案件信息" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "回复" })).toBeInTheDocument();
    const bar = screen.getByRole("navigation", { name: "案件导航" });
    expect(within(bar).getByText("待处理 2 个")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /中文（编辑这里）/ })).toHaveValue(
      DRAFT.back_translation_zh,
    );
  });

  it("编辑中文后翻译为日语，日语和自检随之更新", async () => {
    const calls = mockApi(routes());
    render(<Workspace caseId="SIM-1" actor="客服A" />);
    const zh = await screen.findByRole("textbox", { name: /中文（编辑这里）/ });

    fireEvent.change(zh, { target: { value: "【合成】我们正在调查。" } });
    expect(screen.getByText("未翻译")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "翻译为日语 →" }));

    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: /日语（发送的正文）/ })).toHaveValue(
        "【合成データ】訳文です。",
      ),
    );
    expect(posts(calls, "/translate")[0].body).toEqual({ body_zh: "【合成】我们正在调查。" });
    expect(screen.getByText("全部通过")).toBeInTheDocument();
  });

  it("中文改了没有翻译时不能发送", async () => {
    const calls = mockApi(routes());
    render(<Workspace caseId="SIM-1" actor="客服A" />);
    fireEvent.change(await screen.findByRole("textbox", { name: /中文（编辑这里）/ }), {
      target: { value: "改了" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认发送" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("还没有翻译成日语");
    expect(posts(calls, "/feedback")).toHaveLength(0);
  });

  it("直接改日语先提醒；保存时只做代码自检，不调用翻译；有未通过项要确认后才能发送", async () => {
    const calls = mockApi(routes());
    render(<Workspace caseId="SIM-1" actor="客服A" />);
    const ja = await screen.findByRole("textbox", { name: /日语（发送的正文）/ });

    expect(ja).toHaveAttribute("readonly");
    fireEvent.click(ja);
    const dialog = screen.getByRole("alertdialog", { name: "直接修改日语？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "直接修改日语" }));
    expect(ja).not.toHaveAttribute("readonly");

    fireEvent.change(ja, { target: { value: `${DRAFT.body_ja}全額返金いたします。` } });
    fireEvent.click(screen.getByRole("button", { name: "保存日语修改并自检" }));
    expect(await screen.findByText("1 项未通过")).toBeInTheDocument();
    expect(posts(calls, "/check")).toHaveLength(1);
    expect(posts(calls, "/translate")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "确认发送" }));
    const send = await screen.findByRole("button", { name: "仍然发送" });
    expect(send).toBeDisabled();
    fireEvent.click(screen.getByLabelText("我已核对，仍然发送"));
    fireEvent.click(send);

    await waitFor(() => expect(posts(calls, "/feedback")).toHaveLength(1));
    expect(posts(calls, "/feedback")[0].body).toMatchObject({
      action: "edited",
      final_body_ja: `${DRAFT.body_ja}全額返金いたします。`,
      ja_edited_directly: true,
      confirmed_failed_checks: ["forbidden_promise"],
      final_body_zh: null,
    });
  });

  it("无需回复要填理由；处理完自动跳到下一个待处理的案件", async () => {
    const calls = mockApi(routes());
    render(<Workspace caseId="SIM-1" actor="客服A" />);

    fireEvent.click(await screen.findByRole("button", { name: "无需回复" }));
    const confirm = screen.getByRole("button", { name: "确认无需回复" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/理由/), { target: { value: "重复提交" } });
    fireEvent.click(confirm);

    await waitFor(() => expect(window.location.hash).toBe("#case-SIM-2"));
    expect(posts(calls, "/feedback")[0].body).toMatchObject({
      action: "no_reply",
      reason: "重复提交",
      confirmed_failed_checks: [],
    });
  });

  it("没有草稿时提示生成", async () => {
    const calls = mockApi(routes({ "GET /v1/cases/:id": () => detail({ draft: null }) }));
    render(<Workspace caseId="SIM-1" actor="客服A" />);
    fireEvent.click(await screen.findByRole("button", { name: "生成草稿" }));
    await waitFor(() => expect(posts(calls, "/drafts")).toHaveLength(1));
  });
});
