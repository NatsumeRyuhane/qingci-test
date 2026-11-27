import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EXAMPLES } from "../lib/examples";
import { mockApi } from "../test/api";
import { Mailbox } from "./Mailbox";

describe("Mailbox", () => {
  it("选择示例后投递新线程（附表单），追问时不带表单", async () => {
    const calls = mockApi({
      "GET /v1/cases": () => ({
        items: [{ id: "SIM-9", status: "replied", message_count: 2, preview_ja: "" }],
        total: 1,
      }),
      "GET /v1/sim/outbox": () => ({ items: [], total: 0 }),
      "POST /v1/sim/inbox": () => ({ case_id: "SIM-NEW", message_seq: 1, case_status: "open" }),
    });
    render(<Mailbox />);
    const example = EXAMPLES.find((e) => e.name === "购买未到账");
    if (!example) throw new Error("缺少示例");

    fireEvent.change(await screen.findByLabelText("示例来信（合成数据）"), {
      target: { value: example.name },
    });
    expect(screen.getByText(/演示要点/)).toHaveTextContent(example.note);
    fireEvent.click(screen.getByRole("button", { name: "投递" }));

    expect(await screen.findByRole("link", { name: "SIM-NEW" })).toHaveAttribute(
      "href",
      "#case-SIM-NEW",
    );
    const first = calls.find((c) => c.method === "POST");
    expect(first?.body).toMatchObject({
      body: example.body,
      case_id: null,
      form: { category: "purchase", device: "SYN-PHONE 12" },
    });

    fireEvent.change(screen.getByLabelText("投递到"), { target: { value: "SIM-9" } });
    expect(screen.queryByLabelText("设备")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("正文（日语）"), { target: { value: "追伸" } });
    fireEvent.click(screen.getByRole("button", { name: "投递" }));
    await waitFor(() => {
      const posts = calls.filter((c) => c.method === "POST");
      expect(posts.at(-1)?.body).toEqual({ body: "追伸", case_id: "SIM-9", form: null });
    });
  });

  it("示例分为三组，名字不重复，恶意和格式异常的示例都有", () => {
    const names = EXAMPLES.map((e) => e.name);
    expect(new Set(names).size).toBe(names.length);
    expect(EXAMPLES.filter((e) => e.group === "恶意").length).toBeGreaterThanOrEqual(5);
    expect(EXAMPLES.filter((e) => e.group === "格式异常").length).toBeGreaterThanOrEqual(5);
    expect(EXAMPLES.some((e) => /\p{Cf}/u.test(e.body))).toBe(true);
  });
});
