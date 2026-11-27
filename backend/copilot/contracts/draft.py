"""回复草稿（由 #6 的起草与分流步骤生成）。"""

from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from copilot.contracts.base import Contract, Document
from copilot.contracts.enums import DraftCheckKind, DraftMode, EntryType, Route
from copilot.contracts.knowledge import EntryId
from copilot.contracts.timepoint import TimeContext

ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
"""工具名，例如 `search_facts`。可用的工具由 #6 定义。"""


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


class Citation(Contract):
    """草稿中的一个说法引用的知识库条目。没有引用的事实性说法会在检查中标出（提案 0003 §10）。"""

    entry_type: EntryType
    """条目的类型。"""
    entry_id: EntryId
    """条目 ID。"""
    entry_version: int = Field(ge=1)
    """条目版本。引用具体版本，条目修改后旧草稿引用的内容不变。"""
    claim_zh: str = Field(min_length=1)
    """草稿中引用它的说法（中文），展示给客服。"""


class TraceStep(Contract):
    """工具循环中的一步：模型调用了哪个工具、参数和结果（提案 0003 §10）。"""

    step: int = Field(ge=1)
    """第几步，从 1 开始连续编号。"""
    tool: ToolName
    """调用的工具名。"""
    arguments: dict[str, str] = Field(default_factory=dict)
    """调用参数。工具执行前会按各自的参数模型再校验一次。"""
    result_summary_zh: str | None = None
    """结果摘要（中文）。工具结果和邮件正文一样是不可信数据，这里只记录摘要。"""
    error: str | None = None
    """调用失败时的原因，例如参数校验失败、超出范围、超时。成功时为空。"""


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
    citations: list[Citation] = Field(default_factory=list)
    """草稿中的说法引用的知识库条目。"""
    mode: DraftMode = DraftMode.PIPELINE
    """生成方式。"""
    trace: list[TraceStep] = Field(default_factory=list)
    """工具调用的轨迹，在界面中展示。只有 `pipeline_with_tools` 才有。"""
    fallback_reason: str | None = None
    """工具循环失败、回退到固定流水线的原因（中文），例如超出步数、输出无法校验。
    只有 `pipeline_with_tools` 才有。"""
    time_context: TimeContext | None = None
    """起草时的时间上下文（“现在”和问题发生时间），用于复现草稿。"""
    model: str
    """生成草稿的 LLM 模型名。"""
    created_at: AwareDatetime
    """生成时间。"""

    @model_validator(mode="after")
    def _check_trace(self) -> Self:
        if self.mode is DraftMode.PIPELINE and (self.trace or self.fallback_reason):
            raise ValueError("固定流水线（pipeline）的草稿不能有 trace 或 fallback_reason")
        steps = [step.step for step in self.trace]
        if steps != list(range(1, len(steps) + 1)):
            raise ValueError(f"trace 的步数必须从 1 开始连续编号，实际为 {steps}")
        return self

    @model_validator(mode="after")
    def _check_route(self) -> Self:
        failed = [check.kind.value for check in self.checks if not check.passed]
        if self.route is Route.AUTO_SEND and failed:
            raise ValueError(f"有未通过的检查 {failed}，不能自动发送")
        return self
