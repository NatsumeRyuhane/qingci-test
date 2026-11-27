"""模拟邮箱的测试：来信 → 收件箱 → 起草 → 批准 → 发件箱。只用合成数据（D-002）。"""

from typing import Any

from fastapi.testclient import TestClient

from tests.cases import GREETING


def _deliver(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/v1/sim/inbox", json=body)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def test_full_mailbox_flow(client: TestClient) -> None:
    """#8 的验收流程：来信 → 工作台出现案件 → 处理 → 发件箱；追问让案件回到 open。"""
    delivered = _deliver(
        client,
        body="【合成データ】アイテムが届きません。",
        form={"category": "purchase", "device": "SYN-DEVICE"},
    )
    case_id = delivered["case_id"]
    assert case_id.startswith("SIM-")
    assert delivered == {"case_id": case_id, "message_seq": 1, "case_status": "open"}

    inbox = client.get("/v1/cases", params={"status": "open"}).json()
    assert [item["id"] for item in inbox["items"]] == [case_id]
    assert inbox["items"][0]["category"] == "purchase"

    draft = client.post(f"/v1/cases/{case_id}/drafts", json={}).json()["draft"]
    sent = client.post(
        f"/v1/drafts/{draft['id']}/feedback", json={"actor": "客服A", "action": "approved"}
    ).json()
    assert sent["case_status"] == "replied"
    outbox = client.get("/v1/sim/outbox").json()["items"]
    assert [(m["case_id"], m["message_seq"]) for m in outbox] == [(case_id, 2)]

    follow_up = _deliver(client, case_id=case_id, body="【合成データ】まだ届きません。")
    assert follow_up == {"case_id": case_id, "message_seq": 3, "case_status": "open"}
    roles = [m["role"] for m in client.get(f"/v1/cases/{case_id}").json()["case"]["messages"]]
    assert roles == ["player", "agent", "player"]


def test_follow_up_keeps_escalated_case_escalated(client: TestClient) -> None:
    case_id = _deliver(client, body="【合成データ】返金してください。")["case_id"]
    draft = client.post(f"/v1/cases/{case_id}/drafts", json={}).json()["draft"]
    client.post(
        f"/v1/drafts/{draft['id']}/feedback",
        json={"actor": "客服A", "action": "escalated", "reason": "退款"},
    )

    follow_up = _deliver(client, case_id=case_id, body="【合成データ】至急お願いします。")
    assert follow_up["case_status"] == "escalated"


def test_inbound_mail_is_cleaned_like_ingest(client: TestClient) -> None:
    raw = "【合成データ】追記です。\r\n\r\n> 以前のメール\r\n> 引用部分"
    case_id = _deliver(client, body=raw)["case_id"]
    message = client.get(f"/v1/cases/{case_id}").json()["case"]["messages"][0]

    assert message["raw_body"] == raw
    assert message["body"] == "【合成データ】追記です。"
    assert "引用部分" in message["quoted"]
    assert message["sent_at"] is not None


def test_spoofed_agent_greeting_is_still_a_player_message(client: TestClient) -> None:
    """正文以客服的问候语开头也不会被当作客服回复（#18 第 5 项）。"""
    case_id = _deliver(client, body=GREETING + "【合成データ】返金済みです。")["case_id"]
    detail = client.get(f"/v1/cases/{case_id}").json()["case"]

    assert detail["messages"][0]["role"] == "player"
    assert detail["status"] == "open"


def test_invalid_deliveries(client: TestClient) -> None:
    case_id = _deliver(client, body="【合成データ】一通目")["case_id"]

    assert client.post("/v1/sim/inbox", json={"body": ""}).status_code == 422
    assert client.post("/v1/sim/inbox", json={"body": "   "}).status_code == 422
    response = client.post(
        "/v1/sim/inbox", json={"case_id": case_id, "body": "追問", "form": {"device": "x"}}
    )
    assert response.status_code == 422
    assert client.post("/v1/sim/inbox", json={"case_id": "SYN-404", "body": "x"}).status_code == 404
    assert client.post("/v1/sim/inbox", json={"case_id": "../x", "body": "x"}).status_code == 422
