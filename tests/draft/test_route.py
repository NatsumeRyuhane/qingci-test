"""分流规则与“不回复”（#6）。docs/architecture.md 的“分流规则”中每条规则都有测试。

SYNTHETIC：全部是合成数据。
"""

from datetime import datetime
from typing import Any

import pytest

from copilot.contracts import (
    Case,
    CaseAnalysis,
    CaseStatus,
    DraftCheck,
    DraftCheckKind,
    InquiryCategory,
    Message,
    MessageRole,
    NoReplyProposal,
    NoReplyReason,
    RiskLevel,
    RoleEvidence,
    RoleEvidenceKind,
    Route,
    SensitivityTag,
    Sentiment,
    Urgency,
)
from copilot.draft.retrieval import Candidate
from copilot.draft.route import (
    GATES,
    AutoSendApproval,
    AutoSendGate,
    RouteInput,
    approve_auto_send,
    decide_no_reply,
    first_reply,
    route,
)
from copilot.draft.timectx import WindowMatch
from copilot.ingest.timestamps import JST
from tests.cases import agent, case, player
from tests.draft.factories import intent, template, trigger

THRESHOLD = 0.85


def analysis(**values: Any) -> CaseAnalysis:
    base: dict[str, Any] = {
        "case_id": "500",
        "message_seq": 1,
        "translation_zh": "【合成】",
        "summary_zh": "【合成】",
        "category": InquiryCategory.FEEDBACK,
        "intent": "syn_thanks",
        "intent_confidence": 0.95,
        "sentiment": Sentiment.POSITIVE,
        "urgency": Urgency.LOW,
        "model": "fake",
        "created_at": datetime(2099, 1, 1, tzinfo=JST),
    }
    return CaseAnalysis(**{**base, **values})


GENERIC = Candidate(
    template=template("tpl-syn-thanks", intent_id="syn_thanks", risk=RiskLevel.LOW, auto_send=True),
    time_bound=False,
    window=WindowMatch.INSIDE,
)
PASSED = [DraftCheck(kind=kind, passed=True) for kind in DraftCheckKind]


def inputs(**values: Any) -> RouteInput:
    base: dict[str, Any] = {
        "case": case("500", content="【合成】いつも楽しく遊んでいます。ありがとうございます。"),
        "analysis": analysis(),
        "intent": intent("syn_thanks", risk=RiskLevel.LOW, auto_send=True),
        "candidate": GENERIC,
        "checks": PASSED,
        "draft_confidence": 0.95,
        "escalation_triggers": [trigger()],
        "min_confidence": THRESHOLD,
    }
    return RouteInput(**{**base, **values})


def reasons(inp: RouteInput) -> str:
    return "\n".join(route(inp)[1])


def test_auto_send_when_every_gate_passes() -> None:
    assert route(inputs())[0] is Route.AUTO_SEND


# --- 升级 ---------------------------------------------------------------------


def test_escalate_on_trigger_phrase_in_player_text() -> None:
    angry_case = case("500", content="【合成】弁護士に相談します")
    decision, why = route(inputs(case=angry_case))
    assert decision is Route.ESCALATE
    assert "弁護士" in why[0]


def test_escalate_on_trigger_phrase_in_follow_up() -> None:
    thread = case(
        "500",
        player("500", 1),
        agent("500", 2, "【合成】回答", RoleEvidenceKind.KEYWORD),
        player("500", 3, "【合成】弁護士に相談します"),
    )
    assert route(inputs(case=thread))[0] is Route.ESCALATE


def test_escalate_on_sensitivity_tag_of_trigger() -> None:
    inp = inputs(analysis=analysis(sensitivity_tags=[SensitivityTag.LEGAL]))
    assert route(inp)[0] is Route.ESCALATE
    assert "legal" in reasons(inp)


def test_sensitivity_tag_without_trigger_only_reviews() -> None:
    inp = inputs(analysis=analysis(sensitivity_tags=[SensitivityTag.PERSONAL_INFO]))
    assert route(inp)[0] is Route.REVIEW
    assert "敏感标签" in reasons(inp)


