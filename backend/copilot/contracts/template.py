"""回复模板（知识库条目，由 #5 构建）。"""

import re
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from copilot.contracts.base import Contract, Document
from copilot.contracts.case import MessageId
from copilot.contracts.enums import InquiryCategory, RiskLevel

SlotName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
"""占位符名：小写字母开头，只含小写字母、数字和下划线。"""

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
"""`body_ja` 中占位符的写法：`{{slot_name}}`。"""


class TemplateSlot(Contract):
    """模板中需要按案件填写的占位符。"""

    name: SlotName
    """占位符名，在 `body_ja` 中写作 `{{name}}`。"""
    description_zh: str
    """要填写什么（中文），例如“玩家购买的道具名称”。"""
    required: bool = True
    """是否必填。必填项没有填写时，草稿检查 `slots_filled` 不通过。"""
    example_ja: str | None = None
    """填写示例（日语）。"""


class ReplyTemplate(Document):
    """一个回复模板。由历史客服回复归纳而来，供检索和起草使用。

    同一个 `id` 的模板可以有多个 `version`。草稿通过（`id`, `version`）引用模板，
    所以修改模板时要新建版本，而不是改写旧版本。
    """

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    id: str = Field(min_length=1)
    """模板 ID，跨版本不变。"""
    version: int = Field(ge=1)
    """模板版本，从 1 开始。"""
    category: InquiryCategory
    """模板适用的咨询类别。"""
    title_zh: str = Field(min_length=1)
    """标题（中文），在界面中展示给客服。"""
    description_zh: str
    """这个模板回答的是什么问题（中文）。"""
    use_when_zh: str
    """什么情况下使用（中文）。检索和 LLM 选择模板时参考。"""
    avoid_when_zh: str
    """什么情况下不要使用（中文），例如容易混淆的相似问题。"""
    body_ja: str = Field(min_length=1)
    """回复正文（日语），包含 `{{slot_name}}` 形式的占位符。"""
    slots: list[TemplateSlot] = Field(default_factory=list)
    """`body_ja` 中的占位符。与正文中出现的占位符必须一一对应。"""
    valid_from: AwareDatetime | None = None
    """生效时间。只适用于某次事故的模板才需要（见 docs/data.md 关键发现 2）。"""
    valid_to: AwareDatetime | None = None
    """失效时间。为空表示一直有效。"""
    risk: RiskLevel = RiskLevel.NORMAL
    """风险等级，例如涉及退款、补偿的模板为 `high`。"""
    auto_send_allowed: bool = False
    """是否允许不经人工审核自动发送。只有 `risk` 为 `low` 时才能为 true（D-001）。"""
    source_message_ids: list[MessageId] = Field(default_factory=list)
    """归纳出这个模板的历史客服回复，格式为 `<thread_id>:<seq>`。"""

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        names = [slot.name for slot in self.slots]
        if len(names) != len(set(names)):
            raise ValueError(f"占位符名重复：{names}")
        in_body = set(PLACEHOLDER_PATTERN.findall(self.body_ja))
        if in_body != set(names):
            raise ValueError(
                f"body_ja 中的占位符 {sorted(in_body)} 与 slots {sorted(names)} 不一致"
            )
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise ValueError("valid_from 必须早于 valid_to")
        if self.auto_send_allowed and self.risk is not RiskLevel.LOW:
            raise ValueError("只有 risk 为 low 的模板才能允许自动发送")
        return self
