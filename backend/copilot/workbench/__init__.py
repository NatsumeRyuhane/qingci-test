"""坐席流程：案件在工作台中的状态流转（#8）。

API 是这一层之上的薄封装；以后的 #10、#11 调用同样的函数。
"""

from copilot.workbench.service import (
    CLOSED_STATUSES,
    ConflictError,
    FeedbackResult,
    InvalidRequestError,
    generate_draft,
    submit_feedback,
)
from copilot.workbench.text import levenshtein

__all__ = [
    "CLOSED_STATUSES",
    "ConflictError",
    "FeedbackResult",
    "InvalidRequestError",
    "generate_draft",
    "levenshtein",
    "submit_feedback",
]
