"""批量并发评测执行器（#7）。

在留出集（dev 或 test）上并发运行 `draft_case` 和可选的 `judge_draft`，按案件隔离用量与延迟，
支持带配置指纹的断点续跑（JSONL 缓存）。
"""

import asyncio
import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from copilot.contracts import (
    Case,
    Citation,
    DraftMode,
    EntryType,
    InquiryCategory,
    RiskLevel,
)
from copilot.draft.retrieval import KnowledgeSnapshot, build_view
from copilot.draft.route import RouteInput, approve_auto_send, escalation_reasons
from copilot.draft.service import DraftParams, draft_case
from copilot.draft.similar import EmbeddingCache, SimilarCaseRetriever
from copilot.draft.timectx import build_time_context, known_at_for
from copilot.eval.judge import judge_draft
from copilot.eval.metrics import CaseEvalRecord, append_record, read_records
from copilot.eval.replay import NoReplyEvalItem, ReplyEvalItem, replay_corpus, replay_history
from copilot.ingest.pipeline import thread_order
from copilot.ingest.timestamps import JST
from copilot.kb.knowledge import StyleGuide
from copilot.llm import LLMClient, OpenAICompatibleClient
from copilot.llm.embedding import EmbeddingClient

HIGH_RISK_CATEGORIES = {InquiryCategory.PURCHASE, InquiryCategory.DATA_TRANSFER}


@dataclass(frozen=True)
class EvalRunConfig:
    """一次批量评测的运行参数。"""

    params: DraftParams
    concurrency: int = 4
    strict_replay: bool = True
    """为真时按 `known_from` 过滤知识库版本（D-012）；为假时不过滤（docs/claims.md A-12 对比）。"""
    run_judge: bool = True
    cache_path: Path | None = None
    """JSONL 缓存文件。存在时只复用同配置指纹的已完成样本，每完成一条立即追加写入。"""
    service_tier: str | None = None
    backtranslate_model: str | None = None
    judge_model: str | None = None

    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "analyze_model": self.params.analyze_model,
                "draft_model": self.params.draft_model,
                "backtranslate_model": self.backtranslate_model,
                "judge_model": self.judge_model if self.run_judge else None,
                "service_tier": self.service_tier,
                "min_confidence": self.params.min_confidence,
                "lookback_days": self.params.lookback_days,
                "tools": self.params.tools,
                "max_tool_steps": self.params.max_tool_steps,
                "template_top_k": self.params.template_top_k,
                "strict_replay": self.strict_replay,
                "run_judge": self.run_judge,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _snapshot_digest(snapshot: KnowledgeSnapshot) -> str:
    h = hashlib.sha256()
    for name, entries in (
        ("templates", snapshot.templates),
        ("intents", snapshot.intents),
        ("policies", snapshot.policies),
        ("notices", snapshot.notices),
        ("facts", snapshot.facts),
        ("glossary", snapshot.glossary),
    ):
        h.update(name.encode())
        for item in sorted(entries, key=lambda e: (e.id, e.version)):
            h.update(item.model_dump_json().encode())
    return h.hexdigest()


def _cases_digest(cases: Sequence[Case]) -> str:
    h = hashlib.sha256()
    for c in sorted(cases, key=lambda c: thread_order(c.id)):
        h.update(c.model_dump_json().encode())
    return h.hexdigest()


