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

from copilot.contracts import (
    Case,
    CaseStatus,
    Message,
    MessageRole,
    RoleEvidenceKind,
    RoleEvidenceStrength,
    TimePoint,
    TimeStatus,
)
from copilot.eval.split import Split, SplitManifest
from copilot.ingest.pipeline import thread_order
from copilot.ingest.timestamps import JST
from copilot.kb.evidence import CROSS_THREAD_KINDS, recompute_cross_thread_evidence

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
        later_times = [
            m.sent_at
            for m in case.messages
            if m.sent_at is not None and target_seq is not None and m.seq > target_seq
        ]
        prior_lo = max(prior_times, default=None)
        later_hi = min(later_times, default=None)
        if prior_lo is not None and later_hi is not None and later_hi < prior_lo:
            later_hi = None

        pos = self._pos.get(case.id)
        before = [a.sent_at for a in self._anchors if pos is not None and a.position < pos]
        after = [a.sent_at for a in self._anchors if pos is not None and a.position > pos]
        before_anchor = before[-1] if before else None
        if before_anchor is not None and later_hi is not None and before_anchor > later_hi:
            before_anchor = None

        earliest = max((x for x in (prior_lo, before_anchor) if x is not None), default=None)
        # 丢弃早于当前线程下界的跨线程上界锚点，绝不把下界往回倒换。
        valid_after = [t for t in after if earliest is None or t >= earliest]
        latest = min(
            [*([] if later_hi is None else [later_hi]), *valid_after],
            default=None,
        )

        if earliest is not None and latest is not None:
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


def _case_status(messages: Sequence[Message]) -> CaseStatus:
    last = messages[-1]
    if last.role is MessageRole.PLAYER:
        return CaseStatus.OPEN
    if last.role is MessageRole.AGENT and last.role_strength is RoleEvidenceStrength.STRONG:
        return CaseStatus.REPLIED
    return CaseStatus.UNDETERMINED


def _strip_unavailable_local_evidence(
    messages: Sequence[Message], *, drop_cross_thread: bool = False
) -> list[Message]:
    """移除来源邮件未在截断窗口内的角色证据（未提供可见线程集时一并剥离未验证的跨线程证据）。"""
    kept_ids = {m.id for m in messages}
    cleaned: list[Message] = []
    for m in messages:
        filtered = [
            e
            for e in m.role_evidence
            if not (
                (
                    e.kind is RoleEvidenceKind.QUOTE_CORROBORATION
                    and e.source_message_id not in kept_ids
                )
                or (drop_cross_thread and e.kind in CROSS_THREAD_KINDS)
            )
        ]
        cleaned.append(
            m
            if len(filtered) == len(m.role_evidence)
            else m.model_copy(update={"role_evidence": filtered})
        )
    return cleaned


def _rebuild_visible_cases(cases: Sequence[Case]) -> list[Case]:
    """在当前重放时刻可见的线程集合上重算跨线程角色证据与案件状态，避免泄露未来佐证。"""
    if not cases:
        return []
    rebuilt = recompute_cross_thread_evidence(cases)
    return [c.model_copy(update={"status": _case_status(c.messages)}) for c in rebuilt]


def truncate_case(
    case: Case,
    before_seq: int,
    *,
    visible_cases: Sequence[Case] | None = None,
) -> Case:
    """只保留 `seq < before_seq` 的邮件，去掉不可见的角色佐证并重新判断状态。

    - 传入 `visible_cases`（当前重放时刻已截断的可见线程）时，基于该集合重算跨线程证据，
      保留来自已可见来源的 `CORROBORATED_TEXT` / `REPEATED_TEXT` 并移除不可见来源。
    - 未传入 `visible_cases` 时，无法核实跨线程证据的来源是否已可见，因此保守移除
      `CORROBORATED_TEXT` 与 `REPEATED_TEXT`。
    """
    kept = [m for m in case.messages if m.seq < before_seq]
    if not kept:
        raise ValueError(f"案件 {case.id} 在 seq {before_seq} 之前没有邮件")
    messages = _strip_unavailable_local_evidence(kept, drop_cross_thread=(visible_cases is None))
    truncated = case.model_copy(update={"messages": messages, "status": _case_status(messages)})
    if visible_cases is None:
        return truncated
    others = [c for c in visible_cases if c.id != case.id]
    return _rebuild_visible_cases([*others, truncated])[-1]


def _truncate_by_cutoff(case: Case, cutoff: datetime | None) -> Case | None:
    """截断单个线程：遇到第一封 `sent_at > cutoff` 的邮件即停止，其后所有邮件一并裁掉。

    受数据集条件限制（docs/claims.md A-07：仅约 4.3% 邮件有 `sent_at`），未注明时间且其前
    无未来邮件的消息暂按提交顺序保留；当发生截断时同步剥离失效的线程内引用佐证并重算状态。
    """
    if cutoff is None:
        return case
    kept: list[Message] = []
    for m in case.messages:
        if m.sent_at is not None and m.sent_at > cutoff:
            break
        kept.append(m)
    if not kept:
        return None
    if len(kept) == len(case.messages):
        return case
    messages = _strip_unavailable_local_evidence(kept)
    return case.model_copy(update={"messages": messages, "status": _case_status(messages)})


def replay_corpus(train_cases: Sequence[Case], current: Case, now: TimePoint) -> list[Case]:
    """历史重放时给相似案件检索的语料：只保留线程顺序在当前案件之前的 train 案件，并按
    `now` 的下界截断未来邮件与不可见的跨线程角色证据（docs/security.md H-11、A-07）。"""
    position = thread_order(current.id)
    bounds = now.bounds()
    cutoff = bounds[0] if bounds is not None else None
    result: list[Case] = []
    for other in train_cases:
        if other.id == current.id or thread_order(other.id) >= position:
            continue
        trimmed = _truncate_by_cutoff(other, cutoff)
        if trimmed is not None:
            result.append(trimmed)
    return _rebuild_visible_cases(result)


def replay_history(all_cases: Sequence[Case], current: Case, now: TimePoint) -> list[Case]:
    """历史重放时给重复提交判断的 `history`：只保留更早线程并截断未来回复与佐证。"""
    return replay_corpus(all_cases, current, now)


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
    selected = _evenly_sample(items, limit)
    rebuilt_items: list[ReplyEvalItem] = []
    for item in selected:
        visible_earlier = replay_history(cases, item.case, item.now)
        rebuilt_case = truncate_case(
            by_id[item.case.id], item.before_seq, visible_cases=visible_earlier
        )
        rebuilt_items.append(
            ReplyEvalItem(
                label=item.label,
                case=rebuilt_case,
                actual_reply=item.actual_reply,
                before_seq=item.before_seq,
                now=item.now,
            )
        )
    return rebuilt_items


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
