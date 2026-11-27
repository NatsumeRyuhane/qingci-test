"""工作台的指标（#8）：客服如何处理草稿，按模板和分流结果统计。

每封草稿以**最后一次**处理为准：先升级、后由高级客服批准的草稿算作批准。只统计收到过
处理的草稿。编辑距离的平均值只算发出的回复（原样批准为 0，修改后批准大于 0）。
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from copilot.contracts import Draft, Feedback, FeedbackAction
from copilot.storage import Repository

NO_TEMPLATE = "（无模板）"


@dataclass(frozen=True)
class Tally:
    key: str
    handled: int
    counts: dict[FeedbackAction, int]
    avg_edit_distance: float | None

    def rate(self, action: FeedbackAction) -> float:
        return self.counts.get(action, 0) / self.handled if self.handled else 0.0


def _tally(key: str, outcomes: list[Feedback]) -> Tally:
    counts: dict[FeedbackAction, int] = defaultdict(int)
    for feedback in outcomes:
        counts[feedback.action] += 1
    sent = [
        f.edit_distance
        for f in outcomes
        if f.action in {FeedbackAction.APPROVED, FeedbackAction.EDITED}
    ]
    return Tally(
        key=key,
        handled=len(outcomes),
        counts=dict(counts),
        avg_edit_distance=sum(sent) / len(sent) if sent else None,
    )


@dataclass(frozen=True)
class Metrics:
    total: Tally
    by_template: list[Tally]
    """按模板 ID 排序；没有模板的草稿归入 `NO_TEMPLATE`。"""
    by_route: list[Tally]


def final_outcomes(feedback: Iterable[Feedback]) -> dict[str, Feedback]:
    """每封草稿最后一次处理，按草稿 ID 索引。`feedback` 按写入顺序排列。"""
    last: dict[str, Feedback] = {}
    for item in feedback:
        last[item.draft_id] = item
    return last


def compute_metrics(repo: Repository) -> Metrics:
    outcomes = final_outcomes(repo.list_feedback())
    drafts: dict[str, Draft] = {}
    for draft_id in outcomes:
        draft = repo.get_draft(draft_id)
        if draft is not None:
            drafts[draft_id] = draft

    by_template: dict[str, list[Feedback]] = defaultdict(list)
    by_route: dict[str, list[Feedback]] = defaultdict(list)
    for draft_id, feedback in outcomes.items():
        draft = drafts.get(draft_id)
        if draft is None:
            continue
        template = draft.template_refs[0].template_id if draft.template_refs else NO_TEMPLATE
        by_template[template].append(feedback)
        by_route[draft.route.value].append(feedback)
    return Metrics(
        total=_tally("total", [outcomes[d] for d in drafts]),
        by_template=[_tally(k, v) for k, v in sorted(by_template.items())],
        by_route=[_tally(k, v) for k, v in sorted(by_route.items())],
    )
