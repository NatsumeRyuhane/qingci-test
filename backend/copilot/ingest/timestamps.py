"""从引用引导行中恢复邮件的发送时间。

样本没有发送时间列。后续邮件引用前一封邮件时，邮件客户端会写一行引导行，例如
`2024年3月23日(土) 11:21 【ぽちゃガチョ！カスタマーサポート】 <>:`，其中的时间精确到分钟，
就是被引用邮件的发送时间。这里只采用这类能确定的时间；找不到或有矛盾时保持为空，不做估计。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from copilot.contracts import MessageRole
from copilot.ingest.text import GENERIC_LEADER, GENERIC_LEADER_TIME, GMAIL_LEADER, IPHONE_LEADER

JST = timezone(timedelta(hours=9), "JST")
"""样本中的时间都是日本时间（docs/architecture.md：时间必须带时区）。"""

SUPPORT_SENDER = "サポート"
"""引导行中客服发件人名称的特征，例如 `【ぽちゃガチョ！カスタマーサポート】`。"""


@dataclass(frozen=True)
class QuotedHeader:
    sent_at: datetime
    sender_role: MessageRole
    """被引用邮件的发送方：客服或玩家。"""


_QUOTE_PREFIX = re.compile(r"[ \t\u3000>]*")


@dataclass(frozen=True)
class _Leader:
    depth: int
    """所在行开头的 `>` 个数，即引用层级。"""
    line_start: int
    time: re.Match[str]
    line: str


def _leaders(quoted: str) -> list[_Leader]:
    """所有能读出时间的引导行。同一行被多条规则匹配时只保留一条。"""
    found: dict[int, _Leader] = {}
    for pattern in (GMAIL_LEADER, IPHONE_LEADER, GENERIC_LEADER):
        for match in pattern.finditer(quoted):
            line_start = quoted.rfind("\n", 0, match.start()) + 1
            if line_start in found:
                continue
            time = match if pattern is not GENERIC_LEADER else GENERIC_LEADER_TIME.search(match[0])
            if time is None:
                continue
            line_end = quoted.find("\n", line_start)
            line = quoted[line_start : line_end if line_end >= 0 else len(quoted)]
            prefix = _QUOTE_PREFIX.match(line)
            depth = prefix[0].count(">") if prefix else 0
            found[line_start] = _Leader(depth=depth, line_start=line_start, time=time, line=line)
    return list(found.values())


def outermost_header(quoted: str) -> QuotedHeader | None:
    """引用文本中层级最浅的第一个引导行，即这封邮件直接回复的那封邮件。"""
    leaders = _leaders(quoted)
    if not leaders:
        return None
    leader = min(leaders, key=lambda x: (x.depth, x.line_start))
    match = leader.time
    hour = int(match["h"])
    ampm = match.groupdict().get("ampm")
    if ampm == "午後" and hour < 12:
        hour += 12
    elif ampm == "午前" and hour == 12:
        hour = 0
    try:
        sent_at = datetime(
            int(match["y"]), int(match["mo"]), int(match["d"]), hour, int(match["mi"]), tzinfo=JST
        )
    except ValueError:
        return None
    role = MessageRole.AGENT if SUPPORT_SENDER in leader.line else MessageRole.PLAYER
    return QuotedHeader(sent_at=sent_at, sender_role=role)


@dataclass(frozen=True)
class _Msg:
    seq: int
    role: MessageRole
    quoted: str


def recover_sent_at(messages: list[_Msg]) -> tuple[dict[int, datetime], int]:
    """返回（{seq: 发送时间}, 矛盾次数）。

    第 k 封邮件的引导行指向它之前、发送方相符的最近一封邮件。两封邮件给同一封邮件
    推出不同的时间时，视为无法确定，不采用。
    """
    found: dict[int, set[datetime]] = {}
    for i, msg in enumerate(messages):
        header = outermost_header(msg.quoted) if msg.quoted else None
        if header is None:
            continue
        target = next((m for m in reversed(messages[:i]) if m.role is header.sender_role), None)
        if target is not None:
            found.setdefault(target.seq, set()).add(header.sent_at)
    times = {seq: next(iter(ts)) for seq, ts in found.items() if len(ts) == 1}
    return times, sum(1 for ts in found.values() if len(ts) > 1)


def message_ref(seq: int, role: MessageRole, quoted: str) -> _Msg:
    return _Msg(seq=seq, role=role, quoted=quoted)
