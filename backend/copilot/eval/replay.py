"""离线评测的历史重放工具（#7，D-012，提案 0003 §9）。

历史重放要解决三个问题：
1. **模拟“现在”**（`estimate_now`）：样本大多没有发送时间。被评测的客服回复本身恢复了
   `sent_at` 时，“现在”为 `known`；否则按线程顺序（线程 ID 即提交顺序）从相邻有已知发送时间
   的线程插值出区间 `[earliest, latest]`，状态为 `estimated`；完全没有时间锚点时回退到指定的
   默认时间。
2. **截断案件**（`truncate_case`）：只保留被评测回复之前的邮件（`seq < before_seq`），模拟
   客服起草那一刻看到的线程状态。
3. **截断相似案件语料**（`replay_corpus`）：只保留线程顺序在当前案件之前的 train 案件，并
   去掉发送时间晚于“现在”的邮件（docs/security.md H-11、docs/claims.md A-07）。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from copilot.contracts import Case, CaseStatus, Message, MessageRole, TimePoint, TimeStatus
from copilot.eval.split import Split, SplitManifest
from copilot.ingest.pipeline import thread_order
from copilot.ingest.timestamps import JST

if TYPE_CHECKING:
    from copilot.kb.labels import NoReplyLabel, ReplyLabel

DEFAULT_REPLAY_NOW = datetime(2024, 3, 20, 12, 0, tzinfo=JST)
"""样本完全没有已知发送时间时的默认重放时间（仅用于极小的合成测试集）。"""
_FALLBACK_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class _Anchor:
    position: int
    sent_at: datetime


class TimelineIndex:
    """所有案件的时间锚点索引：按线程顺序记录每个已知发送时间的位置，用于为没有发送时间的
    线程插值 `estimated` 区间。"""

    def __init__(self, cases: Iterable[Case]) -> None:
        ordered = sorted(cases, key=lambda c: thread_order(c.id))
        self._pos: dict[str, int] = {c.id: i for i, c in enumerate(ordered)}
        anchors: list[_Anchor] = []
        for i, case in enumerate(ordered):
            times = [m.sent_at for m in case.messages if m.sent_at is not None]
            if times:
                anchors.append(_Anchor(position=i, sent_at=min(times)))
        self._anchors = anchors

    def position(self, case_id: str) -> int | None:
        return self._pos.get(case_id)

    def estimate_now(
        self,
        case: Case,
        *,
        target_seq: int | None = None,
        fallback: datetime = DEFAULT_REPLAY_NOW,
    ) -> TimePoint:
        """为 `case`（起草 `target_seq` 之前）确定模拟的“现在”。"""
        if target_seq is not None:
            target = next((m for m in case.messages if m.seq == target_seq), None)
            if target is not None and target.sent_at is not None:
                return TimePoint(
                    status=TimeStatus.KNOWN,
                    value=target.sent_at,
                    basis_zh="回复发送时间（引用恢复）",
                )
        prior_times = [
            m.sent_at
            for m in case.messages
            if m.sent_at is not None and (target_seq is None or m.seq < target_seq)
        ]
        pos = self._pos.get(case.id)
        before = [a.sent_at for a in self._anchors if pos is not None and a.position < pos]
        after = [a.sent_at for a in self._anchors if pos is not None and a.position > pos]
        earliest = max([*prior_times, *([before[-1]] if before else [])], default=None)
        latest = min(after, default=None)

        if earliest is not None and latest is not None and earliest <= latest:
            return TimePoint(
                status=TimeStatus.ESTIMATED,
                earliest=earliest,
                latest=latest,
                basis_zh="按线程顺序相邻已知时间插值",
            )
        if earliest is not None:
            return TimePoint(
                status=TimeStatus.ESTIMATED,
                earliest=earliest,
                latest=earliest + _FALLBACK_WINDOW,
                basis_zh="按之前最近已知时间向后估计",
            )
        if latest is not None:
            return TimePoint(
                status=TimeStatus.ESTIMATED,
                earliest=latest - _FALLBACK_WINDOW,
                latest=latest,
                basis_zh="按之后最近已知时间向前估计",
            )
        return TimePoint(status=TimeStatus.KNOWN, value=fallback, basis_zh="默认重放时间")


def truncate_case(case: Case, before_seq: int) -> Case:
    """只保留 `seq < before_seq` 的邮件，状态按最后一封邮件重新判断。"""
    messages = [m for m in case.messages if m.seq < before_seq]
    if not messages:
        raise ValueError(f"案件 {case.id} 在 seq {before_seq} 之前没有邮件")
    last = messages[-1]
    if last.role is MessageRole.PLAYER:
        status = CaseStatus.OPEN
    elif last.role is MessageRole.AGENT:
        status = CaseStatus.REPLIED
    else:
        status = case.status
    return case.model_copy(update={"messages": messages, "status": status})


def replay_corpus(train_cases: Sequence[Case], current: Case, now: TimePoint) -> list[Case]:
    """历史重放时给相似案件检索的语料：只保留线程顺序在当前案件之前的 train 案件，并去掉
    发送时间明确晚于“现在”的邮件（docs/security.md H-11、docs/claims.md A-07）。"""
    position = thread_order(current.id)
    bounds = now.bounds()
    cutoff = bounds[1] if bounds is not None else None
    result: list[Case] = []
    for other in train_cases:
        if other.id == current.id or thread_order(other.id) >= position:
            continue
        if cutoff is not None:
            kept = [m for m in other.messages if m.sent_at is None or m.sent_at <= cutoff]
            if not kept:
                continue
            if len(kept) != len(other.messages):
                other = other.model_copy(update={"messages": kept})
        result.append(other)
    return result


@dataclass(frozen=True)
class ReplyEvalItem:
    """一条待评测的客服回复样本。"""

    label: "ReplyLabel"
    case: Case
    """已截断到 `before_seq` 之前的案件。"""
    actual_reply: Message
    """真实的历史客服回复（评测对照对象，不进入起草输入）。"""
    before_seq: int
    now: TimePoint


@dataclass(frozen=True)
class NoReplyEvalItem:
    """一条待评测的“不回复”样本。"""

    label: "NoReplyLabel"
    case: Case
    now: TimePoint


def _evenly_sample[T](items: Sequence[T], limit: int | None) -> list[T]:
    """按均匀步长抽取 `limit` 条，保持时间线上的代表性且完全可复现。"""
    if limit is None or limit >= len(items):
        return list(items)
    if limit <= 0:
        return []
    n = len(items)
    return [items[(i * n) // limit] for i in range(limit)]


def build_reply_items(
    cases: Sequence[Case],
    manifest: SplitManifest,
    split: Split,
    labels: Sequence["ReplyLabel"],
    *,
    limit: int | None = None,
    first_only: bool = False,
) -> list[ReplyEvalItem]:
    """从标准答案构造待评测的回复样本列表。"""
    by_id = {c.id: c for c in cases}
    timeline = TimelineIndex(cases)
    side_ids = manifest.ids(split)
    items: list[ReplyEvalItem] = []
    for label in labels:
        if label.split is not split:
            continue
        thread_id, _, seq_str = label.message_id.rpartition(":")
        if thread_id not in side_ids or thread_id not in by_id:
            continue
        if first_only and not label.first_agent_reply:
            continue
        seq = int(seq_str)
        full = by_id[thread_id]
        actual = next((m for m in full.messages if m.seq == seq), None)
        if actual is None or not any(m.seq < seq for m in full.messages):
            continue
        items.append(
            ReplyEvalItem(
                label=label,
                case=truncate_case(full, seq),
                actual_reply=actual,
                before_seq=seq,
                now=timeline.estimate_now(full, target_seq=seq),
            )
        )
    return _evenly_sample(items, limit)


def build_no_reply_items(
    cases: Sequence[Case],
    manifest: SplitManifest,
    split: Split,
    labels: Sequence["NoReplyLabel"],
    *,
    limit: int | None = None,
) -> list[NoReplyEvalItem]:
    """从标准答案构造待评测的“不回复”样本列表。"""
    by_id = {c.id: c for c in cases}
    timeline = TimelineIndex(cases)
    side_ids = manifest.ids(split)
    items: list[NoReplyEvalItem] = []
    for label in labels:
        if label.split is not split:
            continue
        if label.thread_id not in side_ids or label.thread_id not in by_id:
            continue
        full = by_id[label.thread_id]
        items.append(
            NoReplyEvalItem(
                label=label,
                case=full,
                now=timeline.estimate_now(full),
            )
        )
    return _evenly_sample(items, limit)
