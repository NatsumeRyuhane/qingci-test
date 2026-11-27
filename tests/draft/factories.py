"""起草测试共用的合成知识库条目与时间。

SYNTHETIC：这里构造的内容全部是合成数据，不来自客户的样本表格（D-002）。
"""

from datetime import datetime
from typing import Any

from copilot.contracts import (
    EntryOrigin,
    Fact,
    GlossaryEntry,
    InquiryCategory,
    Intent,
    Notice,
    NoticeKind,
    Policy,
    PolicyKind,
    ReplyTemplate,
    ReviewStatus,
    RiskLevel,
    SensitivityTag,
    TemplateSlot,
    TimeContext,
    TimePoint,
    TimeStatus,
)
from copilot.ingest.timestamps import JST

KNOWN = datetime(2099, 1, 1, tzinfo=JST)
"""合成条目的 known_from / recorded_at。"""


def dt(month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2099 年的合成时间（JST）。"""
    return datetime(2099, month, day, hour, minute, tzinfo=JST)


def _common(entry_id: str, version: int, **values: Any) -> dict[str, Any]:
    return {
        "id": entry_id,
        "version": version,
        "known_from": KNOWN,
        "recorded_at": KNOWN,
        "origin": EntryOrigin.MANUAL,
        "review_status": ReviewStatus.APPROVED,
        **values,
    }


def intent(
    entry_id: str = "syn_bug",
    *,
    risk: RiskLevel = RiskLevel.NORMAL,
    auto_send: bool = False,
    required: list[str] | None = None,
    version: int = 1,
    **values: Any,
) -> Intent:
    return Intent(
        **_common(entry_id, version, **values),
        category=InquiryCategory.BUG,
        title_zh=f"【合成】{entry_id}",
        description_zh="【合成】意图说明",
        default_risk=risk,
        auto_send_eligible=auto_send,
        required_info_zh=required or [],
    )


def template(
    entry_id: str = "tpl-syn-generic",
    *,
    intent_id: str = "syn_bug",
    body: str = "【合成データ】ご連絡いただきありがとうございます。",
    slots: list[str] | None = None,
    risk: RiskLevel = RiskLevel.NORMAL,
    auto_send: bool = False,
    status: ReviewStatus = ReviewStatus.APPROVED,
    version: int = 1,
    **values: Any,
) -> ReplyTemplate:
    return ReplyTemplate(
        **{**_common(entry_id, version, **values), "review_status": status},
        intent=intent_id,
        category=InquiryCategory.BUG,
        title_zh=f"【合成】{entry_id}",
        description_zh="【合成】模板说明",
        use_when_zh="【合成】",
        avoid_when_zh="【合成】",
        body_ja=body,
        slots=[TemplateSlot(name=name, description_zh="【合成】") for name in slots or []],
        risk=risk,
        auto_send_allowed=auto_send,
    )


def notice(
    entry_id: str = "syn-incident",
    *,
    start: datetime,
    end: datetime | None = None,
    template_ids: list[str] | None = None,
    intents: list[str] | None = None,
    version: int = 1,
    **values: Any,
) -> Notice:
    return Notice(
        **_common(entry_id, version, **values),
        kind=NoticeKind.INCIDENT,
        title_zh="【合成】事故",
        description_zh="【合成】事故说明",
        event_start=start,
        event_end=end,
        template_ids=template_ids or [],
        intents=intents or [],
    )


def fact(entry_id: str = "syn-fact", statement: str = "【合成】数据迁移需要先绑定账号。") -> Fact:
    return Fact(**_common(entry_id, 1), statement_zh=statement)


def term(entry_id: str = "syn-term", ja: str = "ジェム", zh: str = "宝石") -> GlossaryEntry:
    return GlossaryEntry(**_common(entry_id, 1), term_ja=ja, term_zh=zh)


def forbidden(entry_id: str = "syn-no-refund", *phrases: str) -> Policy:
    return Policy(
        **_common(entry_id, 1),
        kind=PolicyKind.FORBIDDEN_PROMISE,
        title_zh="【合成】不承诺退款",
        description_zh="【合成】",
        phrases_ja=list(phrases) or ["返金いたします"],
    )


def trigger(
    entry_id: str = "syn-legal", *phrases: str, tags: list[SensitivityTag] | None = None
) -> Policy:
    return Policy(
        **_common(entry_id, 1),
        kind=PolicyKind.ESCALATION_TRIGGER,
        title_zh="【合成】法律威胁",
        description_zh="【合成】",
        phrases_ja=list(phrases) or ["弁護士"],
        sensitivity_tags=[SensitivityTag.LEGAL] if tags is None else tags,
    )


def known(at: datetime) -> TimePoint:
    return TimePoint(status=TimeStatus.KNOWN, value=at, basis_zh="合成")


UNAVAILABLE = TimePoint(status=TimeStatus.UNAVAILABLE)


def context(now: datetime, problem: TimePoint | datetime | None = None) -> TimeContext:
    """“现在”已知；问题发生时间可以是 `TimePoint`、确切时间或没有。"""
    problem_point = known(problem) if isinstance(problem, datetime) else problem or UNAVAILABLE
    return TimeContext(now=known(now), problem_time=problem_point)
