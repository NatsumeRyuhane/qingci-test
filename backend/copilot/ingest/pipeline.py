"""把表格行组装成清洗后的 `Case`，并统计清洗结果。"""

import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from copilot.contracts import Case, CaseStatus, InquiryForm, Message, MessageRole
from copilot.ingest.form import REDACTED_KEYS, parse_fields, to_inquiry_form
from copilot.ingest.roles import RoleReason, detect_role
from copilot.ingest.sheet import SheetFormatError, SheetRow
from copilot.ingest.text import (
    CharFix,
    QuoteFormat,
    fix_chars,
    normalize_newlines,
    normalize_whitespace,
    split_frame,
    split_quote,
)
from copilot.ingest.timestamps import message_ref, recover_sent_at

DEFAULT_OUTPUT = Path("data/interim/cases.jsonl")
"""导入结果的默认位置（被 git 忽略，见 data/README.md）。"""


@dataclass
class IngestStats:
    """导入统计，可与 docs/data.md 中的数字对照。"""

    threads: int = 0
    messages: int = 0
    thread_lengths: Counter[int] = field(default_factory=Counter)
    roles: Counter[MessageRole] = field(default_factory=Counter)
    role_reasons: Counter[RoleReason] = field(default_factory=Counter)
    quote_formats: Counter[QuoteFormat] = field(default_factory=Counter)
    inline_quotes: int = 0
    char_fixes: Counter[CharFix] = field(default_factory=Counter)
    messages_with_fixes: int = 0
    agent_without_greeting: int = 0
    agent_without_closing: int = 0
    forms: int = 0
    categories: Counter[str] = field(default_factory=Counter)
    platforms: Counter[str] = field(default_factory=Counter)
    redacted_fields_not_empty: int = 0
    sent_at_known: int = 0
    sent_at_conflicts: int = 0
    statuses: Counter[CaseStatus] = field(default_factory=Counter)
    threads_without_agent: int = 0

    def lines(self) -> list[str]:
        """可读的统计报告，每项一行，顺序固定。"""

        lengths: Counter[str] = Counter()
        for k, n in self.thread_lengths.items():
            lengths[str(k) if k < 5 else "5+"] += n
        return [
            f"线程：{self.threads} · 邮件：{self.messages}",
            f"线程长度：{_counts(lengths, ['1', '2', '3', '4', '5+'])}",
            f"发送方：{_counts(self.roles, MessageRole)}",
            f"判断依据：{_counts(self.role_reasons, RoleReason)}",
            f"客服回复缺问候语：{self.agent_without_greeting}"
            f" · 缺结尾：{self.agent_without_closing}",
            f"去除引用：{_counts(self.quote_formats, QuoteFormat)}"
            f" · 压缩在一行中：{self.inline_quotes}",
            f"字符修复：{_counts(self.char_fixes, CharFix)} · 涉及邮件：{self.messages_with_fixes}",
            f"表单：{self.forms} · 脱敏字段非空：{self.redacted_fields_not_empty}",
            f"类别（按线程）：{_counts(self.categories, sorted(self.categories))}",
            f"平台（按线程）：{_counts(self.platforms, sorted(self.platforms))}",
            f"已知发送时间：{self.sent_at_known} · 引用头矛盾而未采用：{self.sent_at_conflicts}",
            f"案件状态：{_counts(self.statuses, CaseStatus)}",
            f"没有客服回复的线程：{self.threads_without_agent}",
        ]


def _counts(counter: Mapping[Any, int], order: Iterable[Any]) -> str:
    """按给定顺序列出非零的计数。"""
    return " · ".join(f"{key}: {counter[key]}" for key in order if counter.get(key))


