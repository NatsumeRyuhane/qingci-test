"""起草服务：一个案件从分析到分流的完整流水线（#6）。CLI（`copilot draft`）、界面（#8）、
评测（#7）以及之后的 API（#10）都调用 `draft_case`。

    时间上下文 → 可用知识 → 分析 → “不回复” → 候选模板 → 相似案件 → 起草 → 自检 → 回译 → 分流

**每个草稿都经过 `run_checks` 和 `route`**（docs/claims.md C-27）：这是生成 `Draft` 的唯一
途径，`Draft` 的分流和理由只来自 `route`。“不回复”自动生效时不生成草稿。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import AwareDatetime

from copilot.contracts import (
    Case,
    CaseAnalysis,
    CaseStatus,
    Citation,
    Draft,
    DraftMode,
    MessageRole,
    NoReplyProposal,
    Route,
    SimilarCaseRef,
    TemplateRef,
    TimeContext,
)
from copilot.contracts.base import Contract
from copilot.draft.analyze import analyze
from copilot.draft.checks import CheckContext, run_checks
from copilot.draft.compose import back_translate, compose, provided_entries
from copilot.draft.retrieval import KnowledgeSnapshot, KnowledgeView, build_view
from copilot.draft.route import RouteInput, decide_no_reply, route
from copilot.draft.similar import SimilarCaseRetriever, case_text, corpus_before
from copilot.draft.timectx import DEFAULT_LOOKBACK_DAYS, build_time_context, known_at_for
from copilot.kb.cluster import ClusterIndex
from copilot.kb.knowledge import StyleGuide
from copilot.llm import LLMClient

DEFAULT_MIN_CONFIDENCE = 0.85


class DraftOutcome(Contract):
    """一个案件的处理结果。`draft` 为空表示“不回复”自动生效，案件应设为 `no_reply_needed`。"""

    time_context: TimeContext
    analysis: CaseAnalysis
    draft: Draft | None = None
    no_reply: NoReplyProposal | None = None
    """“不回复”的判断（代码确认的重复提交，或分析的提议）。为空表示需要回复。"""
    no_reply_auto: bool = False
    """“不回复”是否自动生效（提案 0003 §8）。"""
    no_reply_reason_zh: str | None = None
    proposed_status: CaseStatus | None = None
    """建议的案件状态：`drafted`、`escalated` 或 `no_reply_needed`。由调用方决定是否写入。"""
    created_at: AwareDatetime


@dataclass(frozen=True)
class DraftParams:
    """起草的参数（来自配置）。"""

    analyze_model: str
    draft_model: str
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    lookback_days: int = DEFAULT_LOOKBACK_DAYS


class TemplateMatcher:
    """把一封客服回复的答复部分对应到可见的模板：先精确匹配，再按近似度匹配（与 #5 的标准答案
    使用同一个索引，`copilot.kb.cluster.ClusterIndex`）。"""

    def __init__(self, view: KnowledgeView) -> None:
        templates = [*view.static_templates.values(), *view.time_bound_templates.values()]
        self._ids = [t.id for t in templates]
        self._index = ClusterIndex.from_variants([[t.body_ja] for t in templates])

    def templates_used(self, case: Case) -> list[str]:
        """案件中的客服回复用过的模板，按出现顺序。"""
        found: list[str] = []
        for message in case.messages:
            if message.role is not MessageRole.AGENT or not message.core:
                continue
            match = self._index.lookup(message.core)
            if match is not None and self._ids[match[0]] not in found:
                found.append(self._ids[match[0]])
        return found


async def draft_case(
    case: Case,
    *,
    snapshot: KnowledgeSnapshot,
    guide: StyleGuide,
    llm: LLMClient,
    params: DraftParams,
    history: Sequence[Case] = (),
    retriever: SimilarCaseRetriever | None = None,
    now: datetime | None = None,
    replay: bool = False,
    created_at: datetime,
) -> DraftOutcome:
    """处理一个案件。

    - `history`：其他案件，用于确认重复提交（只看更早提交的线程）。
    - `retriever`：相似案件检索，语料应当只含“现在”之前的案件（`corpus_before`）。
    - `now` 为空时取系统时间；`replay` 为真时是历史重放，知识库只看当时已知的版本。
    """
    time_ctx = build_time_context(case, now=now, lookback_days=params.lookback_days)
    view = build_view(
        snapshot, time_ctx, form=case.form, known_at=known_at_for(time_ctx.now, replay=replay)
    )
    analysis = await analyze(case, view, llm, model=params.analyze_model, created_at=created_at)
    decision = decide_no_reply(case, analysis, corpus_before(history, case))
    if decision is not None and decision.auto:
        return DraftOutcome(
            time_context=time_ctx,
            analysis=analysis,
            no_reply=decision.proposal,
            no_reply_auto=True,
            no_reply_reason_zh=decision.reason_zh,
            proposed_status=CaseStatus.NO_REPLY_NEEDED,
            created_at=created_at,
        )

    candidates = view.candidates(analysis.intent)
    similar: list[tuple[SimilarCaseRef, list[str]]] = []
    foreign: list[str] = []
    if retriever is not None:
        corpus = {c.id: c for c in retriever.corpus}
        matcher = TemplateMatcher(view)
        for ref in await retriever.similar_to(case):
            other = corpus[ref.case_id]
            similar.append((ref, matcher.templates_used(other)))
            foreign.append(case_text(other))

    composition = await compose(
        case, analysis, view, guide, llm, candidates=candidates, similar=similar
    )
    provided = provided_entries(view)
    checks = run_checks(
        composition.draft,
        CheckContext(
            guide=guide,
            forbidden_promises=view.forbidden_promises,
            provided_entries=provided,
            foreign_texts=foreign,
        ),
    )
    back_translation = await back_translate(composition.draft.body_ja, llm, view.glossary)
    candidate = composition.draft.candidate
    decision_route, reasons = route(
        RouteInput(
            case=case,
            analysis=analysis,
            intent=view.intents.get(analysis.intent),
            candidate=candidate,
            checks=checks,
            draft_confidence=composition.confidence,
            escalation_triggers=view.escalation_triggers,
            min_confidence=params.min_confidence,
        )
    )
    if decision is not None:
        reasons.append(decision.reason_zh)
    draft = Draft(
        case_id=case.id,
        message_seq=analysis.message_seq,
        template_refs=(
            [
                TemplateRef(
                    template_id=candidate.template.id,
                    template_version=candidate.template.version,
                    reason_zh=composition.template_reason_zh,
                )
            ]
            if candidate is not None
            else []
        ),
        similar_case_refs=[ref for ref, _ in similar],
        body_ja=composition.draft.body_ja,
        back_translation_zh=back_translation,
        checks=checks,
        confidence=composition.confidence,
        route=decision_route,
        route_reasons=reasons,
        # 只记录引用了提供给模型的条目的引用；其他引用已在 citations 检查中报告。
        citations=[
            Citation(
                entry_type=c.entry_type,
                entry_id=c.entry_id,
                entry_version=c.entry_version,
                claim_zh=c.claim_zh,
            )
            for c in composition.draft.citations
            if (c.entry_type, c.entry_id, c.entry_version) in provided
        ],
        mode=DraftMode.PIPELINE,
        time_context=time_ctx,
        model=params.draft_model,
        created_at=created_at,
    )
    status = CaseStatus.ESCALATED if draft.route is Route.ESCALATE else CaseStatus.DRAFTED
    return DraftOutcome(
        time_context=time_ctx,
        analysis=analysis,
        draft=draft,
        no_reply=decision.proposal if decision else None,
        no_reply_reason_zh=decision.reason_zh if decision else None,
        proposed_status=status,
        created_at=created_at,
    )
