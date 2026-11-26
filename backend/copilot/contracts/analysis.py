"""咨询分析结果（由 #6 的分析步骤生成）。"""

from typing import Literal

from pydantic import AwareDatetime, Field

from copilot.contracts.base import Document
from copilot.contracts.enums import InquiryCategory, SensitivityTag, Sentiment, Urgency


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
    model: str
    """生成分析的 LLM 模型名。"""
    created_at: AwareDatetime
    """生成时间。"""
