"""从引用引导行中恢复邮件的发送时间。

样本没有发送时间列。后续邮件引用前一封邮件时，邮件客户端会写一行引导行，例如
`2024年3月23日(土) 11:21 【ぽちゃガチョ！カスタマーサポート】 <>:`，其中的时间精确到分钟，
就是被引用邮件的发送时间。这里只采用能确定的时间：引导行要能确定发送方，引用内容要能确定是哪一封邮件；找不到、
无法确定或有矛盾时保持为空，不做估计。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from copilot.contracts import MessageRole
from copilot.ingest.text import (
    ADDRESS,
    GENERIC_LEADER,
    GENERIC_LEADER_TIME,
    GMAIL_LEADER,
    IPHONE_LEADER,
)

JST = timezone(timedelta(hours=9), "JST")
"""样本中的时间都是日本时间（docs/architecture.md：时间必须带时区）。"""

SUPPORT_SENDER = "サポート"
"""引导行中客服发件人名称的特征，例如 `【ぽちゃガチョ！カスタマーサポート】`。"""


@dataclass(frozen=True)
class QuotedHeader:
    sent_at: datetime
    sender_role: MessageRole | None
    """被引用邮件的发送方。引导行中有客服发件人名称时是客服，有其他发件人名称时是玩家；
    只有脱敏后的空地址、没有名称时无法判断，为 None。"""
    block: str
    """引导行之后、下一个引导行之前的引用文本，即被引用邮件本身的内容。用来确认是哪一封邮件。"""


_QUOTE_PREFIX = re.compile(r"[ \t\u3000>]*")


@dataclass(frozen=True)
class _Leader:
    depth: int
    """所在行开头的 `>` 个数，即引用层级。"""
    line_start: int
    line_end: int
    time: re.Match[str]
    sender: str | None
    """发件人名称（去掉地址），只有 Gmail/iPhone 引导行能取出。"""
    line: str


def _leaders(quoted: str) -> list[_Leader]:
    """所有能读出时间的引导行，按位置排列。同一行被多条规则匹配时只保留一条。"""
    found: dict[int, _Leader] = {}
    for pattern in (GMAIL_LEADER, IPHONE_LEADER, GENERIC_LEADER):
        for match in pattern.finditer(quoted):
            line_start = quoted.rfind("\n", 0, match.start()) + 1
            if line_start in found:
                continue
            time = match if pattern is not GENERIC_LEADER else GENERIC_LEADER_TIME.search(match[0])
            if time is None:
                continue
            line_end = quoted.find("\n", match.end() - 1)
            line_end = len(quoted) if line_end < 0 else line_end
            line = quoted[line_start:line_end]
            prefix = _QUOTE_PREFIX.match(line)
            sender = None
            if pattern is not GENERIC_LEADER:
                sender = re.sub(ADDRESS, "", match["sender"]).strip(" \t\u3000,、")
            found[line_start] = _Leader(
                depth=prefix[0].count(">") if prefix else 0,
                line_start=line_start,
                line_end=line_end,
                time=time,
                sender=sender,
                line=line,
            )
    return sorted(found.values(), key=lambda x: x.line_start)


def _sender_role(leader: _Leader) -> MessageRole | None:
    if SUPPORT_SENDER in leader.line:
        return MessageRole.AGENT
    if leader.sender:
        return MessageRole.PLAYER
    return None


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
    following = [x.line_start for x in leaders if x.line_start > leader.line_start]
    block = quoted[leader.line_end : following[0] if following else len(quoted)]
    return QuotedHeader(sent_at=sent_at, sender_role=_sender_role(leader), block=block)


SIGNATURE_CHARS = 40
"""用被引用邮件开头这么多个字符（规范化后）确认引用的是哪一封。"""

_IGNORED = re.compile(r"[\s>?・\u3000]")
"""比较时忽略的字符：空白、引用符号，以及字符修复前后可能不同的 `?` 和 `・`。"""


def _normalize(text: str) -> str:
    return _IGNORED.sub("", text)


@dataclass(frozen=True)
class _Msg:
    seq: int
    role: MessageRole
    quoted: str
    text: str
    """这封邮件自己的内容（客服回复去掉问候语和结尾），用来与引用内容比对。"""


def _identify(block: str, candidates: list[_Msg]) -> _Msg | None:
    """引用内容中包含哪一封候选邮件的开头。恰好一封时返回它，否则无法确定。"""
    quoted = _normalize(block)
    matches = []
    for message in candidates:
        signature = _normalize(message.text)[:SIGNATURE_CHARS]
        if signature and signature in quoted:
            matches.append(message)
    return matches[0] if len(matches) == 1 else None


def recover_sent_at(messages: list[_Msg]) -> tuple[dict[int, datetime], int]:
    """返回（{seq: 发送时间}, 矛盾次数）。

    第 k 封邮件的引导行给出它直接回复的那封邮件的发送时间。只有同时满足以下条件才采用：
    引导行能确定发送方；引导行下的引用内容恰好与之前一封该发送方的邮件吻合。两封邮件给
    同一封邮件推出不同的时间时，视为无法确定，不采用。
    """
    found: dict[int, set[datetime]] = {}
    for i, msg in enumerate(messages):
        header = outermost_header(msg.quoted) if msg.quoted else None
        if header is None or header.sender_role is None:
            continue
        candidates = [m for m in messages[:i] if m.role is header.sender_role]
        target = _identify(header.block, candidates)
        if target is not None:
            found.setdefault(target.seq, set()).add(header.sent_at)
    times = {seq: next(iter(ts)) for seq, ts in found.items() if len(ts) == 1}
    return times, sum(1 for ts in found.values() if len(ts) > 1)


def message_ref(seq: int, role: MessageRole, quoted: str, text: str) -> _Msg:
    return _Msg(seq=seq, role=role, quoted=quoted, text=text)
