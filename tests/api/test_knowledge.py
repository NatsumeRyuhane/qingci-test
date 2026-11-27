"""模板与指标端点的测试。只用合成数据（D-002）。"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot.contracts import Case, CaseStatus, Draft, ReviewStatus, Route, TemplateRef
from copilot.draft.service import DraftOutcome
from copilot.kb.store import KnowledgeStore
from copilot.storage import create_engine_for
from copilot.workbench import StubDrafter
from tests.cases import case
from tests.draft.factories import template

Seed = Callable[..., None]


@pytest.fixture
def published(app: FastAPI) -> None:
    KnowledgeStore(create_engine_for(app.state.database_url)).add_all(
        [
            template("tpl-a", intent_id="syn_bug"),
            template("tpl-a", intent_id="syn_bug", version=2, status=ReviewStatus.NEEDS_REREVIEW),
            template("tpl-b", intent_id="syn_other"),
            template("tpl-draft", status=ReviewStatus.DRAFT),
            template("tpl-retired", status=ReviewStatus.APPROVED),
            template("tpl-retired", version=2, status=ReviewStatus.RETIRED),
        ]
    )


@pytest.mark.usefixtures("published")
def test_list_templates_returns_only_current_approved_versions(client: TestClient) -> None:
    items = client.get("/v1/templates").json()["items"]
    # tpl-a 当前版本待重审，tpl-draft 未审核，tpl-retired 已下线：都不返回
    assert [(t["id"], t["version"]) for t in items] == [("tpl-b", 1)]
    assert client.get("/v1/templates", params={"intent": "syn_bug"}).json()["items"] == []
    assert len(client.get("/v1/templates", params={"category": "bug"}).json()["items"]) == 1


@pytest.mark.usefixtures("published")
def test_get_template_by_version(client: TestClient) -> None:
    """草稿引用具体版本：旧的已批准版本仍然可以查看，未批准的版本按不存在处理。"""
    assert client.get("/v1/templates/tpl-a", params={"version": 1}).json()["version"] == 1
    assert client.get("/v1/templates/tpl-a").status_code == 404
    assert client.get("/v1/templates/tpl-a", params={"version": 2}).status_code == 404
    assert client.get("/v1/templates/tpl-draft").status_code == 404
    assert client.get("/v1/templates/tpl-b").json()["id"] == "tpl-b"
    assert client.get("/v1/templates/BAD ID").status_code == 422


class _TemplateDrafter(StubDrafter):
    """草稿引用模板 `tpl-b`，分流按案件 ID 末尾决定，方便构造指标。"""

    def draft(self, case: Case) -> DraftOutcome:
        outcome = super().draft(case)
        assert outcome.draft is not None
        route = Route.AUTO_SEND if case.id.endswith("a") else Route.REVIEW
        draft: Draft = outcome.draft.model_copy(
            update={
                "route": route,
                "template_refs": [
                    TemplateRef(template_id="tpl-b", template_version=1, reason_zh="【合成】")
                ],
            }
        )
        return outcome.model_copy(update={"draft": draft, "proposed_status": CaseStatus.DRAFTED})


def _handle(client: TestClient, case_id: str, *steps: dict[str, Any]) -> None:
    draft = client.post(f"/v1/cases/{case_id}/drafts").json()["draft"]
    for step in steps:
        body = {"actor": "客服A", **step}
        if step.get("action") == "edited":
            body["final_body_ja"] = draft["body_ja"] + "追記"
        assert client.post(f"/v1/drafts/{draft['id']}/feedback", json=body).status_code == 201


def test_metrics(app: FastAPI, client: TestClient, seed: Seed) -> None:
    assert client.get("/v1/metrics").json()["total"]["handled"] == 0

    app.state.drafter = _TemplateDrafter()
    seed(case("SYN-1a"), case("SYN-2a"), case("SYN-3"), case("SYN-4"), case("SYN-5"))
    _handle(client, "SYN-1a", {"action": "approved"})
    _handle(client, "SYN-2a", {"action": "edited"})
    _handle(client, "SYN-3", {"action": "escalated", "reason": "升级"})
    # 先升级、后批准：以最后一次处理为准
    _handle(client, "SYN-4", {"action": "escalated", "reason": "升级"}, {"action": "approved"})
    client.post("/v1/cases/SYN-5/drafts")  # 没有处理的草稿不计入

    metrics = client.get("/v1/metrics").json()
    total = metrics["total"]
    assert (total["handled"], total["approved"], total["edited"], total["escalated"]) == (
        4,
        2,
        1,
        1,
    )
    assert total["approved_rate"] == 0.5
    assert total["avg_edit_distance"] == pytest.approx(2 / 3)
    assert [row["key"] for row in metrics["by_template"]] == ["tpl-b"]
    routes = {row["key"]: row for row in metrics["by_route"]}
    assert routes["auto_send"]["handled"] == 2 and routes["auto_send"]["edited_rate"] == 0.5
    assert routes["review"]["escalated"] == 1
