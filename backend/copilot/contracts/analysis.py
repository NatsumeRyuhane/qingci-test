"""咨询分析结果（由 #6 的分析步骤生成）。"""

from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from copilot.contracts.base import Contract, Document
from copilot.contracts.case import CaseId
from copilot.contracts.enums import (
    InquiryCategory,
    NoReplyReason,
    SensitivityTag,
    Sentiment,
    Urgency,
)


class NoReplyProposal(Contract):
    """分析步骤提议“这个案件不需要回复”（提案 0003 §8）。

    只是提议：是否自动把案件设为 `no_reply_needed`，由 #6 按原因和证据判断。
    """

    reason: NoReplyReason
    """原因。"""
    reason_zh: str = Field(min_length=1)
    """理由（中文），展示给客服。"""
    duplicate_of: CaseId | None = None
    """重复提交时，对应的那个线程（`Case.id`）。原因为 `duplicate` 时必填，其他原因时为空。"""

    @model_validator(mode="after")
    def _check_duplicate(self) -> Self:
        if (self.reason is NoReplyReason.DUPLICATE) != (self.duplicate_of is not None):
            raise ValueError("只有原因为 duplicate 时才有 duplicate_of，并且此时必填")
        return self


class CaseAnalysis(Document):
    """对案件中一封玩家邮件的分析。玩家追问时重新分析，生成新的一条。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    case_id: str = Field(min_length=1)
    """被分析的案件 ID。"""
    message_seq: int = Field(ge=1)
    """分析针对的玩家邮件的 `seq`，通常是最新一封。"""
    translation_zh: str
    """这封玩家邮件（去除引用后）的中文翻译。"""
    summary_zh: str
    """结合整个线程的中文摘要：玩家遇到了什么问题、想要什么。"""
    category: InquiryCategory
    """判断出的咨询类别。可能与玩家在表单中选的不同。"""
    intent: str = Field(min_length=1)
    """更细的意图标识，例如 `purchase_not_delivered`。取值集合由知识库（#5）定义。"""
    sentiment: Sentiment
    """玩家的情绪。"""
    urgency: Urgency
    """处理的紧急程度。"""
    facts: dict[str, str] = Field(default_factory=dict)
    """从邮件和表单中提取的事实，例如 `{"device": "iPhone 15", "item": "ジェム100個"}`。"""
    missing_info: list[str] = Field(default_factory=list)
    """回复前还需要向玩家确认的信息（中文），例如“购买时间”。"""
    sensitivity_tags: list[SensitivityTag] = Field(default_factory=list)
    """提议的敏感标签。为访问控制（#13）预留，经人工确认后才写入 `Case.sensitivity_tags`。"""
    no_reply: NoReplyProposal | None = None
    """提议不回复时的理由。为空表示需要回复。"""
    model: str
    """生成分析的 LLM 模型名。"""
    created_at: AwareDatetime
    """生成时间。"""

    @model_validator(mode="after")
    def _check_no_reply(self) -> Self:
        if self.no_reply and self.no_reply.duplicate_of == self.case_id:
            raise ValueError("案件不能是自己的重复提交（duplicate_of 等于 case_id）")
        return self
