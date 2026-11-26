"""知识库中除回复模板以外的条目类型（提案 0003 §2、§5）。

这里只定义结构；条目的内容由 #5 构建或人工编写。公共字段和版本选择规则见 `knowledge.py`。
"""

import unicodedata
from datetime import datetime
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from copilot.contracts.enums import (
    InquiryCategory,
    NoticeKind,
    Platform,
    PolicyKind,
    RiskLevel,
    SensitivityTag,
)
from copilot.contracts.knowledge import EntryId, KnowledgeEntry


class Intent(KnowledgeEntry):
    """意图：比类别更细的咨询分类。意图是一个封闭的列表，每个模板属于一个意图，
    `CaseAnalysis.intent` 只能取意图目录中的值（提案 0003 §5）。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    category: InquiryCategory
    """所属的咨询类别。"""
    title_zh: str = Field(min_length=1)
    """名称（中文）。"""
    description_zh: str
    """什么样的咨询属于这个意图（中文）。"""
    required_info_zh: list[str] = Field(default_factory=list)
    """回复前需要向玩家索要的信息清单（中文），例如“购买时间”“平台”。#6 据此生成
    `missing_info` 和索要信息的回复。"""
    default_risk: RiskLevel = RiskLevel.NORMAL
    """默认风险等级。"""
    auto_send_eligible: bool = False
    """是否在自动发送允许清单中：只有模板本身就是完整答复的意图才能列入。只有
    `default_risk` 为 `low` 时才能为 true。案件是否真的自动发送还要看其他条件（#6）。"""

    @model_validator(mode="after")
    def _check_auto_send(self) -> Self:
        if self.auto_send_eligible and self.default_risk is not RiskLevel.LOW:
            raise ValueError("只有 default_risk 为 low 的意图才能列入自动发送允许清单")
        return self


class Fact(KnowledgeEntry):
    """一条游戏事实，例如数据迁移需要什么条件。起草时可以引用，但不能编造。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    statement_zh: str = Field(min_length=1)
    """事实的内容（中文）。"""
    statement_ja: str | None = None
    """事实的内容（日语），可选。"""
    intents: list[EntryId] = Field(default_factory=list)
    """与之相关的意图（`Intent.id`）。为空表示不限。"""


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


class Policy(KnowledgeEntry):
    """一条政策：不允许的承诺，或升级触发条件。由代码检查（提案 0003 §2）。

    匹配用固定的日语短语（NFKC 规范化后的子串），不用正则，避免构造的输入引起回溯（#18）。
    """

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    kind: PolicyKind
    """政策的类型。"""
    title_zh: str = Field(min_length=1)
    """名称（中文）。"""
    description_zh: str
    """政策的内容和理由（中文）。"""
    phrases_ja: list[str] = Field(default_factory=list)
    """要匹配的日语短语。NFKC 规范化后按子串匹配，全角半角视为相同。"""
    sensitivity_tags: list[SensitivityTag] = Field(default_factory=list)
    """触发升级的敏感标签，只用于 `escalation_trigger`。"""
    intents: list[EntryId] = Field(default_factory=list)
    """适用的意图（`Intent.id`）。为空表示适用于所有意图。"""

    @model_validator(mode="after")
    def _check_conditions(self) -> Self:
        if any(not phrase.strip() for phrase in self.phrases_ja):
            raise ValueError("phrases_ja 中不能有空短语")
        if self.kind is PolicyKind.FORBIDDEN_PROMISE:
            if not self.phrases_ja:
                raise ValueError("forbidden_promise 必须有 phrases_ja")
            if self.sensitivity_tags:
                raise ValueError("sensitivity_tags 只用于 escalation_trigger")
        if not self.phrases_ja and not self.sensitivity_tags:
            raise ValueError(
                "政策必须至少有一个可以由代码检查的条件（phrases_ja 或 sensitivity_tags）"
            )
        return self

    def matching_phrases(self, text: str) -> list[str]:
        """`text` 中出现的短语（按 `phrases_ja` 的顺序）。"""
        normalized = _normalize(text)
        return [phrase for phrase in self.phrases_ja if _normalize(phrase) in normalized]


class GlossaryEntry(KnowledgeEntry):
    """日中术语表的一个条目，供翻译、回译和中文说明保持一致。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    term_ja: str = Field(min_length=1)
    """日语术语。"""
    term_zh: str = Field(min_length=1)
    """对应的中文译法。"""
    reading_ja: str | None = None
    """读音（假名），可选。"""
    note_zh: str | None = None
    """用法说明（中文），可选。"""


class Notice(KnowledgeEntry):
    """一条公告或事件：事故、维护、修复、政策变更（提案 0003 §2）。

    有两个时间范围：
    - **有效期**（`valid_from`/`valid_to`，公共字段）：这条公告对起草有用的时间，按“现在”判断；
    - **事件时间**（`event_start`/`event_end`）：事件实际发生的时间，与案件的问题发生时间
      匹配。例如 3 月 2 日的事故，3 月 5 日来信的玩家仍然要能匹配上。
    """

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    kind: NoticeKind
    """类型。"""
    title_zh: str = Field(min_length=1)
    """标题（中文）。"""
    description_zh: str
    """说明（中文）：发生了什么、影响了谁、如何应对。"""
    event_start: AwareDatetime
    """事件开始的时间（包含）。"""
    event_end: AwareDatetime | None = None
    """事件结束的时间（不包含）。为空表示仍在持续。"""
    platforms: list[Platform] = Field(default_factory=list)
    """受影响的平台。为空表示不限。"""
    app_versions: list[str] = Field(default_factory=list)
    """受影响的 App 版本，例如 `1.4.0`。为空表示不限。"""
    intents: list[EntryId] = Field(default_factory=list)
    """相关的意图（`Intent.id`）。为空表示不限。"""
    template_ids: list[EntryId] = Field(default_factory=list)
    """只在这个事件适用时才提供给起草步骤的模板（`ReplyTemplate.id`）。"""

    @model_validator(mode="after")
    def _check_event(self) -> Self:
        if self.event_end is not None and self.event_start >= self.event_end:
            raise ValueError("event_start 必须早于 event_end")
        return self

    def event_covers(self, at: datetime) -> bool:
        """事件时间是否包含 `at`。时间不确定时的判断（区间跨过边界）由使用方（#6）进行。"""
        if at < self.event_start:
            return False
        return self.event_end is None or at < self.event_end
