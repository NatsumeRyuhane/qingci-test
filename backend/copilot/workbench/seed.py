"""演示数据：把导入的样本线程重放进工作台（`copilot seed`，#8）。

- 默认只从 **dev** 中挑选：知识库只用 train 构建（D-012），演示不会出现“用学过的邮件回答
  自己”的情况；test 只在报告基线时使用。
- 每个线程截到最后一封玩家邮件，去掉之后的客服回复和系统邮件，相当于玩家刚刚来信、等待回复。
  没有玩家邮件的线程跳过。
- 按类别轮流挑选，结果是确定的（同样的输入总是选出同样的线程）。
- 数据库中已有的案件跳过，不覆盖在工作台中已经处理过的内容。

样本只在运行时读取，不进 git（D-002）。
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from copilot.contracts import Case, CaseStatus, MessageRole
from copilot.ingest.pipeline import thread_order
from copilot.storage import Repository


def replayable(case: Case) -> Case | None:
    """截到最后一封玩家邮件，状态为 `open`。没有玩家邮件时返回 None。"""
    last = max(
        (i for i, m in enumerate(case.messages) if m.role is MessageRole.PLAYER), default=None
    )
    if last is None:
        return None
    return case.model_copy(
        update={"messages": case.messages[: last + 1], "status": CaseStatus.OPEN}
    )


def pick(cases: Iterable[Case], count: int) -> list[Case]:
    """按类别轮流挑选 `count` 个可重放的线程，每个类别内按线程顺序。"""
    groups: dict[str, list[Case]] = defaultdict(list)
    for case in sorted(cases, key=lambda c: thread_order(c.id)):
        replay = replayable(case)
        if replay is not None:
            groups[case.category.value if case.category else ""].append(replay)
    picked: list[Case] = []
    queues = [groups[k] for k in sorted(groups)]
    while len(picked) < count and any(queues):
        for queue in queues:
            if queue and len(picked) < count:
                picked.append(queue.pop(0))
    return picked


@dataclass
class SeedReport:
    added: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    """`ids` 中指定、但在样本里找不到或没有玩家邮件的线程。"""


def seed(
    repo: Repository,
    cases: Sequence[Case],
    *,
    count: int = 10,
    ids: Sequence[str] = (),
) -> SeedReport:
    """把线程写入工作台。给出 `ids` 时只写这些线程（不受切分限制），否则按 `pick` 挑选。"""
    report = SeedReport()
    if ids:
        by_id = {c.id: c for c in cases}
        chosen = []
        for case_id in ids:
            replay = replayable(by_id[case_id]) if case_id in by_id else None
            if replay is None:
                report.missing.append(case_id)
            else:
                chosen.append(replay)
    else:
        chosen = pick(cases, count)
    for case in chosen:
        if repo.get_case(case.id) is not None:
            report.skipped_existing.append(case.id)
            continue
        repo.save_case(case)
        report.added.append(case.id)
    return report