def build_message(row: SheetRow, stats: IngestStats) -> tuple[Message, RoleReason]:
    split = split_quote(normalize_newlines(row.body))
    fixed, fixes = fix_chars(split.head)
    body = normalize_whitespace(fixed)
    role, reason = detect_role(row.seq, row.body, body)

    greeting = closing = ""
    if role is MessageRole.AGENT:
        greeting, closing = split_frame(body)
        stats.agent_without_greeting += not greeting
        stats.agent_without_closing += not closing

    stats.messages += 1
    stats.roles[role] += 1
    stats.role_reasons[reason] += 1
    if split.format is not None:
        stats.quote_formats[split.format] += 1
        stats.inline_quotes += split.inline
    if fixes:
        stats.char_fixes.update(fixes)
        stats.messages_with_fixes += 1

    message = Message(
        thread_id=row.thread_id,
        seq=row.seq,
        role=role,
        raw_body=row.body,
        body=body,
        quoted=split.quoted,
        greeting=greeting,
        closing=closing,
    )
    return message, reason


def _status(messages: list[Message]) -> CaseStatus:
    """最后一封客服回复之后还有玩家（或无法判断发送方的）邮件时为待处理。系统邮件不计。"""
    pending = False
    for message in messages:
        if message.role is MessageRole.AGENT:
            pending = False
        elif message.role in (MessageRole.PLAYER, MessageRole.UNKNOWN):
            pending = True
    return CaseStatus.OPEN if pending else CaseStatus.REPLIED


def build_case(thread_id: str, rows: list[SheetRow], stats: IngestStats) -> Case:
    rows = sorted(rows, key=lambda r: r.seq)
    seqs = [r.seq for r in rows]
    if len(set(seqs)) != len(seqs):
        raise SheetFormatError(f"线程 {thread_id} 中有重复的 メールID枝番：{seqs}")

    built = [build_message(row, stats) for row in rows]
    messages = [m for m, _ in built]

    # 表单只取线程的第 1 封（Case.form 的定义）。后面的邮件即使像表单，也只保留角色和正文。
    form: InquiryForm | None = None
    first, first_reason = built[0]
    if first.seq == 1 and first_reason is RoleReason.FORM:
        fields = parse_fields(first.body)
        form = to_inquiry_form(fields)
        stats.forms += 1
        stats.redacted_fields_not_empty += sum(1 for k in REDACTED_KEYS if fields.get(k))

    times, conflicts = recover_sent_at(
        [message_ref(m.seq, m.role, m.quoted, m.core) for m in messages]
    )
    if times:
        messages = [
            m.model_copy(update={"sent_at": times[m.seq]}) if m.seq in times else m
            for m in messages
        ]
    stats.sent_at_known += len(times)
    stats.sent_at_conflicts += conflicts

    case = Case(
        id=thread_id,
        messages=messages,
        form=form,
        category=form.category if form else None,
        status=_status(messages),
    )
    stats.threads += 1
    stats.thread_lengths[len(messages)] += 1
    stats.statuses[case.status] += 1
    stats.categories[form.category.value if form and form.category else "（无）"] += 1
    stats.platforms[form.platform.value if form and form.platform else "（无）"] += 1
    stats.threads_without_agent += not any(m.role is MessageRole.AGENT for m in messages)
    return case


def _thread_order(thread_id: str) -> tuple[int, int, str]:
    """数字 ID 按数值排在前面，其他 ID 按字符串排在后面。数值相同（`1` 和 `01`）时按字符串区分，
    所以顺序只取决于 ID 本身，与输入顺序无关。"""
    return (0, int(thread_id), thread_id) if thread_id.isdigit() else (1, 0, thread_id)


def build_cases(rows: Iterable[SheetRow]) -> tuple[list[Case], IngestStats]:
    """按 `メールID` 分组、按 `メールID枝番` 排序，组装成案件。结果的顺序只取决于输入内容。"""
    groups: dict[str, list[SheetRow]] = defaultdict(list)
    for row in rows:
        groups[row.thread_id].append(row)
    stats = IngestStats()
    cases = [build_case(tid, groups[tid], stats) for tid in sorted(groups, key=_thread_order)]
    return cases, stats


def write_cases(cases: Iterable[Case], path: Path) -> None:
    """写成 JSONL（每行一个案件）。先写临时文件再替换，中途失败不会留下半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for case in cases:
                f.write(case.model_dump_json())
                f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_cases(path: Path) -> list[Case]:
    """读回 `write_cases` 写出的文件。供后续步骤（#5、#6、#7）使用。"""
    with path.open(encoding="utf-8") as f:
        return [Case.model_validate_json(line) for line in f if line.strip()]
