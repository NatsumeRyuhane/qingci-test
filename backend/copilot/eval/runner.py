"""批量并发评测执行器（#7）。

在留出集（dev 或 test）上并发运行 `draft_case` 和可选的 `judge_draft`，按案件隔离用量与延迟，
支持断点续跑（JSONL 缓存）。
"""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from copilot.contracts import Case, DraftMode, RiskLevel, Route
from copilot.draft.retrieval import KnowledgeSnapshot, build_view
from copilot.draft.route import RouteInput, approve_auto_send
from copilot.draft.service import DraftParams, draft_case
from copilot.draft.similar import EmbeddingCache, SimilarCaseRetriever
from copilot.draft.timectx import build_time_context, known_at_for
from copilot.eval.judge import judge_draft
from copilot.eval.metrics import CaseEvalRecord, append_record, read_records
from copilot.eval.replay import NoReplyEvalItem, ReplyEvalItem, replay_corpus
from copilot.ingest.timestamps import JST
from copilot.kb.knowledge import StyleGuide
from copilot.llm import LLMClient, OpenAICompatibleClient
from copilot.llm.embedding import EmbeddingClient


@dataclass(frozen=True)
class EvalRunConfig:
    """一次批量评测的运行参数。"""

    params: DraftParams
    concurrency: int = 4
    strict_replay: bool = True
    """为真时按 `known_from` 过滤知识库版本（D-012）；为假时不过滤（docs/claims.md A-12 对比）。"""
    run_judge: bool = True
    cache_path: Path | None = None
    """JSONL 缓存文件。存在时跳过已完成的样本，每完成一条立即追加写入。"""


def _scoped_client(llm: LLMClient) -> LLMClient:
    if isinstance(llm, OpenAICompatibleClient):
        return llm.with_usage()
    return llm


def _template_lookup(snapshot: KnowledgeSnapshot) -> dict[str, tuple[str, RiskLevel]]:
    """模板 ID -> (最新版本的 intent, risk)，用于对照标准答案计算意图准确率与高风险标记。"""
    latest: dict[str, tuple[int, str, RiskLevel]] = {}
    for t in snapshot.templates:
        prev = latest.get(t.id)
        if prev is None or t.version > prev[0]:
            latest[t.id] = (t.version, t.intent, t.risk)
    return {tid: (intent, risk) for tid, (_, intent, risk) in latest.items()}


def _top3_templates(chosen_id: str | None, candidate_ids: Sequence[str]) -> list[str]:
    result: list[str] = []
    if chosen_id is not None:
        result.append(chosen_id)
    for tid in candidate_ids:
        if tid not in result:
            result.append(tid)
        if len(result) >= 3:
            break
    return result


