"""带状态的时间（TimePoint、TimeContext）的校验规则。数据全部是合成的。"""

from datetime import datetime, time
from typing import Any

import pytest
from pydantic import ValidationError

from copilot.contracts import TimeContext, TimePoint, TimeStatus
from copilot.ingest.timestamps import JST

T = datetime(2099, 1, 2, 12, 30, tzinfo=JST)
EARLY = datetime(2099, 1, 1, 0, 0, tzinfo=JST)
LATE = datetime(2099, 1, 3, 0, 0, tzinfo=JST)


def test_known_time_has_exact_value() -> None:
    point = TimePoint(status=TimeStatus.KNOWN, value=T, basis_zh="表单")
    assert point.bounds() == (T, T)


def test_estimated_time_has_interval() -> None:
    point = TimePoint(status=TimeStatus.ESTIMATED, value=T, earliest=EARLY, latest=LATE)
    assert point.bounds() == (EARLY, LATE)


def test_time_of_day_with_date_range() -> None:
    """表单中只有 `HH:MM`：时刻已知，日期是区间。"""
    point = TimePoint(
        status=TimeStatus.ESTIMATED,
        earliest=EARLY,
        latest=LATE,
        time_of_day=time(12, 30, tzinfo=JST),
        basis_zh="表单（只有时刻）",
    )
    assert point.value is None
    assert point.bounds() == (EARLY, LATE)


def test_unavailable_time_has_no_value() -> None:
    point = TimePoint(status=TimeStatus.UNAVAILABLE)
    assert point.bounds() is None


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"status": "unavailable", "value": T}, "不能有值"),
        ({"status": "unavailable", "earliest": EARLY, "latest": LATE}, "不能有值"),
        ({"status": "known"}, "必须有 value"),
        ({"status": "known", "value": T, "earliest": EARLY, "latest": LATE}, "不能有区间"),
        ({"status": "known", "value": T, "time_of_day": time(1, tzinfo=JST)}, "time_of_day"),
        ({"status": "estimated", "value": T}, "earliest 和 latest"),
        ({"status": "estimated", "earliest": LATE, "latest": EARLY}, "不能晚于"),
        ({"status": "estimated", "value": LATE, "earliest": EARLY, "latest": T}, "之间"),
        (
            {"status": "estimated", "earliest": EARLY, "latest": LATE, "time_of_day": time(1)},
            "时区",
        ),
    ],
    ids=[
        "unavailable-with-value",
        "unavailable-with-interval",
        "known-without-value",
        "known-with-interval",
        "known-with-time-of-day",
        "estimated-without-interval",
        "estimated-reversed",
        "estimated-value-outside",
        "naive-time-of-day",
    ],
)
def test_invalid_time_points_are_rejected(fields: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        TimePoint.model_validate(fields)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        TimePoint.model_validate({"status": "known", "value": "2099-01-02T12:30:00"})


def test_time_context_roundtrip() -> None:
    context = TimeContext(
        now=TimePoint(status=TimeStatus.ESTIMATED, earliest=EARLY, latest=LATE),
        problem_time=TimePoint(status=TimeStatus.UNAVAILABLE),
    )
    assert TimeContext.model_validate_json(context.model_dump_json()) == context


def test_now_cannot_be_unavailable() -> None:
    with pytest.raises(ValidationError, match="现在"):
        TimeContext(
            now=TimePoint(status=TimeStatus.UNAVAILABLE),
            problem_time=TimePoint(status=TimeStatus.KNOWN, value=T),
        )
