"""离线评测核心模块测试（replay、judge、metrics、runner）。SYNTHETIC：全部是合成数据。"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from copilot.config import LLMTask
from copilot.contracts import (
    CaseStatus,
    DraftCheckKind,
    DraftMode,
    InquiryCategory,
    NoReplyReason,
    RiskLevel,
    RoleEvidenceKind,
    RoleEvidenceStrength,
    Route,
    TimeStatus,
)
from copilot.draft.service import DraftParams
from copilot.eval import (
    CaseEvalRecord,
    EvalRunConfig,
    JudgeScores,
    JudgeVerdict,
    Split,
    TimelineIndex,
    build_no_reply_items,
    build_reply_items,
    compute_latency,
    compute_no_reply,
    compute_slice,
    judge_draft,
    percentile,
    read_records,
    render_markdown,
    replay_corpus,
    run_eval,
    split_cases,
    truncate_case,
    write_records,
)
from copilot.eval.metrics import append_record
from copilot.eval.replay import replay_history
from copilot.eval.runner import summarize_citations
from copilot.ingest.timestamps import JST
from copilot.kb.cluster import MatchKind
from copilot.kb.knowledge import load_knowledge
from copilot.kb.labels import NoReplyLabel, ReplyLabel, Resolution
from copilot.llm.fake import FakeLLMClient
from tests.cases import agent, case, player
from tests.draft.test_service import (
    KNOWLEDGE,
    SNAPSHOT,
    THANKS_BODY,
    analysis_reply,
    draft_reply,
)

pytestmark = pytest.mark.anyio

T1 = datetime(2024, 3, 2, 10, 0, tzinfo=JST)
T2 = datetime(2024, 3, 5, 12, 0, tzinfo=JST)
T3 = datetime(2024, 3, 10, 15, 0, tzinfo=JST)


def _verdict_json(**overrides: object) -> str:
    data: dict[str, object] = {
        "solution_agrees": True,
        "register_appropriate": True,
        "no_fabricated_promise": True,
        "back_translation_faithful": True,
        "citations_supported": True,
        "no_other_case_leak": True,
        "notes_zh": "【合成】草稿与真实回复一致，语体得体。",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_timeline_estimate_now_known_and_interpolated() -> None:
    c1 = case(
        "1",
        player("1", 1, "【合成】a"),
        agent("1", 2, "【合成】r1").model_copy(update={"sent_at": T1}),
    )
    c2 = case("2", player("2", 1, "【合成】b"), agent("2", 2, "【合成】r2"))
    c3 = case(
        "3",
        player("3", 1, "【合成】c"),
        agent("3", 2, "【合成】r3").model_copy(update={"sent_at": T3}),
    )
    tl = TimelineIndex([c1, c2, c3])

    known = tl.estimate_now(c1, target_seq=2)
    assert known.status is TimeStatus.KNOWN and known.value == T1

    est = tl.estimate_now(c2, target_seq=2)
    assert est.status is TimeStatus.ESTIMATED
    assert est.earliest == T1 and est.latest == T3


def test_truncate_case_and_replay_corpus_strip_future() -> None:
    c1 = case(
        "1",
        player("1", 1, "【合成】p1").model_copy(update={"sent_at": T1}),
        agent("1", 2, "【合成】a1").model_copy(update={"sent_at": T3}),
    )
    c2 = case("2", player("2", 1, "【合成】p2"), agent("2", 2, "【合成】a2"))
    truncated = truncate_case(c2, before_seq=2)
    assert [m.seq for m in truncated.messages] == [1]
    assert truncated.status is CaseStatus.OPEN

    tl = TimelineIndex([c1, c2])
    c2_known = case(
        "2",
        player("2", 1, "【合成】p2"),
        agent("2", 2, "【合成】a2").model_copy(update={"sent_at": T2}),
    )
    now = tl.estimate_now(c2_known, target_seq=2)
    corpus = replay_corpus([c1, c2], c2, now)
    assert len(corpus) == 1 and corpus[0].id == "1"
    # c1 的第 2 封邮件 sent_at=T3 晚于 T2，必须被裁掉（H-11 / A-07）
    assert [m.seq for m in corpus[0].messages] == [1]


def test_metrics_and_markdown_contain_only_aggregates(tmp_path: Path) -> None:
    rec1 = CaseEvalRecord(
        kind="reply",
        case_id="10",
        message_id="10:2",
        split=Split.DEV,
        evidence=RoleEvidenceStrength.STRONG,
        first_agent_reply=True,
        resolution="resolved",
        now_status=TimeStatus.KNOWN,
        problem_time_status=TimeStatus.UNAVAILABLE,
        mode=DraftMode.PIPELINE,
        expected_category=InquiryCategory.FEEDBACK,
        predicted_category=InquiryCategory.FEEDBACK,
        expected_intent="feedback_thanks",
        predicted_intent="feedback_thanks",
        expected_template_id="tpl-thanks",
        expected_template_visible=True,
        expected_template_risk=RiskLevel.LOW,
        chosen_template_id="tpl-thanks",
        top3_template_ids=["tpl-thanks"],
        route=Route.AUTO_SEND,
        draft_confidence=0.95,
        intent_confidence=0.96,
        judge=JudgeVerdict.model_validate_json(_verdict_json()),
        wall_seconds=12.5,
        step_seconds={"analyze": 4.0, "draft": 6.0, "backtranslate": 2.0},
        prompt_tokens=1000,
        completion_tokens=200,
    )
    rec2 = CaseEvalRecord(
        kind="no_reply",
        case_id="11",
        split=Split.DEV,
        now_status=TimeStatus.ESTIMATED,
        problem_time_status=TimeStatus.UNAVAILABLE,
        no_reply_auto=True,
        expected_no_reply_reason=NoReplyReason.SYSTEM_MESSAGE,
        predicted_no_reply_reason=NoReplyReason.SYSTEM_MESSAGE,
        wall_seconds=3.0,
        step_seconds={"analyze": 3.0},
        prompt_tokens=400,
        completion_tokens=80,
    )

    path = tmp_path / "records.jsonl"
    write_records([rec1, rec2], path)
    loaded = read_records(path)
    assert loaded == [rec1, rec2]

    sl = compute_slice([rec1])
    assert sl.combined_success_hits == 1 and sl.template_top1_hits == 1
    assert sl.high_risk_auto_send == 0

    nr = compute_no_reply([rec2])
    assert nr.auto_hits == 1 and nr.by_reason["system_message"] == (1, 1, 1)

    lat = compute_latency([rec1, rec2])
    assert lat.count == 2 and lat.wall_p50 == pytest.approx(7.75)
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)

    md = render_markdown([rec1, rec2], title="合成测试报告", meta={"切分": "dev"})
    assert "合成测试报告" in md
    assert "模板 Top-3" in md
    assert "延迟与成本" in md
    assert "不回复" in md


async def test_run_eval_end_to_end_and_cache_resume(tmp_path: Path) -> None:
    guide = load_knowledge(KNOWLEDGE).style_guide
    cases = [
        case(
            str(i),
            player(str(i), 1, f"【合成】ありがとう {i}"),
            agent(
                str(i),
                2,
                THANKS_BODY,
                RoleEvidenceKind.METADATA,
            ).model_copy(update={"sent_at": T2}),
            content=f"【合成】ありがとう {i}",
        )
        for i in range(1, 11)
    ]
    manifest = split_cases(cases)
    dev_cases = manifest.select(cases, Split.DEV)
    assert dev_cases
    target = dev_cases[0]
    reply_labels = [
        ReplyLabel(
            message_id=f"{target.id}:2",
            split=Split.DEV,
            evidence=RoleEvidenceStrength.STRONG,
            template_id="tpl-syn-thanks",
            template_version=1,
            match=MatchKind.EXACT,
            resolution=Resolution.RESOLVED,
            inquiry_message_id=f"{target.id}:1",
            first_agent_reply=True,
        )
    ]
    no_reply_labels: list[NoReplyLabel] = []
    reply_items = build_reply_items(cases, manifest, Split.DEV, reply_labels)
    no_reply_items = build_no_reply_items(cases, manifest, Split.DEV, no_reply_labels)
    assert len(reply_items) == 1

    fake = FakeLLMClient()
    fake.queue(LLMTask.ANALYZE, analysis_reply())
    fake.queue(LLMTask.DRAFT, draft_reply())
    fake.queue(LLMTask.BACKTRANSLATE, "【合成】感谢反馈")
    fake.queue(LLMTask.JUDGE, _verdict_json())

    cache = tmp_path / "eval_cache.jsonl"
    config = EvalRunConfig(
        params=DraftParams(analyze_model="fake", draft_model="fake"),
        concurrency=2,
        strict_replay=False,
        run_judge=True,
        cache_path=cache,
    )
    records = await run_eval(
        reply_items,
        no_reply_items,
        snapshot=SNAPSHOT,
        guide=guide,
        llm=fake,
        train_cases=manifest.select(cases, Split.TRAIN),
        all_cases=cases,
        config=config,
    )
    assert len(records) == 1
    assert records[0].chosen_template_id == "tpl-syn-thanks"
    assert records[0].judge is not None and records[0].judge.solution_agrees is True
    assert cache.is_file()

    # 第二次运行直接命中缓存，不再调用 LLM
    empty_fake = FakeLLMClient()
    resumed = await run_eval(
        reply_items,
        no_reply_items,
        snapshot=SNAPSHOT,
        guide=guide,
        llm=empty_fake,
        train_cases=manifest.select(cases, Split.TRAIN),
        all_cases=cases,
        config=config,
    )
    assert resumed == records
    assert empty_fake.calls == []


async def test_judge_draft_wraps_untrusted_text() -> None:
    guide = load_knowledge(KNOWLEDGE).style_guide
    fake = FakeLLMClient()
    fake.queue(LLMTask.JUDGE, _verdict_json(solution_agrees=False, notes_zh="【合成】方向不一致"))
    # 借用 run_eval 构造一个草稿不如直接测 build_judge_messages / judge_draft
    from copilot.contracts import Draft

    draft = Draft(
        case_id="900",
        message_seq=1,
        body_ja=f"{guide.greeting.ja}\n\n{THANKS_BODY}\n\n{guide.closing.ja}\n{guide.notice.ja}",
        back_translation_zh="【合成】回译",
        checks=[
            {"kind": k, "passed": True}
            for k in DraftCheckKind  # type: ignore[misc]
        ],
        confidence=0.9,
        route=Route.REVIEW,
        route_reasons=["测试"],
        model="fake",
        created_at=T2,
    )
    verdict = await judge_draft(
        case("900", content="【合成】测试"), draft, "【合成】真实回复", fake
    )
    assert verdict.solution_agrees is False
    assert "<actual_reply>" in fake.calls[0].messages[1]["content"]
    assert "<draft_ja>" in fake.calls[0].messages[1]["content"]


def test_timeline_keeps_prior_message_as_lower_bound() -> None:
    c1 = case(
        "1",
        player("1", 1, "【合成】p1").model_copy(update={"sent_at": T1}),
        agent("1", 2, "【合成】a1").model_copy(update={"sent_at": T3}),
        player("1", 3, "【合成】p2"),
        agent("1", 4, "【合成】a2"),
    )
    # 线程 2 提交更晚但带有一个早于 T3 的时间锚点，不应把线程 1 seq=4 的重放窗口下界倒推到 T2
    c2 = case(
        "2",
        player("2", 1, "【合成】p").model_copy(update={"sent_at": T2}),
    )
    est = TimelineIndex([c1, c2]).estimate_now(c1, target_seq=4)
    assert est.status is TimeStatus.ESTIMATED
    assert est.earliest == T3
    assert est.latest is not None and est.latest >= est.earliest


def test_replay_strips_unavailable_role_evidence_at_cutoff() -> None:
    c_early = case(
        "1",
        player("1", 1, "【合成】p1").model_copy(update={"sent_at": T1}),
        agent("1", 2, THANKS_BODY, RoleEvidenceKind.QUOTE_CORROBORATION, source="1:3").model_copy(
            update={"sent_at": T1}
        ),
        agent("1", 3, "【合成】a2").model_copy(update={"sent_at": T3}),
    )
    truncated = truncate_case(c_early, before_seq=3)
    assert [m.seq for m in truncated.messages] == [1, 2]
    assert truncated.messages[1].role_strength is not RoleEvidenceStrength.STRONG
    assert truncated.status is CaseStatus.UNDETERMINED

    c_mid = case(
        "2",
        player("2", 1, "【合成】p").model_copy(update={"sent_at": T1}),
        agent("2", 2, THANKS_BODY, RoleEvidenceKind.CORROBORATED_TEXT, source="1:2").model_copy(
            update={"sent_at": T1}
        ),
    )
    c_cur = case("3", player("3", 1, "【合成】now"))
    now = TimelineIndex([c_early, c_mid, c_cur]).estimate_now(
        case(
            "3",
            player("3", 1, "【合成】now"),
            agent("3", 2, "【合成】r").model_copy(update={"sent_at": T2}),
        ),
        target_seq=2,
    )
    hist = replay_history([c_early, c_mid, c_cur], c_cur, now)
    assert len(hist) == 2
    assert hist[0].status is CaseStatus.UNDETERMINED
    assert hist[1].status is CaseStatus.UNDETERMINED

    # 直接调用 truncate_case（未传 visible_cases）时应剥离不可核实的跨线程证据；
    # 传入 visible_cases 时，仅保留来自可见来源的跨线程证据。
    c_target = case(
        "4",
        player("4", 1, "【合成】p").model_copy(update={"sent_at": T1}),
        agent("4", 2, THANKS_BODY, RoleEvidenceKind.CORROBORATED_TEXT, source="1:2").model_copy(
            update={"sent_at": T1}
        ),
        agent("4", 3, "【合成】followup"),
    )
    standalone = truncate_case(c_target, before_seq=3)
    assert standalone.messages[1].role_strength is not RoleEvidenceStrength.STRONG
    assert standalone.status is CaseStatus.UNDETERMINED

    invisible_src = truncate_case(c_target, before_seq=3, visible_cases=hist)
    assert invisible_src.messages[1].role_strength is not RoleEvidenceStrength.STRONG
    assert invisible_src.status is CaseStatus.UNDETERMINED

    c_visible_anchor = case(
        "1",
        player("1", 1, "【合成】p1").model_copy(update={"sent_at": T1}),
        agent("1", 2, THANKS_BODY, RoleEvidenceKind.QUOTE_CORROBORATION, source="1:3").model_copy(
            update={"sent_at": T1}
        ),
        agent("1", 3, "【合成】a2").model_copy(update={"sent_at": T1}),
    )
    visible_hist = replay_history([c_visible_anchor, c_target], c_target, now)
    kept_src = truncate_case(c_target, before_seq=3, visible_cases=visible_hist)
    assert kept_src.messages[1].role_strength is RoleEvidenceStrength.STRONG
    assert kept_src.status is CaseStatus.REPLIED


def test_append_and_read_recover_incomplete_tail_and_split_utf8(tmp_path: Path) -> None:
    rec1 = CaseEvalRecord(
        kind="reply",
        case_id="10",
        message_id="10:2",
        split=Split.DEV,
        now_status=TimeStatus.KNOWN,
        problem_time_status=TimeStatus.UNAVAILABLE,
    )
    rec2 = rec1.model_copy(update={"case_id": "11", "message_id": "11:2"})

    # 1. 截断的半个 UTF-8 尾行不崩溃，并在追加新记录前自动清理
    p1 = tmp_path / "split_utf8.jsonl"
    write_records([rec1], p1)
    with p1.open("ab") as f:
        f.write(b'{"case_id":"bad","split":"' + "あ".encode()[:2])
    assert read_records(p1) == [rec1]
    append_record(rec2, p1)
    assert read_records(p1) == [rec1, rec2]

    # 2. 完整尾行仅缺换行时，保留该行并补齐换行后再追加
    p2 = tmp_path / "no_newline.jsonl"
    p2.write_bytes(rec1.model_dump_json().encode("utf-8"))
    append_record(rec2, p2)
    assert read_records(p2) == [rec1, rec2]


def test_combined_success_counts_false_no_reply_and_separates_unknown() -> None:
    ok_rec = CaseEvalRecord(
        kind="reply",
        case_id="1",
        message_id="1:2",
        split=Split.DEV,
        evidence=RoleEvidenceStrength.STRONG,
        now_status=TimeStatus.KNOWN,
        problem_time_status=TimeStatus.KNOWN,
        expected_template_id="tpl-syn-thanks",
        chosen_template_id="tpl-syn-thanks",
        top3_template_ids=["tpl-syn-thanks"],
        route=Route.REVIEW,
        judge=JudgeScores(
            solution_agrees=True,
            register_appropriate=True,
            no_fabricated_promise=True,
            back_translation_faithful=True,
            citations_supported=True,
            no_other_case_leak=True,
        ),
    )
    suppressed = CaseEvalRecord(
        kind="reply",
        case_id="2",
        message_id="2:2",
        split=Split.DEV,
        evidence=RoleEvidenceStrength.STRONG,
        now_status=TimeStatus.KNOWN,
        problem_time_status=TimeStatus.KNOWN,
        expected_template_id="tpl-syn-thanks",
        no_reply_auto=True,
        predicted_no_reply_reason=NoReplyReason.DUPLICATE,
    )
    judge_err = ok_rec.model_copy(
        update={"case_id": "3", "message_id": "3:2", "judge": None, "judge_error": "TimeoutError"}
    )
    missed_top3_no_judge = ok_rec.model_copy(
        update={
            "case_id": "4",
            "message_id": "4:2",
            "chosen_template_id": "tpl-other",
            "top3_template_ids": ["tpl-other"],
            "judge": None,
            "judge_error": "TimeoutError",
        }
    )
    sl = compute_slice([ok_rec, suppressed, judge_err, missed_top3_no_judge])
    assert sl.combined_success_total == 3
    assert sl.combined_success_hits == 1
    assert sl.combined_success_unknown == 1
    assert sl.judge_errors == 2
    md = render_markdown([ok_rec, suppressed, judge_err, missed_top3_no_judge])
    assert "1/3 (33.3%)" in md
    assert "另有 1 条无评审结果" in md

    # 全部样本缺少评审结果（combined_success_total=0）时，报告也必须展示未知数量
    all_unknown_md = render_markdown([judge_err])
    assert "0/0 (—)（另有 1 条无评审结果）" in all_unknown_md


def test_summarize_citations_includes_template_guidance_and_body() -> None:
    from copilot.contracts import Citation, EntryType
    from copilot.draft.retrieval import KnowledgeSnapshot
    from tests.draft.factories import template

    snap = KnowledgeSnapshot(
        templates=[
            template("tpl-v", version=1, intent_id="syn_thanks", body="【合成】案内A").model_copy(
                update={"use_when_zh": "条件A", "avoid_when_zh": "禁忌A"}
            ),
            template("tpl-v", version=2, intent_id="syn_thanks", body="【合成】案内B").model_copy(
                update={"use_when_zh": "条件A", "avoid_when_zh": "禁忌B"}
            ),
        ]
    )
    out = summarize_citations(
        [
            Citation(
                entry_type=EntryType.TEMPLATE,
                entry_id="tpl-v",
                entry_version=2,
                claim_zh="依据",
            )
        ],
        snap,
    )
    assert len(out) == 1
    assert "【合成】案内B" in out[0]
    assert "禁忌B" in out[0]
    assert "案内A" not in out[0]


async def test_eval_cache_keys_version_visibility_and_text_free_persistence(tmp_path: Path) -> None:
    from copilot.draft.retrieval import KnowledgeSnapshot
    from copilot.llm.embedding import FakeEmbeddingClient
    from tests.draft.factories import intent, template

    guide = load_knowledge(KNOWLEDGE).style_guide
    t_v1 = template(
        "tpl-syn-thanks", version=1, intent_id="syn_thanks", body=THANKS_BODY
    ).model_copy(update={"known_from": T1, "recorded_at": T1})
    t_v2 = template(
        "tpl-syn-thanks", version=2, intent_id="syn_thanks", body=THANKS_BODY
    ).model_copy(update={"known_from": T3, "recorded_at": T3})
    snap = KnowledgeSnapshot(
        intents=[
            intent("syn_thanks", risk=RiskLevel.LOW, auto_send=True).model_copy(
                update={"known_from": T1, "recorded_at": T1}
            )
        ],
        templates=[t_v1, t_v2],
    )
    cases = [
        case(
            str(i),
            player(str(i), 1, f"【合成】ありがとう {i}"),
            agent(str(i), 2, THANKS_BODY, RoleEvidenceKind.METADATA).model_copy(
                update={"sent_at": T2}
            ),
            content=f"【合成】ありがとう {i}",
        )
        for i in range(1, 11)
    ]
    manifest = split_cases(cases)
    target = manifest.select(cases, Split.DEV)[0]
    labels = [
        ReplyLabel(
            message_id=f"{target.id}:2",
            split=Split.DEV,
            evidence=RoleEvidenceStrength.STRONG,
            template_id="tpl-syn-thanks",
            template_version=2,
            match=MatchKind.EXACT,
            resolution=Resolution.RESOLVED,
            inquiry_message_id=f"{target.id}:1",
            first_agent_reply=True,
        )
    ]
    reply_items = build_reply_items(cases, manifest, Split.DEV, labels)

    cache = tmp_path / "cache.jsonl"
    cfg = EvalRunConfig(
        params=DraftParams(analyze_model="fake", draft_model="fake"),
        concurrency=1,
        strict_replay=True,
        run_judge=True,
        cache_path=cache,
        service_tier="default",
        backtranslate_model="bt-a",
        judge_model="judge-a",
    )
    # 1. 指纹区分 service_tier / backtranslate_model / judge_model
    assert (
        cfg.fingerprint()
        != EvalRunConfig(
            params=cfg.params,
            service_tier="flex",
            backtranslate_model="bt-a",
            judge_model="judge-a",
        ).fingerprint()
    )
    assert (
        cfg.fingerprint()
        != EvalRunConfig(
            params=cfg.params,
            service_tier="default",
            backtranslate_model="bt-b",
            judge_model="judge-a",
        ).fingerprint()
    )

    # 2. v1 可见而标注指向 v2 时，expected_template_visible=False；notes_zh 不写入缓存文件
    fake1 = FakeLLMClient()
    fake1.queue(LLMTask.ANALYZE, analysis_reply())
    fake1.queue(LLMTask.DRAFT, draft_reply())
    fake1.queue(LLMTask.BACKTRANSLATE, "【合成】回译")
    fake1.queue(LLMTask.JUDGE, _verdict_json(notes_zh="SECRET-NOTE-SHOULD-NOT-PERSIST"))
    recs = await run_eval(
        reply_items,
        [],
        snapshot=snap,
        guide=guide,
        llm=fake1,
        train_cases=manifest.select(cases, Split.TRAIN),
        all_cases=cases,
        config=cfg,
    )
    assert recs[0].error is None and recs[0].judge_error is None
    assert recs[0].expected_template_visible is False
    raw_cache = cache.read_text(encoding="utf-8")
    assert "SECRET-NOTE-SHOULD-NOT-PERSIST" not in raw_cache
    assert "notes_zh" not in raw_cache

    # 3. 启用 embedder 后不应命中无 embedder 时的缓存
    fake2 = FakeLLMClient()
    fake2.queue(LLMTask.ANALYZE, analysis_reply())
    fake2.queue(LLMTask.DRAFT, draft_reply())
    fake2.queue(LLMTask.BACKTRANSLATE, "【合成】回译")
    fake2.queue(LLMTask.JUDGE, _verdict_json())
    recs2 = await run_eval(
        reply_items,
        [],
        snapshot=snap,
        guide=guide,
        llm=fake2,
        train_cases=manifest.select(cases, Split.TRAIN),
        all_cases=cases,
        embedder=FakeEmbeddingClient(),
        config=cfg,
    )
    assert recs2[0].error is None
    assert len(fake2.calls) == 4

    # 4. 知识库内容或样本标签变更后同样失效；未变更则命中缓存（0 次模型调用）
    empty_fake = FakeLLMClient()
    resumed2 = await run_eval(
        reply_items,
        [],
        snapshot=snap,
        guide=guide,
        llm=empty_fake,
        train_cases=manifest.select(cases, Split.TRAIN),
        all_cases=cases,
        embedder=FakeEmbeddingClient(),
        config=cfg,
    )
    assert resumed2 == recs2
    assert empty_fake.calls == []

    updated_snap = KnowledgeSnapshot(
        intents=snap.intents,
        templates=[t_v1.model_copy(update={"use_when_zh": "【合成】新条件"}), t_v2],
    )
    fake3 = FakeLLMClient()
    fake3.queue(LLMTask.ANALYZE, analysis_reply())
    fake3.queue(LLMTask.DRAFT, draft_reply())
    fake3.queue(LLMTask.BACKTRANSLATE, "【合成】回译")
    fake3.queue(LLMTask.JUDGE, _verdict_json())
    await run_eval(
        reply_items,
        [],
        snapshot=updated_snap,
        guide=guide,
        llm=fake3,
        train_cases=manifest.select(cases, Split.TRAIN),
        all_cases=cases,
        embedder=FakeEmbeddingClient(),
        config=cfg,
    )
    assert len(fake3.calls) == 4