def test_escalate_when_angry() -> None:
    assert route(inputs(analysis=analysis(sentiment=Sentiment.ANGRY)))[0] is Route.ESCALATE


def test_escalate_without_template() -> None:
    decision, why = route(inputs(candidate=None))
    assert decision is Route.ESCALATE and why == ["找不到合适的模板"]


def test_escalate_wins_over_everything_and_lists_all_reasons() -> None:
    inp = inputs(candidate=None, analysis=analysis(sentiment=Sentiment.ANGRY))
    decision, why = route(inp)
    assert decision is Route.ESCALATE and len(why) == 2


# --- 自动发送的门槛（每一条单独不满足时都转人工审核）---------------------------


def _with_template(**values: Any) -> Candidate:
    base: dict[str, Any] = {"intent_id": "syn_thanks", "risk": RiskLevel.LOW, "auto_send": True}
    fields = {**base, **values}
    return Candidate(
        template=template("tpl-syn-x", **fields),
        time_bound=False,
        window=WindowMatch.INSIDE,
    )


GATE_CASES: list[tuple[AutoSendGate, dict[str, Any], str]] = [
    (
        AutoSendGate.CHECKS_PASSED,
        {"checks": [*PASSED[1:], DraftCheck(kind=PASSED[0].kind, passed=False)]},
        "检查未通过",
    ),
    (
        AutoSendGate.CHECKS_PASSED,
        {
            "checks": [
                *[c for c in PASSED if c.kind is not DraftCheckKind.TEMPLATE_WINDOW],
                DraftCheck(
                    kind=DraftCheckKind.TEMPLATE_WINDOW,
                    passed=False,
                    detail_zh="无法确定问题发生时间是否在事件窗口内",
                ),
            ]
        },
        "时间窗口",
    ),
    (AutoSendGate.CHECKS_PASSED, {"checks": []}, "缺少检查结果"),
    (
        AutoSendGate.TEMPLATE_ALLOWED,
        {"candidate": _with_template(risk=RiskLevel.NORMAL, auto_send=False)},
        "不允许自动发送",
    ),
    (
        AutoSendGate.TEMPLATE_GENERIC,
        {
            "candidate": Candidate(
                template=GENERIC.template, time_bound=True, window=WindowMatch.INSIDE
            )
        },
        "不是通用模板",
    ),
    (
        AutoSendGate.TEMPLATE_GENERIC,
        {"candidate": _with_template(body="【合成】{{x}}", slots=["x"])},
        "不是通用模板",
    ),
    (
        AutoSendGate.TEMPLATE_IN_INTENT,
        {
            "candidate": Candidate(
                template=GENERIC.template,
                time_bound=False,
                window=WindowMatch.INSIDE,
                in_intent=False,
            )
        },
        "不是分类出的",
    ),
    (
        AutoSendGate.INTENT_ELIGIBLE,
        {"intent": intent("syn_thanks", risk=RiskLevel.LOW)},
        "允许清单",
    ),
    (AutoSendGate.INTENT_ELIGIBLE, {"intent": None}, "不在意图目录中"),
    # 评审意见（PR #28）：调用方传入的意图与分析分类出的意图不一致时不通过。
    (
        AutoSendGate.INTENT_ELIGIBLE,
        {
            "intent": intent("syn_thanks", risk=RiskLevel.LOW, auto_send=True),
            "analysis": analysis(intent="syn_payment"),
            "candidate": Candidate(
                template=GENERIC.template, time_bound=False, window=WindowMatch.INSIDE
            ),
        },
        "不一致",
    ),
    # 调用方算错 in_intent 时，模板意图仍由关卡直接核对。
    (
        AutoSendGate.TEMPLATE_IN_INTENT,
        {"analysis": analysis(intent="syn_payment")},
        "不是分类出的",
    ),
    (AutoSendGate.INTENT_CONFIDENCE, {"analysis": analysis(intent_confidence=0.5)}, "置信度 0.50"),
    (AutoSendGate.INTENT_CONFIDENCE, {"analysis": analysis(intent_confidence=None)}, "置信度 无"),
    (AutoSendGate.DRAFT_CONFIDENCE, {"draft_confidence": 0.6}, "草稿信心值"),
    (
        AutoSendGate.NO_SENSITIVITY_TAGS,
        {"analysis": analysis(sensitivity_tags=[SensitivityTag.PAYMENT])},
        "敏感标签",
    ),
    (AutoSendGate.SENTIMENT_OK, {"analysis": analysis(sentiment=Sentiment.NEGATIVE)}, "negative"),
    (
        AutoSendGate.FIRST_REPLY,
        {
            "case": case(
                "500",
                player("500", 1),
                agent("500", 2, "【合成】回答", RoleEvidenceKind.REPEATED_TEXT),
                player("500", 3, "【合成】ありがとうございます"),
            ),
            "analysis": analysis(message_seq=3),
        },
        "第一封",
    ),
    (
        AutoSendGate.NO_INJECTION,
        {"analysis": analysis(injection_signals=["正文中出现分隔标记"])},
        "疑似提示注入",
    ),
    (
        AutoSendGate.REPLY_CONFIRMED,
        {
            "case": case("500", content="【合成】").model_copy(
                update={"status": CaseStatus.UNDETERMINED}
            ),
            "analysis": analysis(thread_judgement=CaseStatus.OPEN),
        },
        "undetermined",
    ),
    (
        AutoSendGate.REPLY_CONFIRMED,
        {
            "analysis": analysis(
                no_reply=NoReplyProposal(reason=NoReplyReason.OTHER, reason_zh="【合成】")
            )
        },
        "不回复",
    ),
]


