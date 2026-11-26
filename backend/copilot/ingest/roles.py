"""判断每封邮件的发送方。样本中没有标明发送方的列（docs/data.md）。"""

import re
from enum import StrEnum

from copilot.contracts import MessageRole
from copilot.ingest.form import looks_like_form
from copilot.ingest.text import CLOSING_MARK, has_greeting

SYSTEM_PATTERN = re.compile(
    r"Mail Delivery|Undelivered Mail|Delivery Status Notification|MAILER-DAEMON"
    r"|This is the mail system|^[ \t]*The mail system[ \t]*$|Host or domain name not found"
    r"|The email account that you tried to reach does not exist|Reason:\s*\d\.\d\.\d"
    r"|メール一括返信作業のレポート|host \S+\[[\d.]+\]\s+said:|returned to sender",
    re.IGNORECASE | re.MULTILINE,
)
"""退信和客服工具批量回复报告的特征。"""


class RoleReason(StrEnum):
    """判断依据，按判断顺序排列。用于导入统计，便于核对规则。"""

    EMPTY = "empty"
    """原文为空 → unknown。"""
    GREETING = "greeting"
    """去除引用后开头有固定问候语 → agent。"""
    SYSTEM = "system"
    """退信或批量回复报告 → system。"""
    FORM = "form"
    """含游戏内表单字段 → player。"""
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
    if SYSTEM_PATTERN.search(body):
        return MessageRole.SYSTEM, RoleReason.SYSTEM
    if looks_like_form(body):
        return MessageRole.PLAYER, RoleReason.FORM
    if CLOSING_MARK in body:
        return MessageRole.AGENT, RoleReason.CLOSING_ONLY
    if not body.strip():
        return MessageRole.UNKNOWN, RoleReason.EMPTY_AFTER_QUOTE
    if seq == 1:
        return MessageRole.PLAYER, RoleReason.FIRST_MESSAGE
    return MessageRole.PLAYER, RoleReason.FOLLOW_UP
