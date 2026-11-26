"""各工作线（#4–#8）共享的数据契约（#3）。实体说明见 docs/architecture.md。"""

from copilot.contracts.analysis import CaseAnalysis
from copilot.contracts.base import Contract, Document
from copilot.contracts.case import Case, InquiryForm, Message, MessageId, message_id
from copilot.contracts.draft import Draft, DraftCheck, SimilarCaseRef, TemplateRef
from copilot.contracts.enums import (
    CaseStatus,
    DraftCheckKind,
    FeedbackAction,
    InquiryCategory,
    MessageRole,
    Platform,
    RiskLevel,
    Route,
    SensitivityTag,
    Sentiment,
    Urgency,
)
from copilot.contracts.feedback import Feedback
from copilot.contracts.template import PLACEHOLDER_PATTERN, ReplyTemplate, TemplateSlot

EXPORTED_MODELS: tuple[type[Contract], ...] = (
    Message,
    InquiryForm,
    Case,
    ReplyTemplate,
    CaseAnalysis,
    Draft,
    Feedback,
)
"""对外发布 JSON Schema 的模型（`copilot contracts export`）。嵌套模型包含在各自的 `$defs` 中。"""

__all__ = [
    "EXPORTED_MODELS",
    "PLACEHOLDER_PATTERN",
    "Case",
    "CaseAnalysis",
    "CaseStatus",
    "Contract",
    "Document",
    "Draft",
    "DraftCheck",
    "DraftCheckKind",
    "Feedback",
    "FeedbackAction",
    "InquiryCategory",
    "InquiryForm",
    "Message",
    "MessageId",
    "MessageRole",
    "Platform",
    "ReplyTemplate",
    "RiskLevel",
    "Route",
    "SensitivityTag",
    "Sentiment",
    "SimilarCaseRef",
    "TemplateRef",
    "TemplateSlot",
    "Urgency",
    "message_id",
]
