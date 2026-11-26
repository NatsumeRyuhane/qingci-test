"""回复草稿（由 #6 的起草与分流步骤生成）。"""

from typing import Literal, Self
from uuid import uuid4

from pydantic import AwareDatetime, Field, model_validator

from copilot.contracts.base import Contract, Document
from copilot.contracts.enums import DraftCheckKind, Route


class TemplateRef(Contract):
    """草稿引用的一个模板。"""

    template_id: str
    """模板 ID（`ReplyTemplate.id`）。"""
    template_version: int = Field(ge=1)
    """模板版本（`ReplyTemplate.version`）。"""
    reason_zh: str
    """为什么选择这个模板（中文），展示给客服。"""


class SimilarCaseRef(Contract):
    """检索到的一个相似历史案件。"""

    case_id: str
    """相似案件的 ID。"""
    score: float = Field(allow_inf_nan=False)
    """相似度分数，越大越相似。只在同一次检索的结果之间可比。"""
    reason_zh: str | None = None
    """为什么认为相似（中文），可选。"""


class DraftCheck(Contract):
    """对草稿做的一项自动检查的结果。"""

    kind: DraftCheckKind
    """检查项。"""
    passed: bool
    """是否通过。"""
    detail_zh: str | None = None
    """说明（中文）。不通过时写明问题在哪里。"""


class Draft(Document):
    """一封回复草稿及其分流结果。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    """草稿 ID，`Feedback.draft_id` 引用它。默认随机生成。"""
    case_id: str = Field(min_length=1)
    """所属案件 ID。"""
    message_seq: int = Field(ge=1)
    """草稿回复的玩家邮件的 `seq`。"""
    template_refs: list[TemplateRef] = Field(default_factory=list)
    """使用的模板及选择理由。没有合适模板时为空。"""
    similar_case_refs: list[SimilarCaseRef] = Field(default_factory=list)
    """参考的相似历史案件。"""
    body_ja: str
    """回复正文（日语）。"""
    back_translation_zh: str
    """`body_ja` 的中文回译，供客服核对内容。"""
    checks: list[DraftCheck] = Field(default_factory=list)
    """自动检查的结果。"""
    confidence: float = Field(ge=0.0, le=1.0)
    """对草稿正确性的信心，0 到 1。"""
    route: Route
    """分流结果。"""
    route_reasons: list[str] = Field(min_length=1)
    """分流的理由（中文），至少一条。客服需要知道为什么要审核或升级。"""
    model: str
    """生成草稿的 LLM 模型名。"""
    created_at: AwareDatetime
    """生成时间。"""

    @model_validator(mode="after")
    def _check_route(self) -> Self:
        failed = [check.kind.value for check in self.checks if not check.passed]
        if self.route is Route.AUTO_SEND and failed:
            raise ValueError(f"有未通过的检查 {failed}，不能自动发送")
        return self
