"""起草服务的接口。"""

from dataclasses import dataclass
from typing import Protocol

from copilot.contracts import Case, CaseAnalysis, Draft, Message
from copilot.contracts.enums import MessageRole


@dataclass(frozen=True)
class DraftResult:
    """对案件最新一封玩家邮件的分析和回复草稿。"""

    analysis: CaseAnalysis
    draft: Draft


class Drafter(Protocol):
    """分析一个案件并起草回复（#6）。"""

    def draft(self, case: Case, *, template_id: str | None = None) -> DraftResult:
        """分析最新一封玩家邮件并起草回复。`template_id` 由客服指定时优先使用该模板。

        案件中没有玩家邮件时抛出 `ValueError`。
        """
        ...

    def backtranslate(self, body_ja: str) -> str:
        """把（客服编辑后的）日语回复回译为中文，供不懂日语的客服核对。"""
        ...


def latest_player_message(case: Case) -> Message:
    """案件中最新一封玩家邮件。没有时抛出 `ValueError`。"""
    for message in reversed(case.messages):
        if message.role is MessageRole.PLAYER:
            return message
    raise ValueError(f"案件 {case.id!r} 中没有玩家邮件")
