"""识别模板中的槽位与案件特定的内容（#5）。

样本中客服回复的措辞几乎是固定的：簇内的变体大多是多一句或少一句，而不是填了不同的值。所以
分两种：

- **槽位**（`slots`）：代表正文中的日期、时刻、时间窗口、数字或 `「名称」`，并且簇内有变体
  正好在这个位置上不同（差异完全落在这一段之内）。在正文中替换为 `{{name}}`。
- **特定内容**（`specific`）：代表正文中的日期、时刻和时间窗口，但簇内没有变体在这里不同。
  它们通常是某次事故的固定时间，不是槽位：不替换，标出来供标注和审核判断（是否限定事故期间、
  是否是通用模板）。

正则只用有界的重复，不会因构造的输入而回溯（#18）。
"""

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from copilot.contracts import TemplateSlot


class SpanKind(StrEnum):
    """识别出的内容的类型。按这个顺序优先：先找时间窗口，再找日期和时刻，最后找数字。"""

    WINDOW = "window"
    DATETIME = "datetime"
    DATE = "date"
    TIME = "time"
    NAME = "name"
    NUMBER = "number"


_DATE = r"(?:\d{4}[年/-])?\d{1,2}[月/-]\d{1,2}日?"
_TIME = r"\d{1,2}(?:[:：]\d{2}(?:[:：]\d{2})?|時(?:\d{1,2}分)?)"
_POINT = rf"(?:{_DATE}[ 　]?)?{_TIME}|{_DATE}"
_PATTERNS: dict[SpanKind, re.Pattern[str]] = {
    SpanKind.WINDOW: re.compile(rf"(?:{_POINT})[ 　]?(?:～|〜|~|－|-|から)[ 　]?(?:{_POINT})"),
    SpanKind.DATETIME: re.compile(rf"{_DATE}[ 　]?{_TIME}"),
    SpanKind.DATE: re.compile(_DATE),
    SpanKind.TIME: re.compile(_TIME),
    SpanKind.NAME: re.compile(r"「[^「」\n]{1,40}」"),
    SpanKind.NUMBER: re.compile(r"\d+(?:[,，]\d{3})*"),
}
SPECIFIC_KINDS = (SpanKind.WINDOW, SpanKind.DATETIME, SpanKind.DATE, SpanKind.TIME)
"""即使没有变化也要标出的类型。`「名称」` 大多是界面上的固定名称（例如菜单项），数字太常见，
都只在有变化时才当作槽位。"""

DESCRIPTIONS_ZH: dict[SpanKind, str] = {
    SpanKind.WINDOW: "时间窗口（自动识别，待审核）",
    SpanKind.DATETIME: "日期和时刻（自动识别，待审核）",
    SpanKind.DATE: "日期（自动识别，待审核）",
    SpanKind.TIME: "时刻（自动识别，待审核）",
    SpanKind.NAME: "名称，例如道具或活动名（自动识别，待审核）",
    SpanKind.NUMBER: "数字（自动识别，待审核）",
}


@dataclass(frozen=True)
class Span:
    kind: SpanKind
    start: int
    end: int
    text: str


@dataclass
class SlotDetection:
    body_ja: str
    """代表正文，槽位替换为 `{{name}}`。"""
    slots: list[TemplateSlot] = field(default_factory=list)
    specific: list[Span] = field(default_factory=list)
    """没有变化的日期、时刻和时间窗口（位置相对于原始的代表正文）。"""


def find_spans(text: str, kinds: tuple[SpanKind, ...] = tuple(SpanKind)) -> list[Span]:
    """`text` 中互不重叠的内容，按位置排列。按 `SpanKind` 的顺序优先，先找到的占住位置。"""
    taken: list[Span] = []
    # 逐字符的占用标记：检查重叠只看这一段本身（长度有界），匹配很多时也保持线性。
    occupied = bytearray(len(text))
    for kind in SpanKind:
        if kind not in kinds:
            continue
        for match in _PATTERNS[kind].finditer(text):
            start, end = match.span()
            if any(occupied[start:end]):
                continue
            occupied[start:end] = b"\x01" * (end - start)
            taken.append(Span(kind, start, end, match.group()))
    return sorted(taken, key=lambda s: s.start)


MAX_DIFF_CHARS = 5_000
"""超过这个长度的正文不做逐字比较（difflib 最坏是平方级），也就不从变化中识别槽位。样本中
客服回复最长约 800 个字符；更长的“回复”多半是粘贴或伪造的输入（#18）。"""


def _diff_ranges(representative: str, variants: list[str]) -> list[tuple[int, int]]:
    """各变体与代表正文不同的位置（代表正文中的区间；插入时区间为空）。"""
    if len(representative) > MAX_DIFF_CHARS:
        return []
    ranges = []
    for variant in variants:
        if len(variant) > MAX_DIFF_CHARS:
            continue
        matcher = difflib.SequenceMatcher(None, representative, variant, autojunk=False)
        ranges += [(i1, i2) for op, i1, i2, _, _ in matcher.get_opcodes() if op != "equal"]
    return ranges


def _varies(span: Span, ranges: list[tuple[int, int]]) -> bool:
    """有差异完全落在这一段之内。大段的增删即使经过这一段也不算。"""
    return any(span.start <= i1 and i2 <= span.end and i1 < span.end for i1, i2 in ranges)


def detect_slots(representative: str, variants: list[str]) -> SlotDetection:
    """在代表正文中识别槽位和特定内容。`variants` 是簇内其他不同的正文。"""
    ranges = _diff_ranges(representative, variants)
    spans = find_spans(representative)
    slots: list[TemplateSlot] = []
    specific: list[Span] = []
    counts: Counter[SpanKind] = Counter()
    pieces: list[str] = []
    position = 0
    for span in spans:
        if not _varies(span, ranges):
            if span.kind in SPECIFIC_KINDS:
                specific.append(span)
            continue
        counts[span.kind] += 1
        name = f"{span.kind.value}_{counts[span.kind]}"
        inner = span.text
        prefix = suffix = ""
        if span.kind is SpanKind.NAME:
            prefix, inner, suffix = "「", inner[1:-1], "」"
        pieces += [representative[position : span.start], prefix, f"{{{{{name}}}}}", suffix]
        position = span.end
        slots.append(
            TemplateSlot(name=name, description_zh=DESCRIPTIONS_ZH[span.kind], example_ja=inner)
        )
    pieces.append(representative[position:])
    return SlotDetection(body_ja="".join(pieces), slots=slots, specific=specific)
