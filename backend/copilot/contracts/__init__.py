"""各工作线（#4–#8）共享的数据契约（#3）。实体说明见 docs/architecture.md。"""

from copilot.contracts.analysis import CaseAnalysis
from copilot.contracts.base import Contract, Document
from copilot.contracts.case import (
    CASE_ID_CHARS,
    Case,
    CaseId,
    InquiryForm,
    Message,
    MessageId,
    is_valid_case_id,
    message_id,
)
from copilot.contracts.draft import Draft, DraftCheck, SimilarCaseRef, TemplateRef
from copilot.contracts.enums import (
    CaseStatus,
    DraftCheckKind,
    EntryOrigin,
    FeedbackAction,
    InquiryCategory,
    MessageRole,
    Platform,
    ReviewStatus,
    RiskLevel,
    Route,
    SensitivityTag,
    Sentiment,
    TimeStatus,
    Urgency,
)
from copilot.contracts.feedback import Feedback
from copilot.contracts.knowledge import EntryId, KnowledgeEntry, current_version, select_valid
from copilot.contracts.template import PLACEHOLDER_PATTERN, ReplyTemplate, TemplateSlot
from copilot.contracts.timepoint import TimeContext, TimePoint

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
    "CASE_ID_CHARS",
    "EXPORTED_MODELS",
    "PLACEHOLDER_PATTERN",
    "Case",
    "CaseAnalysis",
    "CaseId",
    "CaseStatus",
    "Contract",
    "Document",
    "Draft",
    "DraftCheck",
    "DraftCheckKind",
    "EntryId",
    "EntryOrigin",
    "Feedback",
    "FeedbackAction",
    "InquiryCategory",
    "InquiryForm",
    "KnowledgeEntry",
    "Message",
    "MessageId",
    "MessageRole",
    "Platform",
    "ReplyTemplate",
    "ReviewStatus",
    "RiskLevel",
    "Route",
    "SensitivityTag",
    "Sentiment",
    "SimilarCaseRef",
    "TemplateRef",
    "TemplateSlot",
    "TimeContext",
    "TimePoint",
    "TimeStatus",
    "Urgency",
    "current_version",
    "is_valid_case_id",
    "message_id",
    "select_valid",
]
