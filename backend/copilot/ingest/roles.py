"""判断每封邮件的发送方。样本中没有标明发送方的列（docs/data.md）。"""

import re
from enum import StrEnum

from copilot.contracts import MessageRole
from copilot.ingest.form import first_field_line, looks_like_form
from copilot.ingest.text import CLOSING_MARK, has_greeting

_F = re.IGNORECASE | re.MULTILINE

BOUNCE_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Mail Delivery (?:Subsystem|System)", _F),
    re.compile(r"Undelivered Mail", _F),
    re.compile(r"Delivery Status Notification", _F),
    re.compile(r"MAILER-DAEMON", _F),
    re.compile(r"This is the mail system", _F),
    re.compile(r"^[ \t]*The mail system[ \t]*$", _F),
    re.compile(r"Host or domain name not found", _F),
    re.compile(r"The email account that you tried to reach does not exist", _F),
    re.compile(r"Reason:\s*\d\.\d\.\d", _F),
    re.compile(r"host \S+\[[\d.]+\]\s+said:", _F),
    re.compile(r"returned to sender", _F),
    re.compile(r"\*\* Address not found \*\*", _F),
)
"""退信中常见的特征。真实退信至少同时出现两种（样本中 73 封全部如此）。"""

HEADER_LINES: tuple[re.Pattern[str], ...] = (
    re.compile(r"メールボックス「[^」\n]*」のメール一括返信作業のレポートです。"),
    re.compile(r"This is the mail system at host \S+", re.IGNORECASE),
    re.compile(r"\*\* Address not found \*\*"),
)
"""系统邮件固定的第一行，必须整行匹配，例如客服工具批量回复报告的开头。"""

SYSTEM_SEARCH_LINES = 15
"""只在正文开头这么多行内找系统邮件的特征。退信和报告的特征都在开头（样本中最多到第 15 行）。"""


class SystemEvidence(StrEnum):
    STRONG = "strong"
    """第一行是固定的系统邮件开头，或同时有至少两种退信特征。"""
    WEAK = "weak"
    """只有一种退信特征，可能是玩家在正文里提到了通知。"""
    NONE = "none"


def system_evidence(body: str) -> SystemEvidence:
    """只看正文开头、并且在第一个表单字段之前的部分。

    退信会附上原邮件（常常是表单），但退信说明总在前面；玩家在咨询内容里提到
    `Mail Delivery Subsystem` 之类的文字时，它出现在表单字段之后，不算。
    """
    lines = body.split("\n")
    limit = SYSTEM_SEARCH_LINES
    field = first_field_line(body)
    if field is not None:
        limit = min(limit, field)
    preamble = "\n".join(lines[:limit])
    first = next((line.strip() for line in lines[:limit] if line.strip()), "")
    if any(pattern.fullmatch(first) for pattern in HEADER_LINES):
        return SystemEvidence.STRONG
    hits = sum(1 for pattern in BOUNCE_MARKERS if pattern.search(preamble))
    if hits >= 2:
        return SystemEvidence.STRONG
    return SystemEvidence.WEAK if hits == 1 else SystemEvidence.NONE


class RoleReason(StrEnum):
    """判断依据，按判断顺序排列。用于导入统计，便于核对规则。"""

    EMPTY = "empty"
    """原文为空 → unknown。"""
    GREETING = "greeting"
    """去除引用后开头有固定问候语 → agent。"""
    SYSTEM = "system"
    """退信或批量回复报告（有明确的系统邮件结构）→ system。"""
    FORM = "form"
    """含游戏内表单字段 → player。"""
    AMBIGUOUS_NOTIFICATION = "ambiguous_notification"
    """不是表单，开头只有一种退信特征：可能是退信，也可能是玩家提到了通知 → unknown。"""
    CLOSING_ONLY = "closing_only"
    """没有问候语但有固定结语 → agent。"""
    EMPTY_AFTER_QUOTE = "empty_after_quote"
    """去除引用后什么都不剩，例如纯转发 → unknown。"""
    FIRST_MESSAGE = "first_message"
    """线程的第 1 封，不是表单 → player（第 1 封是玩家的咨询）。"""
    FOLLOW_UP = "follow_up"
    """第 2 封起，没有客服的问候语和结语 → player（玩家追问）。"""


def detect_role(seq: int, raw_body: str, body: str) -> tuple[MessageRole, RoleReason]:
    """`body` 是去除引用并清洗后的正文。"""
    if not raw_body.strip():
        return MessageRole.UNKNOWN, RoleReason.EMPTY
    if has_greeting(body):
        return MessageRole.AGENT, RoleReason.GREETING
    evidence = system_evidence(body)
    if evidence is SystemEvidence.STRONG:
        return MessageRole.SYSTEM, RoleReason.SYSTEM
    if looks_like_form(body):
        return MessageRole.PLAYER, RoleReason.FORM
    if evidence is SystemEvidence.WEAK:
        return MessageRole.UNKNOWN, RoleReason.AMBIGUOUS_NOTIFICATION
    if CLOSING_MARK in body:
        return MessageRole.AGENT, RoleReason.CLOSING_ONLY
    if not body.strip():
        return MessageRole.UNKNOWN, RoleReason.EMPTY_AFTER_QUOTE
    if seq == 1:
        return MessageRole.PLAYER, RoleReason.FIRST_MESSAGE
    return MessageRole.PLAYER, RoleReason.FOLLOW_UP
