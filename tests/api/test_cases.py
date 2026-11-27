"""案件与草稿端点的测试。只用合成数据（D-002），起草使用占位实现。"""

from collections.abc import Callable

from fastapi.testclient import TestClient

from copilot.contracts import Case, InquiryForm
from copilot.contracts.enums import CaseStatus, InquiryCategory
from tests.cases import agent, case, player

Seed = Callable[..., None]


def _purchase(thread_id: str) -> Case:
    """表单中选了“购买问题”的案件。与导入步骤一样，`Case.category` 取自表单。"""
    return case(thread_id).model_copy(
        update={
            "form": InquiryForm(category=InquiryCategory.PURCHASE, content="【合成】"),
            "category": InquiryCategory.PURCHASE,
        }
    )


def test_list_cases_before_and_after_drafting(client: TestClient, seed: Seed) -> None:
    seed(
        case("SYN-1", player("SYN-1", 1, "【合成データ】  ログイン\nできません")),
        _purchase("SYN-2"),
    )

    listed = client.get("/v1/cases").json()
    assert listed["total"] == 2
    first = next(item for item in listed["items"] if item["id"] == "SYN-1")
    assert first["route"] is None
    assert first["summary_zh"] is None
    assert first["preview_ja"] == "【合成データ】 ログイン できません"

    assert client.post("/v1/cases/SYN-2/drafts", json={}).status_code == 201

    listed = client.get("/v1/cases", params={"route": "escalate"}).json()
    assert [item["id"] for item in listed["items"]] == ["SYN-2"]
    item = listed["items"][0]
    assert item["status"] == "drafted"
    assert item["category"] == "purchase"
    assert item["summary_zh"].startswith("【占位】")
    assert item["urgency"] == "high"


def test_list_cases_filters_and_paginates(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1"), case("SYN-2"), _purchase("SYN-3"))

    assert client.get("/v1/cases", params={"category": "purchase"}).json()["total"] == 1
    page = client.get("/v1/cases", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert client.get("/v1/cases", params={"limit": 0}).status_code == 422
    assert client.get("/v1/cases", params={"status": "bogus"}).status_code == 422


def test_case_detail(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1"))

    detail = client.get("/v1/cases/SYN-1").json()
    assert detail["case"]["id"] == "SYN-1"
    assert detail["analysis"] is None and detail["draft"] is None and detail["feedback"] == []

    created = client.post("/v1/cases/SYN-1/drafts", json={}).json()
    assert created["draft"]["route"] == "review"
    assert created["case"]["status"] == "drafted"
    assert client.get("/v1/cases/SYN-1").json() == created


def test_redraft_replaces_latest_draft(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1"))
    first = client.post("/v1/cases/SYN-1/drafts", json={}).json()["draft"]
    second = client.post("/v1/cases/SYN-1/drafts", json={"template_id": "tpl-x"}).json()["draft"]

    assert first["id"] != second["id"]
    assert any("tpl-x" in reason for reason in second["route_reasons"])
    assert client.get("/v1/cases/SYN-1").json()["draft"]["id"] == second["id"]


def test_drafting_keeps_escalated_status(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1").model_copy(update={"status": CaseStatus.ESCALATED}))
    detail = client.post("/v1/cases/SYN-1/drafts", json={}).json()
    assert detail["case"]["status"] == "escalated"


def test_drafting_closed_or_invalid_cases_is_rejected(client: TestClient, seed: Seed) -> None:
    seed(
        case("SYN-1").model_copy(update={"status": CaseStatus.REPLIED}),
        case("SYN-2", agent("SYN-2", 1, "【合成データ】回答")).model_copy(
            update={"status": CaseStatus.OPEN}
        ),
    )

    for case_id in ["SYN-1", "SYN-2"]:
        response = client.post(f"/v1/cases/{case_id}/drafts", json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
    assert client.get("/v1/cases/SYN-1").json()["draft"] is None


def test_missing_and_malformed_ids(client: TestClient) -> None:
    assert client.get("/v1/cases/SYN-404").json()["error"]["code"] == "not_found"
    assert client.post("/v1/cases/SYN-404/drafts", json={}).status_code == 404
    assert client.get("/v1/cases/bad..id").status_code == 422
    response = client.post("/v1/drafts/nope/backtranslate", json={"body_ja": "テスト"})
    assert response.status_code == 404


def test_backtranslate_edited_body(client: TestClient, seed: Seed) -> None:
    seed(case("SYN-1"))
    draft_id = client.post("/v1/cases/SYN-1/drafts", json={}).json()["draft"]["id"]

    response = client.post(f"/v1/drafts/{draft_id}/backtranslate", json={"body_ja": "テスト"})
    assert response.status_code == 200
    assert response.json()["back_translation_zh"].startswith("【占位】")
    assert (
        client.post(f"/v1/drafts/{draft_id}/backtranslate", json={"body_ja": ""}).status_code == 422
    )