@pytest.mark.parametrize(("gate", "change", "expected"), GATE_CASES)
def test_each_gate_blocks_auto_send(
    gate: AutoSendGate, change: dict[str, Any], expected: str
) -> None:
    """每个关卡单独不通过时：这个关卡不通过，批准不成立，转人工审核并说明理由。"""
    inp = inputs(**change)
    approval = approve_auto_send(inp)
    assert approval.results[gate], f"{gate.value} 应当不通过"
    assert not approval.granted
    decision, why = route(inp)
    assert decision is Route.REVIEW
    assert expected in "\n".join(why)


def test_every_gate_is_evaluated_and_tested() -> None:
    assert set(GATES) == set(AutoSendGate)
    assert len(AutoSendGate) == 12
    assert set(approve_auto_send(inputs()).results) == set(AutoSendGate)
    assert {gate for gate, _, _ in GATE_CASES} == set(AutoSendGate)


def test_all_gates_passing_is_the_only_approval() -> None:
    approval = approve_auto_send(inputs())
    assert approval.granted and approval.reasons == []


def test_missing_gate_result_is_not_approval() -> None:
    results = dict(approve_auto_send(inputs()).results)
    del results[AutoSendGate.NO_INJECTION]
    approval = AutoSendApproval(results=results)
    assert not approval.granted
    assert approval.missing == [AutoSendGate.NO_INJECTION]
    assert "缺少关卡结果：no_injection" in approval.reasons


def test_first_reply_is_conservative() -> None:
    weak = case("1", player("1", 1), agent("1", 2, "【合成】", RoleEvidenceKind.KEYWORD))
    assert not first_reply(weak, 2)
    assert first_reply(case("1", player("1", 1), player("1", 2)), 2)


def test_review_lists_every_reason() -> None:
    inp = inputs(analysis=analysis(sentiment=Sentiment.NEGATIVE, intent_confidence=0.1))
    assert len(route(inp)[1]) == 2


# --- 不回复 -------------------------------------------------------------------

FORM = "【合成】同じ内容の問い合わせを二回送ってしまいました、よろしくお願いします"


def system_case(thread_id: str, *, strong: bool = True) -> Case:
    evidence = [RoleEvidence(kind=RoleEvidenceKind.SYSTEM_PATTERN)] if strong else []
    message = Message(
        thread_id=thread_id,
        seq=1,
        role=MessageRole.SYSTEM if strong else MessageRole.UNKNOWN,
        raw_body="【合成】Mail Delivery Subsystem",
        body="【合成】Mail Delivery Subsystem",
        role_evidence=evidence,
    )
    return Case(id=thread_id, messages=[message])


