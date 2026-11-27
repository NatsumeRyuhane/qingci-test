"""起草时的时间上下文与时间窗口匹配（#6，提案 0003 §9）。

- “现在”（`now`）：生产中为系统时间（`known`）；历史重放时为指定或估计的时间点。
- 问题发生时间（`problem_time`）：从 `InquiryForm.occurred_at` 解析。
  - 完整日期和时间 → `known`（晚于“现在”时视为无效，返回 `unavailable`）；
  - 只有 `HH:MM` → `estimated`，保留 `time_of_day`，日期区间为 `[now - lookback_days, now]`；
  - 空、无表单或无法解析 → `unavailable`。没有就是没有，不用常识猜测填补。
- 窗口判断返回 `inside` / `outside` / `uncertain`：区间跨过窗口边界或时间缺失（对已开始事件）
  时返回 `uncertain`，由下游转人工审核。
"""

import re
import unicodedata
from datetime import datetime, time, timedelta
from enum import StrEnum

from copilot.contracts import (
    Case,
    InquiryForm,
    Notice,
    ReplyTemplate,
    TimeContext,
    TimePoint,
    TimeStatus,
)
from copilot.ingest.timestamps import JST

DEFAULT_LOOKBACK_DAYS = 7
"""表单问题发生时间只有时刻（HH:MM）时，默认向前估计的回看天数。"""

_FULL_DATETIME_RE = re.compile(
    r"^(?P<y>\d{4})[-/](?P<mo>\d{1,2})[-/](?P<d>\d{1,2})"
    r"[ T]+(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?$"
)
_TIME_ONLY_RE = re.compile(r"^(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?$")


class WindowMatch(StrEnum):
    """一个时间点与半开窗口 `[start, end)` 的匹配结果。"""

    INSIDE = "inside"
    """确定落在窗口内。"""
    OUTSIDE = "outside"
    """确定落在窗口外（或公告的平台、版本条件不符，或事件尚未开始）。"""
    UNCERTAIN = "uncertain"
    """无法确定：估计区间跨过边界，或问题发生时间缺失但事件已开始。"""


def build_now(now: TimePoint | datetime | None = None) -> TimePoint:
    """构造“现在”的 `TimePoint`。不接受 `unavailable` 或不带时区的 `datetime`。"""
    if now is None:
        return TimePoint(
            status=TimeStatus.KNOWN,
            value=datetime.now(JST).replace(microsecond=0),
            basis_zh="系统时间",
        )
    if isinstance(now, TimePoint):
        if now.status is TimeStatus.UNAVAILABLE:
            raise ValueError("“现在”不能是 unavailable")
        return now
    if now.tzinfo is None:
        raise ValueError("“现在”必须带时区")
    return TimePoint(status=TimeStatus.KNOWN, value=now, basis_zh="指定时间")


def known_at_for(now: TimePoint, *, replay: bool) -> datetime | None:
    """历史重放时用于过滤 `KnowledgeEntry.known_from` 的截止时间；生产模式返回 None。

    `now` 为 `estimated` 且没有 `value` 时取区间下界，避免泄露尚未存在的知识版本。
    """
    if not replay:
        return None
    if now.value is not None:
        return now.value
    bounds = now.bounds()
    if bounds is None:
        raise ValueError("“现在”不能是 unavailable")
    return bounds[0]


def _parse_iso_datetime(text: str) -> datetime | None:
    if "T" not in text and "t" not in text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=JST)