def _run_fingerprint(
    config: EvalRunConfig,
    *,
    snapshot: KnowledgeSnapshot,
    guide: StyleGuide,
    train_cases: Sequence[Case],
    all_cases: Sequence[Case],
    embedder: EmbeddingClient | None,
) -> str:
    payload = json.dumps(
        {
            "config": config.fingerprint(),
            "embedding_namespace": embedder.cache_namespace if embedder is not None else None,
            "snapshot": _snapshot_digest(snapshot),
            "guide": hashlib.sha256(guide.model_dump_json().encode()).hexdigest(),
            "train_cases": _cases_digest(train_cases),
            "all_cases": _cases_digest(all_cases),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _item_fingerprint(item: ReplyEvalItem | NoReplyEvalItem) -> str:
    if isinstance(item, ReplyEvalItem):
        payload = json.dumps(
            {
                "kind": "reply",
                "label": item.label.model_dump(mode="json"),
                "case": item.case.model_dump(mode="json"),
                "actual_reply": item.actual_reply.model_dump(mode="json"),
                "before_seq": item.before_seq,
                "now": item.now.model_dump(mode="json"),
            },
            sort_keys=True,
        )
    else:
        payload = json.dumps(
            {
                "kind": "no_reply",
                "label": item.label.model_dump(mode="json"),
                "case": item.case.model_dump(mode="json"),
                "now": item.now.model_dump(mode="json"),
            },
            sort_keys=True,
        )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _scoped_client(llm: LLMClient) -> tuple[LLMClient, bool]:
    """返回（隔离了 UsageMeter 的客户端, 是否实际隔离）。"""
    if isinstance(llm, OpenAICompatibleClient):
        return llm.with_usage(), True
    with_usage = getattr(llm, "with_usage", None)
    if callable(with_usage):
        return cast(LLMClient, with_usage()), True
    return llm, False


def _template_lookup(
    snapshot: KnowledgeSnapshot,
) -> tuple[dict[tuple[str, int], tuple[str, RiskLevel]], dict[str, tuple[str, RiskLevel]]]:
    """返回（(id, version) -> (intent, risk)，id -> 最新版本 (intent, risk)）。"""
    by_ver: dict[tuple[str, int], tuple[str, RiskLevel]] = {}
    latest: dict[str, tuple[int, str, RiskLevel]] = {}
    for t in snapshot.templates:
        by_ver[(t.id, t.version)] = (t.intent, t.risk)
        prev = latest.get(t.id)
        if prev is None or t.version > prev[0]:
            latest[t.id] = (t.version, t.intent, t.risk)
    return by_ver, {tid: (intent, risk) for tid, (_, intent, risk) in latest.items()}


def summarize_citations(citations: Sequence[Citation], snapshot: KnowledgeSnapshot) -> list[str]:
    """把草稿引用的条目还原成简短中文摘要，供 LLM 评审检查引用是否成立（docs/security.md H-03）。"""
    if not citations:
        return []
    index: dict[tuple[EntryType, str, int], str] = {}
    for t in snapshot.templates:
        avoid = f"；不要使用：{t.avoid_when_zh}" if t.avoid_when_zh else ""
        index[(EntryType.TEMPLATE, t.id, t.version)] = (
            f"模板《{t.title_zh}》（意图 {t.intent}）：适用条件：{t.use_when_zh}{avoid}；"
            f"正文：\n{t.body_ja}"
        )
    for i in snapshot.intents:
        req = f"；需要索要：{'、'.join(i.required_info_zh)}" if i.required_info_zh else ""
        index[(EntryType.INTENT, i.id, i.version)] = (
            f"意图《{i.title_zh}》：{i.description_zh}{req}"
        )
    for n in snapshot.notices:
        index[(EntryType.NOTICE, n.id, n.version)] = f"公告《{n.title_zh}》：{n.description_zh}"
    for p in snapshot.policies:
        index[(EntryType.POLICY, p.id, p.version)] = f"政策《{p.title_zh}》：{p.description_zh}"
    for f in snapshot.facts:
        index[(EntryType.FACT, f.id, f.version)] = f"事实：{f.statement_zh}"
    for g in snapshot.glossary:
        index[(EntryType.GLOSSARY, g.id, g.version)] = f"术语：{g.term_ja} = {g.term_zh}"
    out = []
    for c in citations:
        key = (c.entry_type, c.entry_id, c.entry_version)
        desc = index.get(key, "（知识库中未找到该条目）")
        out.append(f"- [{c.entry_type.value}:{c.entry_id} v{c.entry_version}] {desc}")
    return out


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
    template_by_ver: dict[tuple[str, int], tuple[str, RiskLevel]],
    template_latest: dict[str, tuple[str, RiskLevel]],
    fingerprint: str,
    item_fingerprint: str | None = None,
) -> CaseEvalRecord:
    """评测单个历史回复样本。"""
    case_llm, usage_isolated = _scoped_client(llm)
    item_fp = item_fingerprint or _item_fingerprint(item)
    time_ctx = build_time_context(
        item.case, now=item.now, lookback_days=config.params.lookback_days
    )
    known_at = known_at_for(time_ctx.now, replay=config.strict_replay)
    view = build_view(snapshot, time_ctx, form=item.case.form, known_at=known_at)

    expected_tid = item.label.template_id
    if expected_tid is not None:
        visible_tpl = view.template(expected_tid)
        expected_visible: bool | None = visible_tpl is not None and (
            item.label.template_version is None
            or visible_tpl.version == item.label.template_version
        )
    else:
        expected_visible = None
    if expected_tid is not None and item.label.template_version is not None:
        expected_intent, expected_risk = template_by_ver.get(
            (expected_tid, item.label.template_version),
            template_latest.get(expected_tid, (None, None)),
        )
    elif expected_tid is not None:
        expected_intent, expected_risk = template_latest.get(expected_tid, (None, None))
    else:
        expected_intent, expected_risk = (None, None)

    corpus = replay_corpus(train_cases, item.case, item.now)
    history = replay_history(all_cases, item.case, item.now)
    retriever = SimilarCaseRetriever(corpus, embedder=embedder, cache=embedding_cache)

    started = time.perf_counter()
    try:
        outcome = await draft_case(
            item.case,
            snapshot=snapshot,
            guide=guide,
            llm=case_llm,
            params=config.params,
            history=history,
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
            config_fingerprint=fingerprint,
            item_fingerprint=item_fp,
            wall_seconds=round(time.perf_counter() - started, 3),
            error=type(exc).__name__,
        )
    wall = time.perf_counter() - started

    analysis = outcome.analysis
    draft = outcome.draft
    chosen_id = draft.template_refs[0].template_id if draft and draft.template_refs else None
    # 使用 draft_case 实际提供给模型的候选列表（含 template_top_k 过滤结果）；
    # 若误判为 no_reply_auto（无草稿），不计入模板命中。
    top3 = _top3_templates(chosen_id, outcome.candidate_template_ids) if draft is not None else []

    failed_checks = [c.kind for c in draft.checks if not c.passed] if draft else []
    failed_gates = []
    chosen_risk: RiskLevel | None = None
    if draft is not None:
        cand = view.candidate(chosen_id, analysis.intent) if chosen_id else None
        if cand is not None:
            chosen_risk = cand.template.risk
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

    # 高风险判定只依据固有风险/标准答案/政策触发词，不依赖模型是否输出 ESCALATE
    expected_intent_obj = view.intents.get(expected_intent) if expected_intent else None
    high_risk = (
        expected_risk is RiskLevel.HIGH
        or chosen_risk is RiskLevel.HIGH
        or (expected_intent_obj is not None and expected_intent_obj.default_risk is RiskLevel.HIGH)
        or item.case.category in HIGH_RISK_CATEGORIES
        or bool(escalation_reasons(item.case, analysis, view.escalation_triggers))
    )

    if usage_isolated:
        step_seconds = {task.value: round(u.seconds, 3) for task, u in case_llm.usage.tasks.items()}
        draft_total = case_llm.usage.total()
        prompt_tokens = draft_total.prompt_tokens
        completion_tokens = draft_total.completion_tokens
    else:
        step_seconds = {}
        prompt_tokens = None
        completion_tokens = None

    verdict = None
    judge_error: str | None = None
    if config.run_judge and draft is not None:
        judge_llm, _ = _scoped_client(llm)
        try:
            verdict = await judge_draft(
                item.case,
                draft,
                item.actual_reply.body,
                judge_llm,
                cited_summaries=summarize_citations(draft.citations, snapshot),
            )
        except Exception as exc:
            judge_error = type(exc).__name__

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
        judge=verdict.scores() if verdict is not None else None,
        judge_error=judge_error,
        config_fingerprint=fingerprint,
        item_fingerprint=item_fp,
        wall_seconds=round(wall, 3),
        step_seconds=step_seconds,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
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
    fingerprint: str,
    item_fingerprint: str | None = None,
) -> CaseEvalRecord:
    """评测单个“不回复”样本。"""
    case_llm, usage_isolated = _scoped_client(llm)
    item_fp = item_fingerprint or _item_fingerprint(item)
    time_ctx = build_time_context(
        item.case, now=item.now, lookback_days=config.params.lookback_days
    )
    corpus = replay_corpus(train_cases, item.case, item.now)
    history = replay_history(all_cases, item.case, item.now)
    retriever = SimilarCaseRetriever(corpus, embedder=embedder, cache=embedding_cache)

    started = time.perf_counter()
    try:
        outcome = await draft_case(
            item.case,
            snapshot=snapshot,
            guide=guide,
            llm=case_llm,
            params=config.params,
            history=history,
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
            config_fingerprint=fingerprint,
            item_fingerprint=item_fp,
            wall_seconds=round(time.perf_counter() - started, 3),
            error=type(exc).__name__,
        )
    wall = time.perf_counter() - started
    if usage_isolated:
        step_seconds = {task.value: round(u.seconds, 3) for task, u in case_llm.usage.tasks.items()}
        draft_total = case_llm.usage.total()
        prompt_tokens = draft_total.prompt_tokens
        completion_tokens = draft_total.completion_tokens
    else:
        step_seconds = {}
        prompt_tokens = None
        completion_tokens = None
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
        config_fingerprint=fingerprint,
        item_fingerprint=item_fp,
        wall_seconds=round(wall, 3),
        step_seconds=step_seconds,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
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
    """并发运行所有待评测样本，支持同配置与同输入指纹的 JSONL 缓存续跑。"""
    fingerprint = _run_fingerprint(
        config,
        snapshot=snapshot,
        guide=guide,
        train_cases=train_cases,
        all_cases=all_cases,
        embedder=embedder,
    )
    existing: dict[tuple[str, str, str | None, str], CaseEvalRecord] = {}
    if config.cache_path is not None and config.cache_path.is_file():
        for rec in read_records(config.cache_path):
            if (
                rec.error is None
                and rec.judge_error is None
                and rec.config_fingerprint == fingerprint
                and rec.item_fingerprint is not None
            ):
                existing[(rec.kind, rec.case_id, rec.message_id, rec.item_fingerprint)] = rec

    template_by_ver, template_latest = _template_lookup(snapshot)
    sem = asyncio.Semaphore(max(1, config.concurrency))
    write_lock = asyncio.Lock()

    async def _run_reply(item: ReplyEvalItem) -> CaseEvalRecord:
        item_fp = _item_fingerprint(item)
        key = ("reply", item.case.id, item.label.message_id, item_fp)
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
                template_by_ver=template_by_ver,
                template_latest=template_latest,
                fingerprint=fingerprint,
                item_fingerprint=item_fp,
            )
        if config.cache_path is not None:
            async with write_lock:
                append_record(rec, config.cache_path)
        return rec

    async def _run_no_reply(item: NoReplyEvalItem) -> CaseEvalRecord:
        item_fp = _item_fingerprint(item)
        key = ("no_reply", item.case.id, None, item_fp)
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
                fingerprint=fingerprint,
                item_fingerprint=item_fp,
            )
        if config.cache_path is not None:
            async with write_lock:
                append_record(rec, config.cache_path)
        return rec

    task_objs = [
        *(asyncio.create_task(_run_reply(item)) for item in reply_items),
        *(asyncio.create_task(_run_no_reply(item)) for item in no_reply_items),
    ]
    try:
        return list(await asyncio.gather(*task_objs))
    except BaseException:
        for task in task_objs:
            if not task.done():
                task.cancel()
        await asyncio.gather(*task_objs, return_exceptions=True)
        raise
