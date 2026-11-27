"""坐席流程：案件在工作台中的状态流转（#8）。

API 是这一层之上的薄封装；以后的 #10、#11 调用同样的函数。真实的起草服务
`PipelineDrafter` 在 `copilot.workbench.pipeline` 中，按需导入（它会加载 LLM 客户端等依赖）。
"""

from copilot.workbench.drafting import (
    Drafter,
    DrafterFailedError,
    DrafterUnavailableError,
    StubDrafter,
    latest_player_message,
)
from copilot.workbench.mailbox import SIM_CASE_PREFIX, receive_mail
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
    "SIM_CASE_PREFIX",
    "ConflictError",
    "Drafter",
    "DrafterFailedError",
    "DrafterUnavailableError",
    "FeedbackResult",
    "InvalidRequestError",
    "StubDrafter",
    "generate_draft",
    "latest_player_message",
    "levenshtein",
    "receive_mail",
    "submit_feedback",
]
