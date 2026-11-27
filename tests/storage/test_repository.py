"""仓库接口的测试。只用合成数据（D-002）。

测试通过 `Repository` 接口编写，以后新增实现（例如 Postgres）时把它加进 `repo` fixture 的参数即可。
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command

from copilot.contracts import Case, CaseAnalysis, Draft, Feedback
from copilot.contracts.enums import (
    CaseStatus,
    FeedbackAction,
    InquiryCategory,
    Route,
    Sentiment,
    Urgency,
)
from copilot.storage import (
    NotFoundError,
    Repository,
    SqlRepository,
    create_engine_for,
    session_scope,
    upgrade_database,
)
from copilot.storage.db import alembic_config
from tests.cases import case, player

T0 = datetime(2099, 1, 1, tzinfo=UTC)


class StepClock:
    """每次调用前进一分钟，让“更新时间”的先后可以断言。"""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'copilot.db'}"
    upgrade_database(url)  # 用迁移建表，而不是 create_all：同时验证迁移本身
    return url


@pytest.fixture
def repo(database_url: str) -> Iterator[Repository]:
    engine = create_engine_for(database_url)
    with session_scope(engine) as session:
        yield SqlRepository(session, clock=StepClock())
    engine.dispose()


def analysis(case_id: str, summary: str = "【合成数据】摘要") -> CaseAnalysis:
    return CaseAnalysis(
        case_id=case_id,
        message_seq=1,
        translation_zh="【合成数据】翻译",
        summary_zh=summary,
        category=InquiryCategory.BUG,
        intent="bug_general",
        sentiment=Sentiment.NEUTRAL,
        urgency=Urgency.NORMAL,
        model="fake-llm",
        created_at=T0,
    )


def draft(case_id: str, route: Route = Route.REVIEW) -> Draft:
    return Draft(
        case_id=case_id,
        message_seq=1,
        body_ja="【合成データ】返信",
        back_translation_zh="【合成数据】回复",
        confidence=0.5,
        route=route,
        route_reasons=["【合成数据】理由"],
        model="fake-llm",
        created_at=T0,
    )


def feedback(draft_id: str, action: FeedbackAction = FeedbackAction.APPROVED) -> Feedback:
    edited = action is FeedbackAction.EDITED
    return Feedback(
        draft_id=draft_id,
        actor="客服A",
        action=action,
        final_body_ja="【合成データ】修正後" if edited else None,
        edit_distance=3 if edited else 0,
        created_at=T0,
    )


def test_migrations_match_orm_models(database_url: str) -> None:
    """ORM 模型改了但没有生成迁移时失败（AGENTS.md 第 8 节“改了这些之后”）。"""
    command.check(alembic_config(database_url))


def test_case_round_trip_and_replace(repo: Repository) -> None:
    original = case("SYN-1", content="【合成データ】フォーム")
    repo.save_case(original)
    assert repo.get_case("SYN-1") == original

    replied = original.model_copy(update={"status": CaseStatus.REPLIED})
    repo.save_case(replied)
    assert repo.get_case("SYN-1") == replied
    assert repo.get_case("SYN-404") is None


def test_list_cases_orders_by_latest_update_and_paginates(repo: Repository) -> None:
    for thread_id in ["SYN-1", "SYN-2", "SYN-3"]:
        repo.save_case(case(thread_id))
    repo.save_case(case("SYN-1", player("SYN-1", 1), player("SYN-1", 2)))  # 追问

    page = repo.list_cases(limit=2)
    assert [c.id for c in page.items] == ["SYN-1", "SYN-3"]
    assert page.total == 3
    assert [c.id for c in repo.list_cases(limit=2, offset=2).items] == ["SYN-2"]


def test_list_cases_filters(repo: Repository) -> None:
    repo.save_case(case("SYN-1").model_copy(update={"category": InquiryCategory.BUG}))
    repo.save_case(
        case("SYN-2").model_copy(
            update={"category": InquiryCategory.PURCHASE, "status": CaseStatus.DRAFTED}
        )
    )
    repo.save_case(case("SYN-3"))
    repo.add_draft(draft("SYN-2", Route.ESCALATE))
    repo.add_draft(draft("SYN-3", Route.ESCALATE))
    repo.add_draft(draft("SYN-3", Route.REVIEW))  # 最新草稿决定分流结果

    def ids(page_items: list[Case]) -> list[str]:
        return sorted(c.id for c in page_items)

    assert ids(repo.list_cases(category=InquiryCategory.BUG).items) == ["SYN-1"]
    assert ids(repo.list_cases(status=CaseStatus.DRAFTED).items) == ["SYN-2"]
    assert ids(repo.list_cases(route=Route.ESCALATE).items) == ["SYN-2"]
    assert repo.list_cases(route=Route.AUTO_SEND).total == 0


def test_latest_analysis_and_draft(repo: Repository) -> None:
    repo.save_case(case("SYN-1"))
    assert repo.latest_analysis("SYN-1") is None
    assert repo.latest_draft("SYN-1") is None

    repo.add_analysis(analysis("SYN-1", "第一次"))
    repo.add_analysis(analysis("SYN-1", "第二次"))
    first, second = draft("SYN-1"), draft("SYN-1")
    repo.add_draft(first)
    repo.add_draft(second)

    latest = repo.latest_analysis("SYN-1")
    assert latest is not None and latest.summary_zh == "第二次"
    assert repo.latest_draft("SYN-1") == second
    assert repo.get_draft(first.id) == first


def test_saving_case_keeps_route_of_latest_draft(repo: Repository) -> None:
    """案件被整体替换（例如追问）时，收件箱的分流筛选仍然对应最新草稿。"""
    repo.save_case(case("SYN-1"))
    repo.add_draft(draft("SYN-1", Route.ESCALATE))
    repo.save_case(case("SYN-1", player("SYN-1", 1), player("SYN-1", 2)))

    assert [c.id for c in repo.list_cases(route=Route.ESCALATE).items] == ["SYN-1"]


def test_feedback_round_trip(repo: Repository) -> None:
    repo.save_case(case("SYN-1"))
    one, two = draft("SYN-1"), draft("SYN-1")
    repo.add_draft(one)
    repo.add_draft(two)
    approved, edited = feedback(one.id), feedback(two.id, FeedbackAction.EDITED)
    repo.add_feedback(approved)
    repo.add_feedback(edited)

    assert repo.list_feedback() == [approved, edited]
    assert repo.list_feedback(draft_id=two.id) == [edited]


def test_references_to_missing_objects_are_rejected(repo: Repository) -> None:
    with pytest.raises(NotFoundError):
        repo.add_analysis(analysis("SYN-404"))
    with pytest.raises(NotFoundError):
        repo.add_draft(draft("SYN-404"))
    with pytest.raises(NotFoundError):
        repo.add_feedback(feedback("no-such-draft"))


def test_session_scope_rolls_back_on_error(database_url: str) -> None:
    engine = create_engine_for(database_url)
    with pytest.raises(RuntimeError), session_scope(engine) as session:
        SqlRepository(session).save_case(case("SYN-1"))
        raise RuntimeError("中途失败")

    with session_scope(engine) as session:
        assert SqlRepository(session).get_case("SYN-1") is None
    engine.dispose()