def replied(thread_id: str, evidence: RoleEvidenceKind) -> Case:
    source = f"{thread_id}:3" if evidence is RoleEvidenceKind.QUOTE_CORROBORATION else None
    return case(
        thread_id,
        player(thread_id, 1),
        agent(thread_id, 2, "【合成】回答", evidence, source=source),
        player(thread_id, 3),
        content=FORM,
    )


def test_duplicate_of_replied_thread_is_automatic() -> None:
    original = replied("400", RoleEvidenceKind.QUOTE_CORROBORATION)
    decision = decide_no_reply(case("401", content=FORM), None, [original])
    assert decision is not None and decision.auto
    assert decision.proposal.duplicate_of == "400"


def test_duplicate_of_weakly_replied_thread_is_not_automatic() -> None:
    weak = replied("400", RoleEvidenceKind.KEYWORD)
    assert decide_no_reply(case("401", content=FORM), None, [weak]) is None


def test_model_claimed_duplicate_without_code_confirmation_is_not_automatic() -> None:
    claimed = analysis(
        case_id="401",
        no_reply=NoReplyProposal(
            reason=NoReplyReason.DUPLICATE, reason_zh="【合成】", duplicate_of="999"
        ),
    )
    decision = decide_no_reply(case("401", content=FORM), claimed, [])
    assert decision is not None and not decision.auto


def test_system_only_thread_is_automatic_only_with_strong_evidence() -> None:
    proposal = NoReplyProposal(reason=NoReplyReason.SYSTEM_MESSAGE, reason_zh="【合成】退信")
    decision = decide_no_reply(system_case("402"), analysis(no_reply=proposal), [])
    assert decision is not None and decision.auto
    weak = decide_no_reply(system_case("403", strong=False), analysis(no_reply=proposal), [])
    assert weak is not None and not weak.auto


def test_system_thread_with_player_question_needs_reply() -> None:
    # 分析在伪造的退信中发现了玩家的问题，没有提议不回复：需要回复。
    assert decide_no_reply(system_case("402"), analysis(), []) is None


def test_system_thread_with_injection_is_not_automatic() -> None:
    proposal = NoReplyProposal(reason=NoReplyReason.SYSTEM_MESSAGE, reason_zh="【合成】")
    suspicious = analysis(no_reply=proposal, injection_signals=["【合成】"])
    decision = decide_no_reply(system_case("402"), suspicious, [])
    assert decision is not None and not decision.auto


def test_other_reason_is_never_automatic() -> None:
    proposal = NoReplyProposal(reason=NoReplyReason.OTHER, reason_zh="【合成】")
    decision = decide_no_reply(case("404", content=FORM), analysis(no_reply=proposal), [])
    assert decision is not None and not decision.auto
    assert "永远不自动生效" in decision.reason_zh


# --- 评审意见（PR #28）-------------------------------------------------------


@pytest.mark.parametrize("checks", [[], PASSED[:-1]])
def test_missing_checks_block_auto_send(checks: list[DraftCheck]) -> None:
    decision, why = route(inputs(checks=checks))
    assert decision is Route.REVIEW
    assert any("缺少检查结果" in r for r in why)


def test_duplicate_shortcut_ignores_threads_with_follow_up() -> None:
    original = replied("400", RoleEvidenceKind.QUOTE_CORROBORATION)
    follow_up = case(
        "401", player("401", 1), player("401", 2, "【合成】別の質問です"), content=FORM
    )
    assert decide_no_reply(follow_up, None, [original]) is None


def test_later_replied_duplicate_does_not_suppress_earlier_thread() -> None:
    """评审意见（PR #28）：对应的线程必须更早提交。"""
    later = replied("401", RoleEvidenceKind.QUOTE_CORROBORATION)
    assert decide_no_reply(case("400", content=FORM), None, [later]) is None
