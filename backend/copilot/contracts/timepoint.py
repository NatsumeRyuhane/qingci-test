"""带状态的时间（提案 0003 §9）。

起草和评测都要区分“已知”“估计”“没有”三种时间，不能把猜测当作事实：估计值必须带区间，
没有就是没有。时间窗口（事故、模板有效期）的判断由代码根据这里的区间进行。
"""

from datetime import datetime, time
from typing import Self

from pydantic import AwareDatetime, model_validator

from copilot.contracts.base import Contract
from copilot.contracts.enums import TimeStatus


class TimePoint(Contract):
    """一个带状态的时间点。

    - `known`：`value` 是确切值，没有区间。
    - `estimated`：`earliest` 和 `latest` 给出区间，`value` 是区间内最可能的值（可选）。
      只知道时刻、不知道日期时（表单中只有 `HH:MM`），`time_of_day` 是已知的时刻，
      区间表示日期的范围。
    - `unavailable`：所有值都为空。
    """

    status: TimeStatus
    """可信程度。"""
    value: AwareDatetime | None = None
    """确切值（`known`），或区间内最可能的值（`estimated`，可选）。"""
    earliest: AwareDatetime | None = None
    """估计区间的下界，`estimated` 时必填。"""
    latest: AwareDatetime | None = None
    """估计区间的上界，`estimated` 时必填。"""
    time_of_day: time | None = None
    """已知的时刻（必须带时区），只用于“时刻已知、日期是区间”的估计。"""
    basis_zh: str | None = None
    """依据（中文），例如“表单”“系统时间”“线程 ID 相邻的已知发送时间”。"""

    @model_validator(mode="after")
    def _check_status(self) -> Self:
        given = {
            name
            for name in ("value", "earliest", "latest", "time_of_day")
            if getattr(self, name) is not None
        }
        if self.status is TimeStatus.UNAVAILABLE and given:
            raise ValueError(f"unavailable 的时间不能有值：{sorted(given)}")
        if self.status is TimeStatus.KNOWN and given != {"value"}:
            raise ValueError("known 的时间必须有 value，并且不能有区间或 time_of_day")
        if self.status is TimeStatus.ESTIMATED:
            if self.earliest is None or self.latest is None:
                raise ValueError("estimated 的时间必须有 earliest 和 latest")
            if self.earliest > self.latest:
                raise ValueError("earliest 不能晚于 latest")
            if self.value is not None and not self.earliest <= self.value <= self.latest:
                raise ValueError("value 必须在 earliest 和 latest 之间")
        if self.time_of_day is not None and self.time_of_day.tzinfo is None:
            raise ValueError("time_of_day 必须带时区")
        return self

    def bounds(self) -> tuple[datetime, datetime] | None:
        """时间可能落在的区间（闭区间）。`known` 时上下界相同，`unavailable` 时为 None。

        不考虑 `time_of_day`：用已知时刻进一步排除的判断由使用方（#6 的窗口检查）进行。
        """
        if self.status is TimeStatus.KNOWN and self.value is not None:
            return self.value, self.value
        if self.earliest is not None and self.latest is not None:
            return self.earliest, self.latest
        return None


class TimeContext(Contract):
    """起草时的时间上下文：“现在”和问题发生时间（提案 0003 §9）。

    记录在草稿上，使草稿可以复现。评测中“现在”模拟到分钟。
    """

    now: TimePoint
    """起草的时间。生产中是系统时间（`known`）；评测中取历史回复的发送时间，没有时估计。
    不能是 `unavailable`。"""
    problem_time: TimePoint
    """问题发生时间，来源：`InquiryForm.occurred_at`（玩家填写，不可信）。"""

    @model_validator(mode="after")
    def _check_now(self) -> Self:
        if self.now.status is TimeStatus.UNAVAILABLE:
            raise ValueError("“现在”不能是 unavailable")
        return self
