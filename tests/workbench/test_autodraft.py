"""来信后自动起草与自动发送（#8，D-018）。SYNTHETIC：合成数据，占位起草服务，不调用 LLM。"""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from copilot.api import create_app
from copilot.config import Settings
from copilot.contracts import Case, CaseStatus, InquiryCategory, Route
from copilot.draft.service import DraftOutcome
from copilot.storage import SqlRepository, create_engine_for, session_scope, upgrade_database
from copilot.workbench import DrafterFailedError, StubDrafter
from copilot.workbench.autodraft import AUTO_SEND_ACTOR, AutoDrafter
from tests.cases import case, player


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_database(url)
    engine = create_engine_for(url)
    yield engine
    engine.dispose()


def _save(engine: Engine, *cases: Case) -> None:
    with session_scope(engine) as session:
        repo = SqlRepository(session)
        for item in cases:
            repo.save_case(item)


def _read(engine: Engine, case_id: str) -> tuple[Case, list[str], list[str]]:
    with session_scope(engine) as session:
        repo = SqlRepository(session)
        stored = repo.get_case(case_id)
        assert stored is not None
        draft = repo.latest_draft(case_id)
        drafts = [draft.id] if draft else []
        mails = [m.sent_by for m in repo.list_outbound_mail(case_id=case_id).items]
        return stored, drafts, mails


def _cat(item: Case, category: InquiryCategory) -> Case:
    return item.model_copy(update={"category": category})


def test_scan_finds_cases_without_a_draft_for_the_latest_mail(engine: Engine) -> None:
    _save(
        engine,
        case("SYN-1"),
        case("SYN-2"),
        case("SYN-3").model_copy(update={"status": CaseStatus.REPLIED}),
    )
    auto = AutoDrafter(engine, StubDrafter())

    assert auto.scan() == [("SYN-1", 1), ("SYN-2", 1)]
    assert auto.status("SYN-1") is not None and auto.busy("SYN-1")
    assert auto.scan() == []  # 已在排队

    auto.run_job("SYN-1", 1)
    stored, drafts, _ = _read(engine, "SYN-1")
    assert stored.status is CaseStatus.DRAFTED and len(drafts) == 1
    assert auto.status("SYN-1") is None

    # 追问：最新的玩家邮件没有草稿，重新起草
    _save(engine, case("SYN-1", player("SYN-1", 1), player("SYN-1", 2)))
    assert ("SYN-1", 2) in auto.scan()


class _FollowUpDuringDraft(StubDrafter):
    """起草期间玩家又来信。"""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine

    def draft(self, case: Case, *, background: bool = False) -> DraftOutcome:
        outcome = super().draft(case, background=background)
        _save(
            self._engine, case.model_copy(update={"messages": [*case.messages, player(case.id, 2)]})
        )
        return outcome


def test_result_is_discarded_when_the_player_writes_again(engine: Engine) -> None:
    _save(engine, case("SYN-1"))
    auto = AutoDrafter(engine, _FollowUpDuringDraft(engine))
    auto.scan()
    auto.run_job("SYN-1", 1)

    _, drafts, _ = _read(engine, "SYN-1")
    assert drafts == [] and auto.status("SYN-1") is None
    assert auto.scan() == [("SYN-1", 2)]


class _Broken(StubDrafter):
    def draft(self, case: Case, *, background: bool = False) -> DraftOutcome:
        raise DrafterFailedError("起草服务调用失败，请稍后重试")


def test_failure_is_recorded_and_not_retried_for_the_same_mail(engine: Engine) -> None:
    _save(engine, case("SYN-1"))
    auto = AutoDrafter(engine, _Broken())
    auto.scan()
    auto.run_job("SYN-1", 1)

    job = auto.status("SYN-1")
    assert job is not None and job.state == "failed" and job.error and "失败" in job.error
    assert not auto.busy("SYN-1")
    assert auto.scan() == []
    auto.forget("SYN-1")
    assert auto.scan() == [("SYN-1", 1)]


@pytest.mark.parametrize("enabled", [True, False])
def test_auto_send_only_when_enabled(engine: Engine, enabled: bool) -> None:
    """占位起草服务把“意见建议”分流为 auto_send；开启自动发送时由系统发出。"""
    _save(
        engine,
        _cat(case("SYN-1"), InquiryCategory.FEEDBACK),
        _cat(case("SYN-2"), InquiryCategory.BUG),
    )
    auto = AutoDrafter(engine, _AutoSendable(), auto_send=enabled)
    for case_id, seq in auto.scan():
        auto.run_job(case_id, seq)

    feedback, _, mails = _read(engine, "SYN-1")
    assert (feedback.status is CaseStatus.REPLIED) is enabled
    assert mails == ([AUTO_SEND_ACTOR] if enabled else [])
    bug, _, bug_mails = _read(engine, "SYN-2")
    assert bug.status is CaseStatus.DRAFTED and bug_mails == []  # review 的草稿从不自动发送


class _AutoSendable(StubDrafter):
    """占位实现的 auto_send 草稿正文不符合 #6 的结构检查；这里给它一个全部通过的检查结果。"""

    def check(self, body_ja: str) -> list:  # type: ignore[type-arg]
        return []


def test_auto_send_respects_the_draft_route(engine: Engine) -> None:
    _save(engine, _cat(case("SYN-1"), InquiryCategory.FEEDBACK))
    auto = AutoDrafter(engine, _AutoSendable(), auto_send=True)
    auto.scan()
    auto.run_job("SYN-1", 1)
    with session_scope(engine) as session:
        draft = SqlRepository(session).latest_draft("SYN-1")
    assert draft is not None and draft.route is Route.AUTO_SEND


def test_app_drafts_delivered_mail_in_the_background(tmp_path: Path) -> None:
    """端到端：投递来信后，不需要手动操作，草稿会出现；处理中时不能手动起草。"""
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_database(url)
    app = create_app(
        Settings(database_url=url, workbench_drafter="stub", workbench_auto_draft=True)
    )
    with TestClient(app) as client:
        case_id = client.post("/v1/sim/inbox", json={"body": "【合成データ】動きません"}).json()[
            "case_id"
        ]
        deadline = time.monotonic() + 10
        detail = client.get(f"/v1/cases/{case_id}").json()
        while detail["draft"] is None and time.monotonic() < deadline:
            time.sleep(0.1)
            detail = client.get(f"/v1/cases/{case_id}").json()
        assert detail["draft"] is not None
        assert detail["drafting"] is None
        assert detail["case"]["status"] == "drafted"
