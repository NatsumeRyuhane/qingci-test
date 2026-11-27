"""时间上下文与时间窗口匹配测试（#6，提案 0003 §9）。

SYNTHETIC：全部使用合成时间与合成案件数据，不来自客户样本（D-002）。
"""

from datetime import datetime, time, timedelta

import pytest

from copilot.contracts import (
    EntryOrigin,
    InquiryForm,
    Notice,
    NoticeKind,
    Platform,
    ReplyTemplate,
    ReviewStatus,
    TimeContext,
    TimePoint,
    TimeStatus,
)
from copilot.draft.timectx import (
    WindowMatch,
    build_now,
    build_time_context,
    candidate_datetimes,
    known_at_for,
    match_notice,
    match_template_validity,
    match_window,
    parse_problem_time,
)
from copilot.ingest.timestamps import JST
from tests.cases import case as make_case


def _dt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=JST)


def _notice(
    *,
    event_start: datetime,
    event_end: datetime | None = None,
    platforms: list[Platform] | None = None,
    app_versions: list[str] | None = None,
) -> Notice:
    return Notice(
        id="syn-incident-1",
        version=1,
        known_from=_dt(2099, 1, 1, 0),
        recorded_at=_dt(2099, 1, 1, 0),
        origin=EntryOrigin.MANUAL,
        review_status=ReviewStatus.APPROVED,
        kind=NoticeKind.INCIDENT,
        title_zh="【合成】测试事故",
        description_zh="【合成】测试事故说明",
        event_start=event_start,
        event_end=event_end,
        platforms=platforms or [],
        app_versions=app_versions or [],
    )


def _template(
    *,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> ReplyTemplate:
    return ReplyTemplate(
        id="syn-tpl-1",
        version=1,
        valid_from=valid_from,
        valid_to=valid_to,
        known_from=_dt(2099, 1, 1, 0),
        recorded_at=_dt(2099, 1, 1, 0),
        origin=EntryOrigin.MANUAL,
        review_status=ReviewStatus.APPROVED,
        intent="bug_investigating",
        category="bug",  # type: ignore[arg-type]
        title_zh="【合成】测试模板",
        description_zh="【合成】说明",
        use_when_zh="【合成】适用",
        avoid_when_zh="【合成】不适用",
        body_ja="【合成データ】確認いたしました。",
    )


# --- build_now & known_at_for ---


def test_build_now_variants() -> None:
    auto = build_now()
    assert auto.status is TimeStatus.KNOWN
    assert auto.value is not None and auto.value.tzinfo is not None

    explicit = build_now(_dt(2099, 3, 5, 15, 0))
    assert explicit.status is TimeStatus.KNOWN
    assert explicit.value == _dt(2099, 3, 5, 15, 0)

    estimated = TimePoint(
        status=TimeStatus.ESTIMATED,
        earliest=_dt(2099, 3, 5, 10, 0),
        latest=_dt(2099, 3, 5, 18, 0),
    )
    assert build_now(estimated) == estimated

    with pytest.raises(ValueError, match="时区"):
        build_now(datetime(2099, 3, 5, 15, 0))
    with pytest.raises(ValueError, match="unavailable"):
        build_now(TimePoint(status=TimeStatus.UNAVAILABLE))


def test_known_at_for_production_vs_replay() -> None:
    now_known = build_now(_dt(2099, 3, 5, 15, 0))
    assert known_at_for(now_known, replay=False) is None
    assert known_at_for(now_known, replay=True) == _dt(2099, 3, 5, 15, 0)

    now_est = TimePoint(
        status=TimeStatus.ESTIMATED,
        earliest=_dt(2099, 3, 5, 10, 0),
        latest=_dt(2099, 3, 5, 18, 0),
    )
    assert known_at_for(now_est, replay=True) == _dt(2099, 3, 5, 10, 0)

    now_est_val = TimePoint(
        status=TimeStatus.ESTIMATED,
        value=_dt(2099, 3, 5, 12, 0),
        earliest=_dt(2099, 3, 5, 10, 0),
        latest=_dt(2099, 3, 5, 18, 0),
    )
    assert known_at_for(now_est_val, replay=True) == _dt(2099, 3, 5, 12, 0)


# --- parse_problem_time & build_time_context ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2099-03-02 12:30", _dt(2099, 3, 2, 12, 30)),
        ("2099/3/2 12:30", _dt(2099, 3, 2, 12, 30)),
        ("２０９９-０３-０２　１２：３０", _dt(2099, 3, 2, 12, 30)),
        ("2099-03-02T12:30:00+09:00", _dt(2099, 3, 2, 12, 30)),
    ],
)
def test_parse_problem_time_full_datetime_is_known(raw: str, expected: datetime) -> None:
    now = build_now(_dt(2099, 3, 5, 15, 0))
    pt = parse_problem_time(raw, now)
    assert pt.status is TimeStatus.KNOWN
    assert pt.value == expected


