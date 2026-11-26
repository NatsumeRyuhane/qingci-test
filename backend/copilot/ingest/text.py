"""正文清洗：去除引用、修复丢失的字符、规范化空白、拆出客服回复的固定问候语和结尾。

规则来自对样本的观察（docs/data.md），拿不准时不改。原文始终保留在 `Message.raw_body`。
"""

import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

GREETING_MARK = "サポート担当です"
"""客服回复固定问候语的第二句，例如 `『ぽちゃガチョ！』サポート担当です。`。"""
CLOSING_MARK = "今後とも『ぽちゃガチョ！』をよろしくお願いいたします"
"""客服回复固定结语。"""
NOTICE_MARK = "※本メール内容の無断掲載"
"""客服回复末尾的禁止转载声明。部分回复没有结语，只有它。"""

GREETING_SEARCH_CHARS = 300
"""只在正文开头这么多字符内找问候语。引用被去除后，问候语应当就在开头。"""


# --- 引用 ---------------------------------------------------------------------


class QuoteFormat(StrEnum):
    """识别出引用起点的规则。"""

    ANGLE = "angle"
    """以 `>` 开头的行（含嵌套的 `>>`）。"""
    GMAIL = "gmail"
    """Gmail 风格引导行：`2024年3月29日(金) 16:32 … <>:`。"""
    IPHONE = "iphone"
    """iPhone 风格引导行：`2024/03/29 12:18、… のメール:`。"""
    SEPARATOR = "separator"
    """`Original Message`、`転送メッセージ`、`____` 等分隔线。"""
    ENGLISH = "english"
    """英文引导行：`On … wrote:`。"""
    OTHER_LEADER = "other_leader"
    """其他客户端或语言的引导行：含 `<地址>` 或客服发件人名称、以冒号结尾的一行。"""
    HEADER_BLOCK = "header_block"
    """Outlook 等客户端的邮件头块：`From:`/`差出人:` 之后几行内有 `日付:`、`Subject:` 等。"""


GMAIL_LEADER = re.compile(
    r"(?P<y>\d{4})年(?P<mo>\d{1,2})月(?P<d>\d{1,2})日\s*[(（][^)）\n]{1,3}[)）]\s*"
    r"(?P<ampm>午前|午後)?\s*(?P<h>\d{1,2}):(?P<mi>\d{2})(?P<sender>[^\n]*?)<[^<>\n]*>\s*[:：]"
)
IPHONE_LEADER = re.compile(
    r"(?P<y>\d{4})/(?P<mo>\d{1,2})/(?P<d>\d{1,2})\s*(?P<h>\d{1,2}):(?P<mi>\d{2})、"
    r"(?P<sender>[^\n]*?)のメール\s*[:：]"
)

SUPPORT_SENDER_NAME = "【ぽちゃガチョ！カスタマーサポート】"
"""客服邮件的发件人名称，出现在玩家邮件客户端写的引导行里。"""

GENERIC_LEADER = re.compile(
    r"^[^\n]*<[^<>\n]*>[^\n]*[:：][ \t]*$"
    rf"|^[^\n]*{re.escape(SUPPORT_SENDER_NAME)}[^\n]*(?:\n[^\n]{{0,80}})?[:：][ \t]*$",
    re.MULTILINE,
)
"""其他客户端或语言的引导行：一行中有 `<地址>` 并以冒号结尾；或有客服发件人名称，并在本行或
下一行以冒号结尾（长引导行会被折行）。例如 `Pada Kam, 29 Feb 2024 pukul 13.56 【…】 <> menulis:`、
`2024/03/02 18:30:09 【…】:`。"""
GENERIC_LEADER_TIME = re.compile(
    r"(?P<y>\d{4})[/-](?P<mo>\d{1,2})[/-](?P<d>\d{1,2})\s+(?P<h>\d{1,2}):(?P<mi>\d{2})"
)
"""通用引导行中能确定的时间写法。其他写法（例如印尼语日期）不解析。"""

QUOTE_RULES: tuple[tuple[QuoteFormat, re.Pattern[str]], ...] = (
    (QuoteFormat.ANGLE, re.compile(r"^[ \t\u3000]*>", re.MULTILINE)),
    (QuoteFormat.GMAIL, GMAIL_LEADER),
    (QuoteFormat.IPHONE, IPHONE_LEADER),
    (
        QuoteFormat.SEPARATOR,
        re.compile(
            r"^[ \t]*(?:-{2,}\s*"
            r"(?:Original Message|元のメッセージ|転送メッセージ|Forwarded message)"
            r"|_{10,}[ \t]*$)",
            re.MULTILINE | re.IGNORECASE,
        ),
    ),
    (QuoteFormat.ENGLISH, re.compile(r"^On .{5,200} wrote:[ \t]*$", re.MULTILINE)),
    (QuoteFormat.OTHER_LEADER, GENERIC_LEADER),
    (
        QuoteFormat.HEADER_BLOCK,
        re.compile(
            r"^[ \t]*(?:From|差出人|送信者)[ \t]*[:：][^\n]*\n(?:[^\n]*\n){0,4}?"
            r"[ \t]*(?:Sent|Date|To|Subject|送信日時|日付|日時|宛先|件名)[ \t]*[:：]",
            re.MULTILINE | re.IGNORECASE,
        ),
    ),
)

_TRAILING_RULE = re.compile(r"(?:\n|^)[ \t]*[-_=]{4,}[ \t]*\s*\Z")
"""引用前残留的分隔线，例如邮件头块上方的 `----------------`。"""


