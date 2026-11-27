import { describe, expect, it } from "vitest";
import { api, call } from "../api/client";
import { installMockBackend, levenshtein } from "./server";

describe("演示模式的模拟后端", () => {
  it("来信 → 起草 → 批准 → 发件箱 → 追问，规则与后端一致", async () => {
    installMockBackend();
    const inbox = await call(api.GET("/v1/cases"));
    expect(inbox.total).toBe(4);
    expect(inbox.items.find((c) => c.id === "SIM-DEMO03")?.route).toBe("escalate");

    const sent = await call(
      api.POST("/v1/sim/inbox", { body: { body: "テスト", form: { category: "feedback" } } }),
    );
    const detail = await call(
      api.POST("/v1/cases/{case_id}/drafts", { params: { path: { case_id: sent.case_id } } }),
    );
    expect(detail.draft?.route).toBe("auto_send");
    const draftId = detail.draft?.id ?? "";

    const escalate = await api.POST("/v1/drafts/{draft_id}/feedback", {
      params: { path: { draft_id: draftId } },
      body: { actor: "客服A", action: "escalated", reason: " " },
    });
    expect(escalate.response.status).toBe(422);

    const approved = await call(
      api.POST("/v1/drafts/{draft_id}/feedback", {
        params: { path: { draft_id: draftId } },
        body: { actor: "客服A", action: "approved" },
      }),
    );
    expect(approved.case_status).toBe("replied");
    const outbox = await call(api.GET("/v1/sim/outbox"));
    expect(outbox.items[0]?.case_id).toBe(sent.case_id);

    const again = await api.POST("/v1/drafts/{draft_id}/feedback", {
      params: { path: { draft_id: draftId } },
      body: { actor: "客服A", action: "approved" },
    });
    expect(again.response.status).toBe(409);

    const followUp = await call(
      api.POST("/v1/sim/inbox", { body: { body: "追伸", case_id: sent.case_id } }),
    );
    expect(followUp).toMatchObject({ message_seq: 3, case_status: "open" });

    const metrics = await call(api.GET("/v1/metrics"));
    expect(metrics.total).toMatchObject({ handled: 1, approved: 1 });
  });

  it("疑似提示注入的来信不会被分流为可直接发送", async () => {
    installMockBackend();
    const detail = await call(
      api.POST("/v1/cases/{case_id}/drafts", { params: { path: { case_id: "SIM-DEMO04" } } }),
    );
    expect(detail.draft?.route).toBe("review");
    expect(detail.draft?.route_reasons[0]).toContain("提示注入");
  });

  it("编辑距离与后端相同", () => {
    expect(levenshtein("kitten", "sitting")).toBe(3);
    expect(levenshtein("前半。ここを変更。後半。", "前半。ここを修正した。後半。")).toBe(4);
  });
});
