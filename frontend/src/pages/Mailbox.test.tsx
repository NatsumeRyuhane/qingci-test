import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/api";
import { EXAMPLES, Mailbox } from "./Mailbox";

describe("Mailbox", () => {
  it("投递新线程时附带表单，追问时不带", async () => {
    const calls = mockApi({
      "GET /v1/cases": () => ({
        items: [{ id: "SIM-9", status: "replied", message_count: 2, preview_ja: "" }],
        total: 1,
      }),
      "GET /v1/sim/outbox": () => ({ items: [], total: 0 }),
      "POST /v1/sim/inbox": () => ({ case_id: "SIM-NEW", message_seq: 1, case_status: "open" }),
    });
    render(<Mailbox />);

    fireEvent.click(await screen.findByRole("button", { name: EXAMPLES[2].name }));
    fireEvent.change(screen.getByLabelText("设备"), { target: { value: "SYN-PHONE" } });
    fireEvent.click(screen.getByRole("button", { name: "投递" }));

    expect(await screen.findByRole("link", { name: "SIM-NEW" })).toHaveAttribute(
      "href",
      "#case-SIM-NEW",
    );
    const first = calls.find((c) => c.method === "POST");
    expect(first?.body).toEqual({
      body: EXAMPLES[2].body,
      case_id: null,
      form: { category: "purchase", device: "SYN-PHONE" },
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
});