@dataclass(frozen=True)
class QuoteSplit:
    head: str
    """引用之前的部分。"""
    quoted: str
    """从引用起点到结尾的原文。没有引用时为空字符串。"""
    format: QuoteFormat | None
    """识别出引用起点的规则。"""
    inline: bool
    """引用起点在一行的中间（被压缩成一行的引用）。"""


def split_quote(text: str) -> QuoteSplit:
    """取最早出现的引用起点，之后的全部视为引用。

    玩家几乎都在引用上方书写（top-posting），所以起点之后不再找新内容。
    """
    best: tuple[int, QuoteFormat] | None = None
    for fmt, pattern in QUOTE_RULES:
        match = pattern.search(text)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), fmt)
    if best is None:
        return QuoteSplit(head=text, quoted="", format=None, inline=False)

    index, fmt = best
    line_start = text.rfind("\n", 0, index) + 1
    inline = bool(text[line_start:index].strip(" \t\u3000"))
    if not inline:
        index = line_start
    head = text[:index]
    while (rule := _TRAILING_RULE.search(head.rstrip())) is not None:
        head = head.rstrip()[: rule.start()]
    return QuoteSplit(head=head, quoted=text[len(head) :], format=fmt, inline=inline)


# --- 字符修复 -----------------------------------------------------------------


class CharFix(StrEnum):
    """字符修复规则。只处理半角 `?`；全角 `？` 是真正的问号，不动。"""

    KNOWN_PHRASE = "known_phrase"
    """已知短语中的 `?` → `・`，例如 `ご意見?ご要望`。"""
    BULLET = "bullet"
    """行首的 `?` 后紧跟内容 → `・`（列表的项目符号）。"""
    SEPARATOR = "separator"
    """两个汉字或片假名之间的 `?` → `・`，例如 `運営?開発`。"""
    LONE = "lone"
    """单独占一行的 `?`，或问候语前的 `?`：丢失的字符，删除。"""


KNOWN_PHRASES: tuple[tuple[str, str], ...] = (("ご意見", "ご要望"),)
"""（前半，后半）：中间的 `?` 原本是 `・`。"""

KANJI_KATAKANA = re.compile(r"[一-鿿゠-ヿｦ-ﾟ]")


def _fix_rule(line: str, i: int) -> tuple[str, CharFix] | None:
    before, after = line[:i], line[i + 1 :]
    at_line_start = not before.strip(" \t\u3000")
    if at_line_start and not after.strip(" \t\u3000"):
        return "", CharFix.LONE
    if any(before.endswith(a) and after.startswith(b) for a, b in KNOWN_PHRASES):
        return "・", CharFix.KNOWN_PHRASE
    if at_line_start and after.startswith("いつもご利用"):
        return "", CharFix.LONE
    if at_line_start and after[:1] not in ("", " ", "\t", "\u3000", "?", "？"):
        return "・", CharFix.BULLET
    if before and after and KANJI_KATAKANA.match(before[-1]) and KANJI_KATAKANA.match(after[0]):
        return "・", CharFix.SEPARATOR
    return None


def fix_chars(text: str) -> tuple[str, Counter[CharFix]]:
    """恢复被替换成 `?` 的常见字符，返回修复后的文本和每条规则的修复次数。"""
    fixes: Counter[CharFix] = Counter()
    out_lines = []
    for line in text.split("\n"):
        if "?" not in line:
            out_lines.append(line)
            continue
        parts: list[str] = []
        last = 0
        for i, ch in enumerate(line):
            if ch != "?":
                continue
            rule = _fix_rule(line, i)
            if rule is None:
                continue
            parts.append(line[last:i])
            parts.append(rule[0])
            fixes[rule[1]] += 1
            last = i + 1
        parts.append(line[last:])
        out_lines.append("".join(parts))
    return "\n".join(out_lines), fixes


# --- 空白 ---------------------------------------------------------------------

_TRAILING_SPACE = re.compile(r"[ \t\u3000]+$", re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_whitespace(text: str) -> str:
    """去掉行尾空白，连续空行压缩成一个，去掉首尾空行。行首和行内的全角空格保留。"""
    text = _TRAILING_SPACE.sub("", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip("\n")


# --- 客服回复的固定部分 -------------------------------------------------------


def has_greeting(body: str) -> bool:
    return GREETING_MARK in body[:GREETING_SEARCH_CHARS]


def split_frame(body: str) -> tuple[str, str]:
    """拆出客服回复的问候语和结尾，返回（greeting, closing）。

    greeting 是到含 `サポート担当です` 的那一行为止的开头部分；closing 从问候语之后第一次
    出现的固定结语（没有时用禁止转载声明）开始到结尾。两者都是 `body` 的原样子串，
    找不到时为空字符串。
    """
    lines = body.split("\n")
    greeting_end = 0
    for i, line in enumerate(lines):
        if GREETING_MARK in line:
            greeting_end = i + 1
            break
        if sum(len(x) + 1 for x in lines[: i + 1]) > GREETING_SEARCH_CHARS:
            break

    closing_start = len(lines)
    for mark in (CLOSING_MARK, NOTICE_MARK):
        found = next((i for i in range(greeting_end, len(lines)) if mark in lines[i]), None)
        if found is not None:
            closing_start = found
            break

    greeting = "\n".join(lines[:greeting_end])
    closing = "\n".join(lines[closing_start:])
    return greeting, closing