def parse_problem_time(
    occurred_at: str | None,
    now: TimePoint,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> TimePoint:
    """把表单中的 `occurred_at` 原文解析成带状态的 `TimePoint`（提案 0003 §9）。"""
    if lookback_days < 1:
        raise ValueError(f"lookback_days 必须至少为 1，实际为 {lookback_days}")
    now_bounds = now.bounds()
    if now_bounds is None:
        raise ValueError("“现在”不能是 unavailable")
    now_earliest, now_latest = now_bounds

    if occurred_at is None or not occurred_at.strip():
        return TimePoint(status=TimeStatus.UNAVAILABLE, basis_zh="表单未填写")

    text = unicodedata.normalize("NFKC", occurred_at).strip()

    full_match = _FULL_DATETIME_RE.fullmatch(text)
    if full_match is not None:
        try:
            parsed = datetime(
                int(full_match["y"]),
                int(full_match["mo"]),
                int(full_match["d"]),
                int(full_match["h"]),
                int(full_match["mi"]),
                int(full_match["s"] or 0),
                tzinfo=JST,
            )
        except ValueError:
            return TimePoint(status=TimeStatus.UNAVAILABLE, basis_zh="表单时间格式无法解析")
        if parsed > now_latest:
            return TimePoint(status=TimeStatus.UNAVAILABLE, basis_zh="表单时间晚于当前时间")
        return TimePoint(status=TimeStatus.KNOWN, value=parsed, basis_zh="表单")

    iso_parsed = _parse_iso_datetime(text)
    if iso_parsed is not None:
        if iso_parsed > now_latest:
            return TimePoint(status=TimeStatus.UNAVAILABLE, basis_zh="表单时间晚于当前时间")
        return TimePoint(status=TimeStatus.KNOWN, value=iso_parsed, basis_zh="表单")

    time_match = _TIME_ONLY_RE.fullmatch(text)
    if time_match is not None:
        try:
            tod = time(
                int(time_match["h"]),
                int(time_match["mi"]),
                int(time_match["s"] or 0),
                tzinfo=JST,
            )
        except ValueError:
            return TimePoint(status=TimeStatus.UNAVAILABLE, basis_zh="表单时间格式无法解析")
        earliest = now_earliest.astimezone(JST) - timedelta(days=lookback_days)
        latest = now_latest.astimezone(JST)
        return TimePoint(
            status=TimeStatus.ESTIMATED,
            earliest=earliest,
            latest=latest,
            time_of_day=tod,
            basis_zh=f"表单（仅时刻，回看 {lookback_days} 天）",
        )

    return TimePoint(status=TimeStatus.UNAVAILABLE, basis_zh="表单时间格式无法解析")


def build_time_context(
    case: Case,
    *,
    now: TimePoint | datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> TimeContext:
    """为案件构造完整的 `TimeContext`（“现在”和问题发生时间）。"""
    now_point = build_now(now)
    occurred_at = case.form.occurred_at if case.form is not None else None
    problem_time = parse_problem_time(occurred_at, now_point, lookback_days=lookback_days)
    return TimeContext(now=now_point, problem_time=problem_time)


def candidate_datetimes(point: TimePoint) -> list[datetime]:
    """`point` 带 `time_of_day` 时，在 `[earliest, latest]` 内逐日枚举所有候选时刻。"""
    if (
        point.status is not TimeStatus.ESTIMATED
        or point.time_of_day is None
        or point.earliest is None
        or point.latest is None
    ):
        return []
    tz = point.time_of_day.tzinfo
    if tz is None:
        return []
    earliest_tz = point.earliest.astimezone(tz)
    latest_tz = point.latest.astimezone(tz)
    candidates: list[datetime] = []
    day = earliest_tz.date()
    end_day = latest_tz.date()
    while day <= end_day:
        cand = datetime.combine(day, point.time_of_day)
        if point.earliest <= cand <= point.latest:
            candidates.append(cand)
        day += timedelta(days=1)
    return candidates


def _in_half_open(at: datetime, start: datetime | None, end: datetime | None) -> bool:
    if start is not None and at < start:
        return False
    return end is None or at < end


def match_window(point: TimePoint, start: datetime | None, end: datetime | None) -> WindowMatch:
    """判断时间点 `point` 是否落在半开窗口 `[start, end)` 内。"""
    if start is not None and end is not None and start >= end:
        raise ValueError("窗口起点必须早于终点")
    if start is None and end is None:
        return WindowMatch.INSIDE

    if point.status is TimeStatus.UNAVAILABLE:
        return WindowMatch.UNCERTAIN

    if point.status is TimeStatus.KNOWN:
        assert point.value is not None
        return WindowMatch.INSIDE if _in_half_open(point.value, start, end) else WindowMatch.OUTSIDE

    assert point.earliest is not None and point.latest is not None
    if point.time_of_day is not None:
        candidates = candidate_datetimes(point)
        if candidates:
            hits = [_in_half_open(c, start, end) for c in candidates]
            if all(hits):
                return WindowMatch.INSIDE
            if not any(hits):
                return WindowMatch.OUTSIDE
            return WindowMatch.UNCERTAIN

    # 连续区间 [earliest, latest] 与半开窗口 [start, end) 比较。
    if (start is None or point.earliest >= start) and (end is None or point.latest < end):
        return WindowMatch.INSIDE
    if (end is not None and point.earliest >= end) or (start is not None and point.latest < start):
        return WindowMatch.OUTSIDE
    return WindowMatch.UNCERTAIN


def match_template_validity(template: ReplyTemplate, now: TimePoint) -> WindowMatch:
    """判断模板在“现在”是否处于有效期内。"""
    return match_window(now, template.valid_from, template.valid_to)


def match_notice(
    notice: Notice, time_ctx: TimeContext, *, form: InquiryForm | None = None
) -> WindowMatch:
    """判断公告/事件的事件时间及受影响范围是否与案件匹配（提案 0003 §9）。

    - 公告限定了平台或 App 版本且与表单明确不符 → `outside`；
    - 事件在“现在”尚未开始 → `outside`；
    - 问题发生时间缺失但事件已开始 → `uncertain`；
    - 其余按 `match_window(problem_time, event_start, event_end)` 判断。
    """
    if form is not None:
        if notice.platforms and form.platform is not None and form.platform not in notice.platforms:
            return WindowMatch.OUTSIDE
        version = form.app_version.strip() if form.app_version else ""
        if notice.app_versions and version and version not in notice.app_versions:
            return WindowMatch.OUTSIDE

    now_bounds = time_ctx.now.bounds()
    assert now_bounds is not None
    _, now_latest = now_bounds
    if notice.event_start > now_latest:
        return WindowMatch.OUTSIDE

    return match_window(time_ctx.problem_time, notice.event_start, notice.event_end)