def test_parse_problem_time_future_datetime_becomes_unavailable() -> None:
    now = build_now(_dt(2099, 3, 5, 15, 0))
    pt = parse_problem_time("2099-03-06 10:00", now)
    assert pt.status is TimeStatus.UNAVAILABLE
    assert pt.basis_zh and "晚于" in pt.basis_zh


def test_parse_problem_time_only_hhmm_becomes_estimated_with_lookback() -> None:
    now = build_now(_dt(2099, 3, 5, 15, 0))
    pt = parse_problem_time("12:30", now, lookback_days=7)
    assert pt.status is TimeStatus.ESTIMATED
    assert pt.time_of_day == time(12, 30, tzinfo=JST)
    assert pt.earliest == _dt(2099, 2, 26, 15, 0)
    assert pt.latest == _dt(2099, 3, 5, 15, 0)

    cands = candidate_datetimes(pt)
    assert len(cands) == 7
    assert cands[0] == _dt(2099, 2, 27, 12, 30)
    assert cands[-1] == _dt(2099, 3, 5, 12, 30)


@pytest.mark.parametrize("raw", [None, "", "   ", "昨日ごろ", "2099-02-30 12:00", "25:99"])
def test_parse_problem_time_empty_or_invalid_is_unavailable(raw: str | None) -> None:
    now = build_now(_dt(2099, 3, 5, 15, 0))
    pt = parse_problem_time(raw, now)
    assert pt.status is TimeStatus.UNAVAILABLE


def test_build_time_context_without_form() -> None:
    c = make_case("SYN-1001")  # 无表单
    ctx = build_time_context(c, now=_dt(2099, 3, 5, 15, 0))
    assert ctx.now.value == _dt(2099, 3, 5, 15, 0)
    assert ctx.problem_time.status is TimeStatus.UNAVAILABLE


# --- match_window（提案 0003 §9 的所有形态）---


def test_match_window_known_half_open_bounds() -> None:
    start, end = _dt(2099, 3, 2, 11, 0), _dt(2099, 3, 2, 14, 0)
    assert match_window(build_now(_dt(2099, 3, 2, 11, 0)), start, end) is WindowMatch.INSIDE
    assert match_window(build_now(_dt(2099, 3, 2, 13, 59)), start, end) is WindowMatch.INSIDE
    assert match_window(build_now(_dt(2099, 3, 2, 14, 0)), start, end) is WindowMatch.OUTSIDE
    assert match_window(build_now(_dt(2099, 3, 2, 10, 59)), start, end) is WindowMatch.OUTSIDE


def test_match_window_time_of_day_excludes_non_overlapping_hours() -> None:
    """提案 0003 §9 示例：事故在 11:00–14:00，玩家填 20:00 → 无论哪一天都在窗口外；
    玩家填 12:30 → 无法确定（uncertain）。"""
    now = build_now(_dt(2099, 3, 5, 15, 0))
    start, end = _dt(2099, 3, 2, 11, 0), _dt(2099, 3, 2, 14, 0)

    pt_20 = parse_problem_time("20:00", now, lookback_days=7)
    assert match_window(pt_20, start, end) is WindowMatch.OUTSIDE

    pt_1230 = parse_problem_time("12:30", now, lookback_days=7)
    assert match_window(pt_1230, start, end) is WindowMatch.UNCERTAIN

    # 如果事件一直持续且早在回看窗口之前就开始，所有候选时刻都落在窗口内 → inside。
    long_start = _dt(2099, 2, 1, 0, 0)
    assert match_window(pt_1230, long_start, None) is WindowMatch.INSIDE


