"""稳定的模板 ID（提案 0003 §12）。

人工审核的结果以模板 ID 为键保存（覆盖层），所以重新构建时同一个模板必须得到同一个 ID：

1. 首次出现的模板：ID 由代表正文的哈希生成（`tpl-` + 12 位十六进制），从零构建也得到相同的 ID。
2. 重新构建时，与上一次的候选对应：
   - 代表正文（规范化后）相同 → 沿用；
   - 否则按来源回复的重合度（Jaccard ≥ `ID_MATCH_THRESHOLD`）对应，并且必须是唯一、双向的
     对应（上一次的那个候选的最佳对应也是它）。有多个候选都满足时是**歧义**：生成新 ID 并报告，
     不猜测。
3. 上一次有、这一次没有对应上的候选报告为“消失”，由审核决定是否下线。
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

ID_MATCH_THRESHOLD = 0.5
"""按来源回复对应时，两次构建的来源回复集合的 Jaccard 至少为这个值。"""


@dataclass(frozen=True)
class IdentityInput:
    """对应 ID 所需的信息：代表正文的键（`text_key`）和来源回复。"""

    key: str
    members: frozenset[str]


@dataclass(frozen=True)
class PreviousIdentity(IdentityInput):
    id: str


@dataclass
class IdAssignment:
    ids: list[str]
    """与输入的簇一一对应。"""
    reused_by_text: int = 0
    reused_by_members: int = 0
    new: int = 0
    ambiguous: list[str] = field(default_factory=list)
    """因为对应不唯一而生成了新 ID 的簇的 ID。"""
    disappeared: list[str] = field(default_factory=list)
    """上一次有、这一次没有对应上的 ID。"""


def text_id(key: str) -> str:
    """由代表正文的键生成的 ID。"""
    return "tpl-" + hashlib.sha256(key.encode()).hexdigest()[:12]


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def assign_ids(
    clusters: Sequence[IdentityInput], previous: Iterable[PreviousIdentity]
) -> IdAssignment:
    """为 `clusters` 分配 ID。`previous` 是上一次构建的候选（第一次构建时为空）。"""
    previous = list(previous)
    by_key = {p.key: p for p in previous}
    ids: list[str | None] = [None] * len(clusters)
    used: set[str] = set()
    result = IdAssignment(ids=[])

    for i, cluster in enumerate(clusters):
        match = by_key.get(cluster.key)
        if match is not None and match.id not in used:
            ids[i] = match.id
            used.add(match.id)
            result.reused_by_text += 1

    pending = [i for i in range(len(clusters)) if ids[i] is None]
    remaining = [p for p in previous if p.id not in used]
    options = {
        i: [p for p in remaining if _jaccard(clusters[i].members, p.members) >= ID_MATCH_THRESHOLD]
        for i in pending
    }
    claimants: dict[str, list[int]] = defaultdict(list)
    for i, opts in options.items():
        for option in opts:
            claimants[option.id].append(i)
    ambiguous: set[int] = set()
    for i, opts in options.items():
        # 双向唯一：只有一个满足条件的旧候选，并且它也只被这一个簇认领。
        if len(opts) == 1 and claimants[opts[0].id] == [i]:
            ids[i] = opts[0].id
            used.add(opts[0].id)
            result.reused_by_members += 1
        elif opts:
            ambiguous.add(i)

    # 上一次用过的 ID（包括消失的）都不再分给别的模板。
    reserved = used | {p.id for p in previous}
    for i in pending:
        if ids[i] is not None:
            continue
        base = text_id(clusters[i].key)
        new_id, n = base, 2
        while new_id in reserved:
            new_id, n = f"{base}-{n}", n + 1
        ids[i] = new_id
        reserved.add(new_id)
        result.new += 1
        if i in ambiguous:
            result.ambiguous.append(new_id)

    result.ids = [i for i in ids if i is not None]
    result.disappeared = sorted(p.id for p in previous if p.id not in used)
    return result
