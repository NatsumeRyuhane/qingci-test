"""占位起草实现的测试。只用合成数据（D-002）。"""

import pytest

from copilot.contracts import Case, InquiryForm
from copilot.contracts.enums import InquiryCategory, Route
from copilot.draft import Drafter, StubDrafter
from tests.cases import agent, case, player


def _with_category(category: InquiryCategory | None) -> Case:
    base = case("SYN-1")
    return base.model_copy(update={"form": InquiryForm(category=category)})


@pytest.mark.parametrize(
    ("category", "route"),
    [
        (InquiryCategory.PURCHASE, Route.ESCALATE),
        (InquiryCategory.DATA_TRANSFER, Route.ESCALATE),
        (InquiryCategory.FEEDBACK, Route.AUTO_SEND),
        (InquiryCategory.BUG, Route.REVIEW),
        (None, Route.REVIEW),
    ],
)
def test_routes_by_form_category(category: InquiryCategory | None, route: Route) -> None:
    drafter: Drafter = StubDrafter()
    result = drafter.draft(_with_category(category))

    assert result.draft.route is route
    assert all("占位" in reason for reason in result.draft.route_reasons)
    assert result.analysis.category is (category or InquiryCategory.OTHER)


def test_drafts_reply_to_latest_player_message() -> None:
    thread = case(
        "SYN-1",
        player("SYN-1", 1),
        agent("SYN-1", 2, "【合成データ】回答"),
        player("SYN-1", 3, "【合成データ】追加の質問"),
    )
    result = StubDrafter().draft(thread)

    assert result.analysis.message_seq == result.draft.message_seq == 3
    assert result.analysis.summary_zh.startswith("【占位】")
    assert "追加の質問" in result.analysis.summary_zh
    assert result.draft.body_ja.startswith("いつもご利用いただきありがとうございます。")


def test_case_without_player_message_is_rejected() -> None:
    thread = case("SYN-1", agent("SYN-1", 1, "【合成データ】回答"))
    with pytest.raises(ValueError, match="没有玩家邮件"):
        StubDrafter().draft(thread)


def test_backtranslate_is_marked_as_placeholder() -> None:
    assert StubDrafter().backtranslate("テスト").startswith("【占位】")