def test_match_window_continuous_estimated_interval() -> None:
    start, end = _dt(2099, 3, 2, 11, 0), _dt(2099, 3, 2, 14, 0)

    inside = TimePoint(
        status=TimeStatus.ESTIMATED,
        earliest=_dt(2099, 3, 2, 11, 30),
        latest=_dt(2099, 3, 2, 13, 30),
    )
    assert match_window(inside, start, end) is WindowMatch.INSIDE

    crossing = TimePoint(
        status=TimeStatus.ESTIMATED,
        earliest=_dt(2099, 3, 2, 13, 0),
        latest=_dt(2099, 3, 2, 15, 0),
    )
    assert match_window(crossing, start, end) is WindowMatch.UNCERTAIN

    outside = TimePoint(
        status=TimeStatus.ESTIMATED,
        earliest=_dt(2099, 3, 2, 14, 0),
        latest=_dt(2099, 3, 2, 16, 0),
    )
    assert match_window(outside, start, end) is WindowMatch.OUTSIDE


def test_match_window_unbounded_always_inside_even_unavailable() -> None:
    unavail = TimePoint(status=TimeStatus.UNAVAILABLE)
    assert match_window(unavail, None, None) is WindowMatch.INSIDE
    assert match_window(unavail, _dt(2099, 3, 2, 11, 0), None) is WindowMatch.UNCERTAIN


# --- match_notice & match_template_validity ---


def test_match_notice_checks_platform_and_app_version() -> None:
    notice = _notice(
        event_start=_dt(2099, 3, 2, 11, 0),
        event_end=_dt(2099, 3, 2, 14, 0),
        platforms=[Platform.IOS],
        app_versions=["1.4.0"],
    )
    ctx = TimeContext(
        now=build_now(_dt(2099, 3, 2, 15, 0)),
        problem_time=build_now(_dt(2099, 3, 2, 12, 0)),
    )
    assert match_notice(
        notice, ctx, form=InquiryForm(platform=Platform.IOS, app_version="1.4.0")
    ) is (WindowMatch.INSIDE)
    assert (
        match_notice(
            notice, ctx, form=InquiryForm(platform=Platform.GOOGLE_PLAY, app_version="1.4.0")
        )
        is WindowMatch.OUTSIDE
    )
    assert (
        match_notice(notice, ctx, form=InquiryForm(platform=Platform.IOS, app_version="1.5.0"))
        is WindowMatch.OUTSIDE
    )
    # 表单未提供平台/版本时，不因缺失而误判为 outside。
    assert match_notice(notice, ctx, form=InquiryForm()) is WindowMatch.INSIDE


def test_match_notice_unstarted_event_is_outside_even_when_problem_time_unavailable() -> None:
    notice = _notice(
        event_start=_dt(2099, 3, 15, 12, 0),
        event_end=_dt(2099, 3, 15, 13, 25),
    )
    ctx_before = TimeContext(
        now=build_now(_dt(2099, 3, 10, 12, 0)),
        problem_time=TimePoint(status=TimeStatus.UNAVAILABLE),
    )
    assert match_notice(notice, ctx_before) is WindowMatch.OUTSIDE

    ctx_after = TimeContext(
        now=build_now(_dt(2099, 3, 16, 12, 0)),
        problem_time=TimePoint(status=TimeStatus.UNAVAILABLE),
    )
    assert match_notice(notice, ctx_after) is WindowMatch.UNCERTAIN


def test_match_template_validity_with_estimated_now() -> None:
    tpl = _template(
        valid_from=_dt(2099, 3, 2, 11, 0),
        valid_to=_dt(2099, 3, 10, 0, 0),
    )
    now_inside = build_now(_dt(2099, 3, 5, 12, 0))
    assert match_template_validity(tpl, now_inside) is WindowMatch.INSIDE

    now_crossing = TimePoint(
        status=TimeStatus.ESTIMATED,
        earliest=_dt(2099, 3, 9, 18, 0),
        latest=_dt(2099, 3, 10, 6, 0),
    )
    assert match_template_validity(tpl, now_crossing) is WindowMatch.UNCERTAIN

    now_after = build_now(_dt(2099, 3, 10, 0, 0) + timedelta(minutes=1))
    assert match_template_validity(tpl, now_after) is WindowMatch.OUTSIDE
