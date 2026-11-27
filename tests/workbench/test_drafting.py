"""占位起草实现的测试。只用合成数据（D-002）。"""

import pytest

from copilot.contracts import Case, CaseStatus, InquiryCategory, Route
from copilot.workbench import Drafter, StubDrafter
from tests.cases import agent, case, player


def _with_category(category: InquiryCategory | None) -> Case:
    return case("SYN-1").model_copy(update={"category": category})


@pytest.mark.parametrize(
    ("category", "route", "status"),
    [
        (InquiryCategory.PURCHASE, Route.ESCALATE, CaseStatus.ESCALATED),
        (InquiryCategory.DATA_TRANSFER, Route.ESCALATE, CaseStatus.ESCALATED),
        (InquiryCategory.FEEDBACK, Route.AUTO_SEND, CaseStatus.DRAFTED),
        (InquiryCategory.BUG, Route.REVIEW, CaseStatus.DRAFTED),
        (None, Route.REVIEW, CaseStatus.DRAFTED),
    ],
)
def test_routes_by_category(
    category: InquiryCategory | None, route: Route, status: CaseStatus
) -> None:
    drafter: Drafter = StubDrafter()
    outcome = drafter.draft(_with_category(category))

    assert outcome.draft is not None
    assert outcome.draft.route is route
    assert outcome.proposed_status is status
    assert all("占位" in reason for reason in outcome.draft.route_reasons)
    assert outcome.analysis.category is (category or InquiryCategory.OTHER)


def test_drafts_reply_to_latest_player_message() -> None:
    thread = case(
        "SYN-1",
        player("SYN-1", 1),
        agent("SYN-1", 2, "【合成データ】回答"),
        player("SYN-1", 3, "【合成データ】追加の質問"),
    )
    outcome = StubDrafter().draft(thread)

    assert outcome.draft is not None
    assert outcome.analysis.message_seq == outcome.draft.message_seq == 3
    assert "追加の質問" in outcome.analysis.summary_zh
    assert outcome.draft.body_ja.startswith("いつもご利用いただきありがとうございます。")


def test_case_without_player_message_is_rejected() -> None:
    with pytest.raises(ValueError, match="没有玩家邮件"):
        StubDrafter().draft(case("SYN-1", agent("SYN-1", 1, "【合成データ】回答")))


def test_backtranslate_is_marked_as_placeholder() -> None:
    assert StubDrafter().backtranslate("テスト").startswith("【占位】")
