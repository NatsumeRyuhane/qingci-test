"""起草服务：一个案件从分析到分流的完整流水线（#6）。CLI（`copilot draft`）、界面（#8）、
评测（#7）以及之后的 API（#10）都调用 `draft_case`。

    时间上下文 → 可用知识 → 分析 → “不回复” → 候选模板 → 相似案件 → 起草 → 自检 → 回译 → 分流

起草有两种方式（#7 对比）：固定流水线（一次调用），或“流水线 + 工具”（`agent.compose_with_tools`，
失败时回退到固定流水线）。两种方式之后的自检和分流完全相同。

**每个草稿都经过 `run_checks` 和 `route`**（docs/claims.md C-27）：这是生成 `Draft` 的唯一
途径，`Draft` 的分流和理由只来自 `route`。“不回复”自动生效时不生成草稿。
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
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
    TimePoint,
)
from copilot.contracts.base import Contract
from copilot.draft.agent import DEFAULT_MAX_STEPS, compose_with_tools
from copilot.draft.analyze import analyze
from copilot.draft.checks import CheckContext, run_checks
from copilot.draft.compose import back_translate, compose, provided_entries
from copilot.draft.retrieval import KnowledgeSnapshot, KnowledgeView, build_view
from copilot.draft.route import RouteInput, decide_no_reply, route
from copilot.draft.similar import (
    SimilarCaseRetriever,
    _cosine,
    case_text,
    corpus_before,
    embed_cached,
)
from copilot.draft.timectx import DEFAULT_LOOKBACK_DAYS, build_time_context, known_at_for
from copilot.draft.tools import ToolBox
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
    tools: bool = False
    """为真时是“流水线 + 工具”（`DraftMode.PIPELINE_WITH_TOOLS`），否则是固定流水线。"""
    max_tool_steps: int = DEFAULT_MAX_STEPS
    template_top_k: int | None = None
    """非空时，用 embedding 从静态模板目录中检索前 K 个模板放进 prompt（#7 对比实验）；
    为空时把全部静态模板放进 prompt（默认）。限时模板不受影响。"""


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


async def _filter_static_templates(
    view: KnowledgeView, case: Case, retriever: SimilarCaseRetriever, top_k: int
) -> KnowledgeView:
    """用 embedding 从静态模板目录中筛选与本案件最相关的前 `top_k` 个模板（#7 对比实验）。"""
    embedder = retriever.embedder
    if embedder is None or top_k >= len(view.static_templates) or not view.static_templates:
        return view
    query = case_text(case)
    if not query:
        return view
    ids = list(view.static_templates)
    texts = [f"{t.title_zh}\n{t.use_when_zh}\n{t.body_ja}" for t in view.static_templates.values()]
    vectors = await embed_cached(embedder, retriever._cache, [query, *texts])
    ranked = sorted(
        ((tid, _cosine(vectors[0], vec)) for tid, vec in zip(ids, vectors[1:], strict=True)),
        key=lambda item: (-item[1], item[0]),
    )
    keep = {tid for tid, _ in ranked[:top_k]}
    return replace(
        view,
        static_templates={tid: t for tid, t in view.static_templates.items() if tid in keep},
    )


async def draft_case(
    case: Case,
    *,
    snapshot: KnowledgeSnapshot,
    guide: StyleGuide,
    llm: LLMClient,
    params: DraftParams,
    history: Sequence[Case] = (),
    retriever: SimilarCaseRetriever | None = None,
    now: TimePoint | datetime | None = None,
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

    # 相似案件检索不依赖分析结果，与分析并行以降低延迟（#7）。
    async def _retrieve_similar() -> list[SimilarCaseRef]:
        return await retriever.similar_to(case) if retriever is not None else []

    analysis, similar_refs = await asyncio.gather(
        analyze(case, view, llm, model=params.analyze_model, created_at=created_at),
        _retrieve_similar(),
    )
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

    draft_view = (
        await _filter_static_templates(view, case, retriever, params.template_top_k)
        if params.template_top_k is not None and retriever is not None
        else view
    )
    candidates = draft_view.candidates(analysis.intent)
    similar: list[tuple[SimilarCaseRef, list[str]]] = []
    foreign: list[str] = []
    matcher = TemplateMatcher(view)
    if retriever is not None:
        corpus = {c.id: c for c in retriever.corpus}
        for ref in similar_refs:
            other = corpus[ref.case_id]
            similar.append((ref, matcher.templates_used(other)))
            foreign.append(case_text(other))

    if params.tools:
        toolbox = ToolBox(
            case=case, view=draft_view, retriever=retriever, templates_used=matcher.templates_used
        )
        composition = await compose_with_tools(
            case,
            analysis,
            draft_view,
            guide,
            llm,
            toolbox=toolbox,
            candidates=candidates,
            similar=similar,
            max_steps=params.max_tool_steps,
        )
    else:
        composition = await compose(
            case, analysis, draft_view, guide, llm, candidates=candidates, similar=similar
        )
    provided = provided_entries(draft_view) | composition.tool_entries
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
        mode=DraftMode.PIPELINE_WITH_TOOLS if composition.tools_used else DraftMode.PIPELINE,
        trace=list(composition.trace),
        fallback_reason=composition.fallback_reason,
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
