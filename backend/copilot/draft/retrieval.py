"""起草时可用的知识与候选模板（#6，提案 0003 §4、§5、§9）。

**只有已批准的条目提供给模型**（`KnowledgeEntry.model_visible`）。每个条目取当前版本
（历史重放时只看 `known_from` 不晚于“现在”的版本），再按“现在”判断有效期：确定在有效期
之外的不提供；“现在”是估计值、区间跨过有效期边界的仍然提供，由 `template_window` 检查判为
不通过。

**限时模板**是被某条公告与事件（`Notice.template_ids`）链接的模板。它们**不在**静态模板目录
中，只有链接的事件与问题发生时间匹配（`inside`，或无法确定的 `uncertain`）时，才通过时间块
注入。

**候选模板**是分类出的意图下的模板，排序由代码保证：特定模板（生效中的限时模板、带占位符的
模板）永远排在通用模板之前；限时模板中，确定匹配的排在无法确定的之前。
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from copilot.contracts import (
    Fact,
    GlossaryEntry,
    InquiryForm,
    Intent,
    KnowledgeEntry,
    Notice,
    Policy,
    PolicyKind,
    ReplyTemplate,
    TimeContext,
    current_version,
)
from copilot.draft.timectx import WindowMatch, match_notice, match_window


@dataclass(frozen=True)
class KnowledgeSnapshot:
    """知识库中所有条目的所有版本，未经筛选。由 `load_snapshot` 从数据库读取，测试中直接构造。"""

    templates: Sequence[ReplyTemplate] = ()
    intents: Sequence[Intent] = ()
    policies: Sequence[Policy] = ()
    notices: Sequence[Notice] = ()
    facts: Sequence[Fact] = ()
    glossary: Sequence[GlossaryEntry] = ()


def load_snapshot(store: "KnowledgeStoreLike") -> KnowledgeSnapshot:
    """从知识库存储读取全部条目。"""
    return KnowledgeSnapshot(
        templates=store.entries(ReplyTemplate),
        intents=store.entries(Intent),
        policies=store.entries(Policy),
        notices=store.entries(Notice),
        facts=store.entries(Fact),
        glossary=store.entries(GlossaryEntry),
    )


class KnowledgeStoreLike(Protocol):
    """`load_snapshot` 需要的存储接口（`copilot.kb.store.KnowledgeStore` 满足它）。"""

    def entries[E: KnowledgeEntry](self, model: type[E]) -> list[E]: ...


@dataclass(frozen=True)
class NoticeMatch:
    """一条生效中的公告与事件，以及它与本案件问题发生时间的匹配结果。"""

    notice: Notice
    match: WindowMatch
    """公告在“现在”的有效期与事件和问题发生时间的匹配中较差的一个：`inside` 或 `uncertain`。
    `outside` 的公告不出现在 `KnowledgeView.notices` 中。"""


@dataclass(frozen=True)
class Candidate:
    """一个候选模板。"""

    template: ReplyTemplate
    time_bound: bool
    """是否是限时模板（被某条公告与事件链接）。"""
    window: WindowMatch
    """模板能否用于本案件：模板在“现在”的有效期，以及（限时模板）链接的事件与问题发生时间的
    匹配，取两者中较差的一个。`template_window` 检查要求它是 `inside`。"""
    notices: tuple[NoticeMatch, ...] = ()
    """链接这个限时模板、并与本案件匹配的公告与事件。"""
    in_intent: bool = True
    """是否属于分类出的意图。模型选了其他意图的模板时为 False，此时不能自动发送。"""

    @property
    def specific(self) -> bool:
        """特定模板：限时模板，或带占位符的模板。通用模板永远排在它们之后。"""
        return self.time_bound or bool(self.template.slots)


_WORST = [WindowMatch.INSIDE, WindowMatch.UNCERTAIN, WindowMatch.OUTSIDE]


def _worse(a: WindowMatch, b: WindowMatch) -> WindowMatch:
    return max(a, b, key=_WORST.index)


def _visible[E: KnowledgeEntry](
    versions: Iterable[E], time_ctx: TimeContext, known_at: datetime | None
) -> dict[str, tuple[E, WindowMatch]]:
    """每个条目的当前版本中，已批准、并且在“现在”不确定在有效期外的，附有效期的匹配结果。"""
    groups: dict[str, list[E]] = defaultdict(list)
    for entry in versions:
        groups[entry.id].append(entry)
    visible: dict[str, tuple[E, WindowMatch]] = {}
    for entry_id in sorted(groups):
        current = current_version(groups[entry_id], known_at=known_at)
        if current is None or not current.model_visible:
            continue
        validity = match_window(time_ctx.now, current.valid_from, current.valid_to)
        if validity is not WindowMatch.OUTSIDE:
            visible[entry_id] = (current, validity)
    return visible


@dataclass(frozen=True)
class KnowledgeView:
    """在“现在”可以提供给模型的知识。由 `build_view` 构造。"""

    time_context: TimeContext
    intents: dict[str, Intent]
    static_templates: dict[str, ReplyTemplate]
    """静态模板目录：已批准、有效、不限时的模板，按 id 排序。放在 prompt 的静态前缀中。"""
    time_bound_templates: dict[str, ReplyTemplate]
    """生效中、并且链接的事件与本案件匹配的限时模板。只放在时间块中。"""
    notices: list[NoticeMatch]
    """生效中、并且与本案件匹配（`inside` 或 `uncertain`）的公告与事件。"""
    policies: list[Policy]
    facts: list[Fact]
    glossary: list[GlossaryEntry]
    template_validity: dict[str, WindowMatch] = field(default_factory=dict)
    """每个可见模板在“现在”的有效期匹配结果。"""
    linked: dict[str, tuple[NoticeMatch, ...]] = field(default_factory=dict)
    """限时模板 id → 链接它、并与本案件匹配的公告与事件。"""

    @property
    def forbidden_promises(self) -> list[Policy]:
        return [p for p in self.policies if p.kind is PolicyKind.FORBIDDEN_PROMISE]

    @property
    def escalation_triggers(self) -> list[Policy]:
        return [p for p in self.policies if p.kind is PolicyKind.ESCALATION_TRIGGER]

    def template(self, template_id: str) -> ReplyTemplate | None:
        """静态目录或时间块中的模板。不可见的模板返回 None。"""
        return self.static_templates.get(template_id) or self.time_bound_templates.get(template_id)

    def candidate(self, template_id: str, intent: str) -> Candidate | None:
        """把一个可见的模板包装成 `Candidate`（模型选择的模板也用它，不一定属于 `intent`）。"""
        template = self.template(template_id)
        if template is None:
            return None
        validity = self.template_validity[template_id]
        if template_id in self.time_bound_templates:
            matches = self.linked[template_id]
            best = min((m.match for m in matches), key=_WORST.index)
            in_intent = template.intent == intent or any(
                intent in m.notice.intents for m in matches
            )
            return Candidate(
                template=template,
                time_bound=True,
                window=_worse(validity, best),
                notices=matches,
                in_intent=in_intent,
            )
        return Candidate(
            template=template,
            time_bound=False,
            window=validity,
            in_intent=template.intent == intent,
        )

    def candidates(self, intent: str) -> list[Candidate]:
        """分类出的意图下的候选模板，特定模板在前（提案 0003 §5）。"""
        found = [
            c
            for template_id in (*self.time_bound_templates, *self.static_templates)
            if (c := self.candidate(template_id, intent)) is not None and c.in_intent
        ]
        return sorted(found, key=_candidate_order)


def _candidate_order(candidate: Candidate) -> tuple[int, int, str]:
    if candidate.time_bound:
        rank = 0 if candidate.window is WindowMatch.INSIDE else 1
    elif candidate.template.slots:
        rank = 2
    else:
        rank = 3
    return (rank, _WORST.index(candidate.window), candidate.template.id)


def build_view(
    snapshot: KnowledgeSnapshot,
    time_ctx: TimeContext,
    *,
    form: InquiryForm | None = None,
    known_at: datetime | None = None,
) -> KnowledgeView:
    """筛选出在“现在”可以提供给模型的知识。`known_at` 不为空时是历史重放（只看当时已知的版本）。"""
    templates = _visible(snapshot.templates, time_ctx, known_at)
    notices = _visible(snapshot.notices, time_ctx, known_at)
    # 被任何公告的任何版本链接过的模板都是限时模板（包括已下线或过期的公告）：事故模板
    # 不会因为公告下线而变成通用模板进入静态目录。
    # 历史重放时只看当时已知的版本：之后才加入的链接不能影响当时的分类。
    time_bound_ids = {
        tid
        for n in snapshot.notices
        if known_at is None or n.known_from <= known_at
        for tid in n.template_ids
    }

    matched: list[NoticeMatch] = []
    for notice, validity in notices.values():
        # 公告在“现在”是否有效也可能无法确定（“现在”是估计值）：与事件的匹配取较差的一个，
        # 否则不限有效期的模板会把它变成 inside，template_window 检查就会误判为通过。
        match = _worse(validity, match_notice(notice, time_ctx, form=form))
        if match is not WindowMatch.OUTSIDE:
            matched.append(NoticeMatch(notice=notice, match=match))
    linked: dict[str, list[NoticeMatch]] = defaultdict(list)
    for notice_match in matched:
        for template_id in notice_match.notice.template_ids:
            if template_id in templates:
                linked[template_id].append(notice_match)

    return KnowledgeView(
        time_context=time_ctx,
        intents={i: e for i, (e, _) in _visible(snapshot.intents, time_ctx, known_at).items()},
        static_templates={i: t for i, (t, _) in templates.items() if i not in time_bound_ids},
        time_bound_templates={i: templates[i][0] for i in sorted(linked)},
        notices=matched,
        policies=[p for p, _ in _visible(snapshot.policies, time_ctx, known_at).values()],
        facts=[f for f, _ in _visible(snapshot.facts, time_ctx, known_at).values()],
        glossary=[g for g, _ in _visible(snapshot.glossary, time_ctx, known_at).values()],
        template_validity={i: v for i, (_, v) in templates.items()},
        linked={i: tuple(m) for i, m in linked.items()},
    )
