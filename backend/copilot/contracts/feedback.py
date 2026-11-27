"""客服对草稿的反馈（由 #8 的界面采集），是迭代优化的训练信号（D-001）。"""

from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from copilot.contracts.base import Document
from copilot.contracts.enums import DraftCheckKind, FeedbackAction


class Feedback(Document):
    """客服对一封草稿的处理结果。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    draft_id: str = Field(min_length=1)
    """草稿 ID（`Draft.id`）。"""
    actor: str = Field(min_length=1)
    """处理草稿的客服身份。MVP 中是界面上选择的客服名，真正的身份体系随 #13 引入。"""
    action: FeedbackAction
    """处理方式。"""
    final_body_ja: str | None = None
    """最终发送的正文（日语）。`edited` 时必填；`approved` 时可省略，表示与草稿相同。"""
    reason: str | None = None
    """修改、升级或拒绝的原因（中文），可选。"""
    edit_distance: int = Field(ge=0)
    """草稿 `body_ja` 与 `final_body_ja` 之间按字符计算的编辑距离（Levenshtein）。
    `approved` 时为 0，`edited` 时大于 0。"""
    created_at: AwareDatetime
    """提交时间。"""
    final_body_zh: str | None = None
    """客服在界面中编辑的中文回复。客服用中文工作，日语由 LLM 从它翻译而来；没有编辑中文时
    为空。"""
    ja_edited_directly: bool = False
    """客服是否直接改过日语正文。直接改过时，日语以客服的修改为准，不再由中文重新翻译，
    `final_body_zh` 可能与日语不完全一致。"""
    confirmed_failed_checks: list[DraftCheckKind] = Field(default_factory=list)
    """发送时未通过、由客服确认后仍然发送的自检项。只用于批准和修改后发送。"""

    @model_validator(mode="after")
    def _check_action(self) -> Self:
        if self.action is FeedbackAction.EDITED:
            if self.final_body_ja is None:
                raise ValueError("action 为 edited 时必须提供 final_body_ja")
            if self.edit_distance == 0:
                raise ValueError("action 为 edited 时 edit_distance 必须大于 0")
        if self.action is FeedbackAction.APPROVED and self.edit_distance != 0:
            raise ValueError("action 为 approved 时 edit_distance 必须为 0")
        # 以下只约束新增的取值和字段，不收紧已有数据的含义（不需要升 schema_version）
        sends = self.action in {FeedbackAction.APPROVED, FeedbackAction.EDITED}
        if not sends and self.confirmed_failed_checks:
            raise ValueError("只有批准和修改后发送才能有 confirmed_failed_checks")
        if self.action is FeedbackAction.NO_REPLY:
            if not (self.reason or "").strip():
                raise ValueError("action 为 no_reply 时必须写明 reason")
            if self.final_body_ja is not None or self.edit_distance != 0:
                raise ValueError("action 为 no_reply 时没有发出的正文")
        return self
