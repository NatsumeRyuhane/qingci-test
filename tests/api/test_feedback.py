"""反馈端点与发件箱的测试。只用合成数据（D-002），起草使用占位实现。"""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot.contracts import Case, DraftCheck, DraftCheckKind
from copilot.contracts.enums import CaseStatus
from copilot.draft.service import DraftOutcome
from copilot.workbench import StubDrafter
from tests.cases import agent, case, player

Seed = Callable[..., None]


def _draft(client: TestClient, case_id: str = "SYN-1") -> dict[str, Any]:
    response = client.post(f"/v1/cases/{case_id}/drafts", json={})
    assert response.status_code == 201, response.text
    draft: dict[str, Any] = response.json()["draft"]
    return draft


def _feedback(client: TestClient, draft_id: str, **body: Any) -> Any:
    return client.post(f"/v1/drafts/{draft_id}/feedback", json={"actor": "客服A", **body})


def test_approve_sends_to_outbox_and_closes_case(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1"))
    draft = _draft(client)

    response = _feedback(client, draft["id"], action="approved")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["case_status"] == "replied"
    assert body["feedback"]["edit_distance"] == 0
    assert body["mail"]["body_ja"] == draft["body_ja"]
    assert body["mail"]["sent_by"] == "客服A"

    detail = client.get("/v1/cases/SYN-1").json()
    reply = detail["case"]["messages"][-1]
    assert reply["seq"] == 2 == body["mail"]["message_seq"]
    assert reply["role"] == "agent"
    assert reply["body"] == draft["body_ja"]
    assert [e["kind"] for e in reply["role_evidence"]] == ["metadata"]
    assert detail["feedback"] == [body["feedback"]]

    outbox = client.get("/v1/sim/outbox").json()
    assert outbox["total"] == 1 and outbox["items"] == [body["mail"]]


def test_edited_reply_records_distance_and_sends_edited_text(
    client: TestClient, seed: Seed
) -> None:
    seed(case("SYN-1"))
    draft = _draft(client)
    edited = draft["body_ja"].replace("【占位の下書き】", "【合成データ】確認いたしました。")

    body = _feedback(
        client, draft["id"], action="edited", final_body_ja=edited, reason="补充说明"
    ).json()
    assert body["feedback"]["edit_distance"] > 0
    assert body["feedback"]["final_body_ja"] == edited
    assert body["mail"]["body_ja"] == edited


def test_escalate_and_reject_require_reason(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1"), case("SYN-2"))
    first, second = _draft(client, "SYN-1"), _draft(client, "SYN-2")

    for action, draft in [("escalated", first), ("rejected", second)]:
        response = _feedback(client, draft["id"], action=action, reason="  ")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    escalated = _feedback(client, first["id"], action="escalated", reason="涉及退款").json()
    assert escalated["case_status"] == "escalated" and escalated["mail"] is None
    rejected = _feedback(client, second["id"], action="rejected", reason="模板不对").json()
    assert rejected["case_status"] == "open"
    assert client.get("/v1/sim/outbox").json()["total"] == 0


def test_escalated_case_can_still_be_approved_later(client: TestClient, seed: Seed) -> None:
    """升级后由高级客服处理同一封草稿。"""
    seed(case("SYN-1"))
    draft = _draft(client)
    _feedback(client, draft["id"], action="escalated", reason="需要主管确认")

    response = client.post(
        f"/v1/drafts/{draft['id']}/feedback", json={"actor": "主管", "action": "approved"}
    )
    assert response.json()["case_status"] == "replied"
    assert len(client.get("/v1/cases/SYN-1").json()["feedback"]) == 2


def test_invalid_combinations_are_rejected(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1"))
    draft = _draft(client)

    cases = [
        {"action": "edited"},
        {"action": "edited", "final_body_ja": draft["body_ja"]},
        {"action": "approved", "final_body_ja": draft["body_ja"] + "!"},
    ]
    for body in cases:
        assert _feedback(client, draft["id"], **body).status_code == 422, body
    assert _feedback(client, draft["id"], action="approved", actor="").status_code == 422
    assert client.get("/v1/cases/SYN-1").json()["feedback"] == []


def test_only_the_latest_draft_of_an_open_case_can_be_handled(
    client: TestClient, seed: Seed
) -> None:
    seed(case("SYN-1"))
    old, new = _draft(client), _draft(client)

    response = _feedback(client, old["id"], action="approved")
    assert response.status_code == 409
    assert _feedback(client, new["id"], action="approved").status_code == 201
    # 已回复：不能再处理，也不能再起草
    assert _feedback(client, new["id"], action="approved").status_code == 409
    assert client.post("/v1/cases/SYN-1/drafts", json={}).status_code == 409


def test_follow_up_after_drafting_makes_draft_stale(client: TestClient, seed: Seed) -> None:
    seed(
        case(
            "SYN-1", player("SYN-1", 1), agent("SYN-1", 2, "【合成データ】回答"), player("SYN-1", 3)
        )
    )
    draft = _draft(client)
    seed(
        case(
            "SYN-1",
            player("SYN-1", 1),
            agent("SYN-1", 2, "【合成データ】回答"),
            player("SYN-1", 3),
            player("SYN-1", 4, "【合成データ】追記です"),
        ).model_copy(update={"status": CaseStatus.OPEN})
    )

    response = _feedback(client, draft["id"], action="approved")
    assert response.status_code == 409
    assert "又有来信" in response.json()["error"]["message"]


def test_feedback_for_missing_draft(client: TestClient) -> None:
    assert _feedback(client, "nope", action="approved").status_code == 404


def test_outbox_filters_by_case(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1"), case("SYN-2"))
    for case_id in ["SYN-1", "SYN-2"]:
        _feedback(client, _draft(client, case_id)["id"], action="approved")

    assert client.get("/v1/sim/outbox").json()["total"] == 2
    only = client.get("/v1/sim/outbox", params={"case_id": "SYN-2"}).json()
    assert [m["case_id"] for m in only["items"]] == ["SYN-2"]
    assert client.get("/v1/sim/outbox", params={"case_id": "../x"}).status_code == 422


# --- 中文编辑、翻译、自检确认与无需回复（#8） ---


class _FailingChecks(StubDrafter):
    """编辑后的正文语体不通过；草稿本身也有一项未通过。"""

    def draft(self, case: Case) -> DraftOutcome:
        outcome = super().draft(case)
        assert outcome.draft is not None
        failed = DraftCheck(kind=DraftCheckKind.CITATIONS, passed=False, detail_zh="【合成】")
        window = DraftCheck(kind=DraftCheckKind.TEMPLATE_WINDOW, passed=True)
        draft = outcome.draft.model_copy(update={"checks": [failed, window]})
        return outcome.model_copy(update={"draft": draft})

    def check(self, body_ja: str) -> list[DraftCheck]:
        return [DraftCheck(kind=DraftCheckKind.REGISTER, passed="直接修正" not in body_ja)]


def test_translate_and_check_endpoints(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1"))
    draft = _draft(client)

    translated = client.post(
        f"/v1/drafts/{draft['id']}/translate", json={"body_zh": "【合成】我们正在调查。"}
    ).json()
    assert "【合成】我们正在调查。" in translated["body_ja"]
    assert [c["kind"] for c in translated["checks"]] == ["structure"]
    assert translated["checks"][0]["passed"] is True

    checked = client.post(f"/v1/drafts/{draft['id']}/check", json={"body_ja": "直接の編集"}).json()
    assert checked["body_ja"] == "直接の編集"
    assert checked["checks"][0]["passed"] is False
    assert client.post("/v1/drafts/nope/check", json={"body_ja": "x"}).status_code == 404
    assert (
        client.post(f"/v1/drafts/{draft['id']}/translate", json={"body_zh": ""}).status_code == 422
    )


def test_failed_checks_must_be_confirmed_before_sending(
    app: FastAPI, client: TestClient, seed: Seed
) -> None:
    app.state.drafter = _FailingChecks()
    seed(case("SYN-1"), case("SYN-2"))

    # 原样批准：草稿自己的自检有一项未通过
    first = _draft(client, "SYN-1")
    refused = _feedback(client, first["id"], action="approved")
    assert refused.status_code == 422 and "citations" in refused.json()["error"]["message"]
    sent = _feedback(client, first["id"], action="approved", confirmed_failed_checks=["citations"])
    assert sent.json()["feedback"]["confirmed_failed_checks"] == ["citations"]

    # 修改后发送：服务端重新自检编辑后的正文（语体不通过），模板有效期沿用草稿的结果
    second = _draft(client, "SYN-2")
    edited = {
        "action": "edited",
        "final_body_ja": second["body_ja"] + "直接修正",
        "final_body_zh": "【合成】客服写的中文",
        "ja_edited_directly": True,
    }
    assert _feedback(client, second["id"], **edited).status_code == 422
    ok = _feedback(
        client, second["id"], **edited, confirmed_failed_checks=["register", "citations"]
    )
    feedback = ok.json()["feedback"]
    # 只记录实际未通过的项；citations 不再检查（客服自己写的说法没有引用）
    assert feedback["confirmed_failed_checks"] == ["register"]
    assert feedback["final_body_zh"] == "【合成】客服写的中文"
    assert feedback["ja_edited_directly"] is True


def test_no_reply_closes_case_with_reason(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1"))
    draft = _draft(client)

    assert _feedback(client, draft["id"], action="no_reply").status_code == 422
    result = _feedback(client, draft["id"], action="no_reply", reason="【合成】重复提交").json()
    assert result["case_status"] == "no_reply_needed" and result["mail"] is None
    assert client.get("/v1/sim/outbox").json()["total"] == 0
    assert client.post("/v1/cases/SYN-1/drafts").status_code == 409


def test_actionable_queue_oldest_first(client: TestClient, seed: Seed) -> None:
    seed(
        case("SYN-1"),
        case("SYN-2"),
        case("SYN-3").model_copy(update={"status": CaseStatus.REPLIED}),
    )
    queue = client.get("/v1/cases", params={"actionable": True, "sort": "oldest"}).json()
    assert [item["id"] for item in queue["items"]] == ["SYN-1", "SYN-2"]


def test_translate_with_base_only_replaces_changed_paragraphs(
    client: TestClient, seed: Seed
) -> None:
    seed(case("SYN-1"))
    draft = _draft(client)
    base_zh = "问候\n\n第一段\n\n第二段\n\n结语"
    base_ja = "挨拶\n\n一段目\n\n二段目\n\n結び"
    body = {
        "body_zh": base_zh.replace("第二段", "改过的第二段"),
        "base_zh": base_zh,
        "base_ja": base_ja,
    }
    result = client.post(f"/v1/drafts/{draft['id']}/translate", json=body).json()
    assert result["body_ja"] == "挨拶\n\n一段目\n\n【占位翻訳】改过的第二段\n\n結び"


def test_escalated_case_stays_escalated(client: TestClient, seed: Seed) -> None:
    """已升级的案件不能再次升级；驳回草稿也不会让它失去升级。"""
    seed(case("SYN-1"))
    draft = _draft(client)
    assert _feedback(client, draft["id"], action="escalated", reason="退款").status_code == 201

    again = _feedback(client, draft["id"], action="escalated", reason="再升级一次")
    assert again.status_code == 409 and "已经升级" in again.json()["error"]["message"]
    rejected = _feedback(client, draft["id"], action="rejected", reason="草稿不可用").json()
    assert rejected["case_status"] == "escalated"
    redraft = client.post("/v1/cases/SYN-1/drafts").json()
    assert redraft["case"]["status"] == "escalated"