async def evaluate_reply_item(
    item: ReplyEvalItem,
    *,
    snapshot: KnowledgeSnapshot,
    guide: StyleGuide,
    llm: LLMClient,
    train_cases: Sequence[Case],
    all_cases: Sequence[Case],
    embedder: EmbeddingClient | None,
    embedding_cache: EmbeddingCache | None,
    config: EvalRunConfig,
    template_meta: dict[str, tuple[str, RiskLevel]],
) -> CaseEvalRecord:
    """评测单个历史回复样本。"""
    case_llm = _scoped_client(llm)
    time_ctx = build_time_context(
        item.case, now=item.now, lookback_days=config.params.lookback_days
    )
    known_at = known_at_for(time_ctx.now, replay=config.strict_replay)
    view = build_view(snapshot, time_ctx, form=item.case.form, known_at=known_at)

    expected_tid = item.label.template_id
    expected_visible = view.template(expected_tid) is not None if expected_tid is not None else None
    expected_intent, expected_risk = (
        template_meta.get(expected_tid, (None, None)) if expected_tid is not None else (None, None)
    )

    corpus = replay_corpus(train_cases, item.case, item.now)
    retriever = SimilarCaseRetriever(corpus, embedder=embedder, cache=embedding_cache)

    started = time.perf_counter()
    try:
        outcome = await draft_case(
            item.case,
            snapshot=snapshot,
            guide=guide,
            llm=case_llm,
            params=config.params,
            history=all_cases,
            retriever=retriever,
            now=item.now,
            replay=config.strict_replay,
            created_at=datetime.now(JST).replace(microsecond=0),
        )
    except Exception as exc:
        return CaseEvalRecord(
            kind="reply",
            case_id=item.case.id,
            message_id=item.label.message_id,
            split=item.label.split,
            evidence=item.label.evidence,
            first_agent_reply=item.label.first_agent_reply,
            resolution=item.label.resolution.value,
            now_status=time_ctx.now.status,
            problem_time_status=time_ctx.problem_time.status,
            mode=DraftMode.PIPELINE_WITH_TOOLS if config.params.tools else DraftMode.PIPELINE,
            expected_category=item.case.category,
            expected_intent=expected_intent,
            expected_template_id=expected_tid,
            expected_template_visible=expected_visible,
            expected_template_risk=expected_risk,
            wall_seconds=round(time.perf_counter() - started, 3),
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
    wall = time.perf_counter() - started

    analysis = outcome.analysis
    draft = outcome.draft
    chosen_id = draft.template_refs[0].template_id if draft and draft.template_refs else None
    candidates = view.candidates(analysis.intent)
    top3 = _top3_templates(chosen_id, [c.template.id for c in candidates])

    failed_checks = [c.kind for c in draft.checks if not c.passed] if draft else []
    failed_gates = []
    if draft is not None:
        cand = view.candidate(chosen_id, analysis.intent) if chosen_id else None
        approval = approve_auto_send(
            RouteInput(
                case=item.case,
                analysis=analysis,
                intent=view.intents.get(analysis.intent),
                candidate=cand,
                checks=draft.checks,
                draft_confidence=draft.confidence,
                escalation_triggers=view.escalation_triggers,
                min_confidence=config.params.min_confidence,
            )
        )
        failed_gates = [g for g, reasons in approval.results.items() if reasons]

    intent_obj = view.intents.get(analysis.intent)
    high_risk = (
        expected_risk is RiskLevel.HIGH
        or (intent_obj is not None and intent_obj.default_risk is RiskLevel.HIGH)
        or (draft is not None and draft.route is Route.ESCALATE)
    )

    # 先记录起草阶段（不含 judge）的用量和耗时
    step_seconds = {task.value: round(u.seconds, 3) for task, u in case_llm.usage.tasks.items()}
    draft_total = case_llm.usage.total()

    verdict = None
    if config.run_judge and draft is not None:
        judge_llm = _scoped_client(llm)
        try:
            verdict = await judge_draft(item.case, draft, item.actual_reply.body, judge_llm)
        except Exception:
            verdict = None

    return CaseEvalRecord(
        kind="reply",
        case_id=item.case.id,
        message_id=item.label.message_id,
        split=item.label.split,
        evidence=item.label.evidence,
        first_agent_reply=item.label.first_agent_reply,
        resolution=item.label.resolution.value,
        now_status=outcome.time_context.now.status,
        problem_time_status=outcome.time_context.problem_time.status,
        mode=draft.mode
        if draft
        else (DraftMode.PIPELINE_WITH_TOOLS if config.params.tools else DraftMode.PIPELINE),
        expected_category=item.case.category,
        predicted_category=analysis.category,
        expected_intent=expected_intent,
        predicted_intent=analysis.intent,
        expected_template_id=expected_tid,
        expected_template_visible=expected_visible,
        expected_template_risk=expected_risk,
        chosen_template_id=chosen_id,
        top3_template_ids=top3,
        route=draft.route if draft else None,
        no_reply_auto=outcome.no_reply_auto,
        predicted_no_reply_reason=outcome.no_reply.reason if outcome.no_reply else None,
        intent_confidence=analysis.intent_confidence,
        draft_confidence=draft.confidence if draft else None,
        failed_checks=failed_checks,
        failed_gates=failed_gates,
        high_risk=high_risk,
        fallback_reason=draft.fallback_reason if draft else None,
        tool_calls=len(draft.trace) if draft else 0,
        judge=verdict,
        wall_seconds=round(wall, 3),
        step_seconds=step_seconds,
        prompt_tokens=draft_total.prompt_tokens,
        completion_tokens=draft_total.completion_tokens,
    )


async def evaluate_no_reply_item(
    item: NoReplyEvalItem,
    *,
    snapshot: KnowledgeSnapshot,
    guide: StyleGuide,
    llm: LLMClient,
    train_cases: Sequence[Case],
    all_cases: Sequence[Case],
    embedder: EmbeddingClient | None,
    embedding_cache: EmbeddingCache | None,
    config: EvalRunConfig,
) -> CaseEvalRecord:
    """评测单个“不回复”样本。"""
    case_llm = _scoped_client(llm)
    time_ctx = build_time_context(
        item.case, now=item.now, lookback_days=config.params.lookback_days
    )
    corpus = replay_corpus(train_cases, item.case, item.now)
    retriever = SimilarCaseRetriever(corpus, embedder=embedder, cache=embedding_cache)

    started = time.perf_counter()
    try:
        outcome = await draft_case(
            item.case,
            snapshot=snapshot,
            guide=guide,
            llm=case_llm,
            params=config.params,
            history=all_cases,
            retriever=retriever,
            now=item.now,
            replay=config.strict_replay,
            created_at=datetime.now(JST).replace(microsecond=0),
        )
    except Exception as exc:
        return CaseEvalRecord(
            kind="no_reply",
            case_id=item.case.id,
            split=item.label.split,
            now_status=time_ctx.now.status,
            problem_time_status=time_ctx.problem_time.status,
            expected_no_reply_reason=item.label.reason,
            wall_seconds=round(time.perf_counter() - started, 3),
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
    wall = time.perf_counter() - started
    step_seconds = {task.value: round(u.seconds, 3) for task, u in case_llm.usage.tasks.items()}
    draft_total = case_llm.usage.total()
    draft = outcome.draft
    return CaseEvalRecord(
        kind="no_reply",
        case_id=item.case.id,
        split=item.label.split,
        now_status=outcome.time_context.now.status,
        problem_time_status=outcome.time_context.problem_time.status,
        mode=draft.mode
        if draft
        else (DraftMode.PIPELINE_WITH_TOOLS if config.params.tools else DraftMode.PIPELINE),
        route=draft.route if draft else None,
        no_reply_auto=outcome.no_reply_auto,
        expected_no_reply_reason=item.label.reason,
        predicted_no_reply_reason=outcome.no_reply.reason if outcome.no_reply else None,
        wall_seconds=round(wall, 3),
        step_seconds=step_seconds,
        prompt_tokens=draft_total.prompt_tokens,
        completion_tokens=draft_total.completion_tokens,
    )


async def run_eval(
    reply_items: Sequence[ReplyEvalItem],
    no_reply_items: Sequence[NoReplyEvalItem],
    *,
    snapshot: KnowledgeSnapshot,
    guide: StyleGuide,
    llm: LLMClient,
    train_cases: Sequence[Case],
    all_cases: Sequence[Case],
    embedder: EmbeddingClient | None = None,
    embedding_cache: EmbeddingCache | None = None,
    config: EvalRunConfig,
) -> list[CaseEvalRecord]:
    """并发运行所有待评测样本，支持 JSONL 缓存续跑。"""
    existing: dict[tuple[str, str, str | None], CaseEvalRecord] = {}
    if config.cache_path is not None and config.cache_path.is_file():
        for rec in read_records(config.cache_path):
            if rec.error is None:
                existing[(rec.kind, rec.case_id, rec.message_id)] = rec

    template_meta = _template_lookup(snapshot)
    sem = asyncio.Semaphore(max(1, config.concurrency))
    write_lock = asyncio.Lock()

    async def _run_reply(item: ReplyEvalItem) -> CaseEvalRecord:
        key = ("reply", item.case.id, item.label.message_id)
        if key in existing:
            return existing[key]
        async with sem:
            rec = await evaluate_reply_item(
                item,
                snapshot=snapshot,
                guide=guide,
                llm=llm,
                train_cases=train_cases,
                all_cases=all_cases,
                embedder=embedder,
                embedding_cache=embedding_cache,
                config=config,
                template_meta=template_meta,
            )
        if config.cache_path is not None:
            async with write_lock:
                append_record(rec, config.cache_path)
        return rec

    async def _run_no_reply(item: NoReplyEvalItem) -> CaseEvalRecord:
        key = ("no_reply", item.case.id, None)
        if key in existing:
            return existing[key]
        async with sem:
            rec = await evaluate_no_reply_item(
                item,
                snapshot=snapshot,
                guide=guide,
                llm=llm,
                train_cases=train_cases,
                all_cases=all_cases,
                embedder=embedder,
                embedding_cache=embedding_cache,
                config=config,
            )
        if config.cache_path is not None:
            async with write_lock:
                append_record(rec, config.cache_path)
        return rec

    tasks = [
        *(_run_reply(item) for item in reply_items),
        *(_run_no_reply(item) for item in no_reply_items),
    ]
    return list(await asyncio.gather(*tasks))
