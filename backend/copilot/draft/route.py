"""分流与“不回复”的判断（#6，D-001，提案 0003 §5、§8、§13）。

规则全部由代码执行，写在 docs/architecture.md 的“分流规则”中，每条都有测试。按顺序：

1. **升级（`escalate`）**，任一条件成立：玩家邮件中出现升级政策的短语；分析提议的敏感标签
   命中升级政策；情绪为 `angry`；找不到合适的模板。
2. **自动发送（`auto_send`）**，全部条件成立（D-011）：模板已批准、风险低、允许自动发送，
   并且是通用模板、属于分类出的意图；意图在自动发送允许清单中；意图分类置信度和草稿信心值
   都不低于阈值；没有敏感标签，情绪不是负面；线程中此前只有玩家邮件（第一封客服回复）；
   所有检查通过；没有疑似提示注入；案件不是 `undetermined`，也没有“不回复”的提议。
3. 其余为**人工审核（`review`）**，每个不满足的条件都写进 `route_reasons`。时间窗口无法
   确定（`template_window` 不通过）也在这里。

MVP 中不会真正发送任何邮件，`auto_send` 只是界面中显示的建议。

“不回复”（提案 0003 §8）只在两种可解释的情况下自动生效，其他提议都转人工审核：

- 重复提交：线程只有最初的那封咨询（之后没有追问），由代码按 `duplicate_key` 确认（不依赖
  模型），并且对应的线程有可信的客服回复。表单属于第一封邮件，线程中有了追问就不能只按
  表单判断，追问可能是另一个问题；
- 只含系统邮件：每封都有强的系统证据，分析也认为其中没有玩家的问题（提议 `system_message`），
  并且没有疑似注入。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from copilot.contracts import (
    Case,
    CaseAnalysis,
    CaseStatus,
    DraftCheck,
    DraftCheckKind,
    Intent,
    MessageRole,
    NoReplyProposal,
    NoReplyReason,
    Policy,
    RiskLevel,
    RoleEvidenceKind,
    RoleEvidenceStrength,
    Route,
    Sentiment,
)
from copilot.draft.retrieval import Candidate
from copilot.ingest.duplicates import duplicate_key
from copilot.ingest.pipeline import thread_order

NEGATIVE = {Sentiment.NEGATIVE, Sentiment.ANGRY}

CHECK_NAMES_ZH = {
    DraftCheckKind.STRUCTURE: "结构",
    DraftCheckKind.REGISTER: "语体",
    DraftCheckKind.FORBIDDEN_PROMISE: "不允许的承诺",
    DraftCheckKind.TEMPLATE_WINDOW: "时间窗口",
    DraftCheckKind.SLOTS_FILLED: "占位符",
    DraftCheckKind.CITATIONS: "引用",
    DraftCheckKind.CONTENT_SAFETY: "内容安全",
}


def done(checks: Iterable[DraftCheck]) -> set[DraftCheckKind]:
    """有结果的检查项。自动发送要求每一项（`DraftCheckKind` 的全部取值）都有结果并且通过。"""
    return {c.kind for c in checks}


def player_text(case: Case) -> str:
    """案件中玩家写的全部文字：表单内容和玩家邮件。"""
    parts = [case.form.content] if case.form and case.form.content else []
    parts += [m.body for m in case.messages if m.role is MessageRole.PLAYER]
    return "\n".join(parts)


def escalation_reasons(case: Case, analysis: CaseAnalysis, triggers: Iterable[Policy]) -> list[str]:
    """升级政策命中的理由：玩家文字中的短语，或分析提议的敏感标签。"""
    text = player_text(case)
    reasons = []
    for policy in triggers:
        phrases = policy.matching_phrases(text)
        tags = sorted(set(policy.sensitivity_tags) & set(analysis.sensitivity_tags))
        if phrases:
            reasons.append(f"升级政策《{policy.title_zh}》：玩家邮件中出现“{phrases[0]}”")
        elif tags:
            reasons.append(
                f"升级政策《{policy.title_zh}》：敏感标签 {', '.join(t.value for t in tags)}"
            )
    return reasons


def first_reply(case: Case, message_seq: int) -> bool:
    """草稿是否是线程中的第一封客服回复：被回复的邮件及之前只有玩家邮件。

    保守的判断：之前有任何非玩家邮件（包括证据弱的“客服回复”、系统邮件和无法判断的邮件）
    都不算第一封。
    """
    return all(m.role is MessageRole.PLAYER for m in case.messages if m.seq <= message_seq)


@dataclass(frozen=True)
class RouteInput:
    """分流需要的全部信息。"""

    case: Case
    analysis: CaseAnalysis
    intent: Intent | None
    candidate: Candidate | None
    checks: Sequence[DraftCheck]
    draft_confidence: float
    escalation_triggers: Sequence[Policy]
    min_confidence: float


def _auto_send_blockers(inp: RouteInput) -> list[str]:
    """不能自动发送的理由。为空表示满足全部条件。"""
    blockers = []
    candidate, analysis = inp.candidate, inp.analysis
    if candidate is not None:
        template = candidate.template
        if not template.auto_send_allowed or template.risk is not RiskLevel.LOW:
            blockers.append(f"模板不允许自动发送（风险 {template.risk.value}）")
        if candidate.specific:
            blockers.append("模板是限时模板或带占位符，不是通用模板")
        if not candidate.in_intent:
            blockers.append(f"所选模板属于意图 {template.intent}，不是分类出的 {analysis.intent}")
    if inp.intent is None or not inp.intent.auto_send_eligible:
        blockers.append(f"意图 {analysis.intent} 不在自动发送允许清单中")
    confidence = analysis.intent_confidence
    if confidence is None or confidence < inp.min_confidence:
        shown = "无" if confidence is None else f"{confidence:.2f}"
        blockers.append(f"意图分类置信度 {shown} 低于 {inp.min_confidence:.2f}")
    if inp.draft_confidence < inp.min_confidence:
        blockers.append(f"草稿信心值 {inp.draft_confidence:.2f} 低于 {inp.min_confidence:.2f}")
    if analysis.sensitivity_tags:
        tags = ", ".join(t.value for t in analysis.sensitivity_tags)
        blockers.append(f"有敏感标签：{tags}")
    if analysis.sentiment in NEGATIVE:
        blockers.append(f"玩家情绪为 {analysis.sentiment.value}")
    if not first_reply(inp.case, analysis.message_seq):
        blockers.append("不是线程中的第一封客服回复")
    if analysis.injection_signals:
        blockers.append(f"疑似提示注入：{'；'.join(analysis.injection_signals)}")
    if inp.case.status is CaseStatus.UNDETERMINED:
        judged = analysis.thread_judgement
        detail = f"，分析判断为 {judged.value}" if judged else ""
        blockers.append(f"案件状态待判断（undetermined{detail}），请确认是否需要回复")
    if analysis.no_reply is not None:
        blockers.append(f"分析提议不回复：{analysis.no_reply.reason_zh}")
    return blockers


def route(inp: RouteInput) -> tuple[Route, list[str]]:
    """分流结果及理由（至少一条）。"""
    escalate = escalation_reasons(inp.case, inp.analysis, inp.escalation_triggers)
    if inp.analysis.sentiment is Sentiment.ANGRY:
        escalate.append("玩家情绪非常负面（angry）")
    if inp.candidate is None:
        escalate.append("找不到合适的模板")
    if escalate:
        return Route.ESCALATE, escalate

    failed = [
        f"检查未通过（{CHECK_NAMES_ZH.get(c.kind, c.kind.value)}）：{c.detail_zh or '未说明'}"
        for c in inp.checks
        if not c.passed
    ]
    missing = [CHECK_NAMES_ZH.get(k, k.value) for k in DraftCheckKind if k not in done(inp.checks)]
    if missing:
        failed.append(f"缺少检查结果：{'、'.join(missing)}")
    blockers = _auto_send_blockers(inp)
    if not failed and not blockers:
        return Route.AUTO_SEND, ["低风险的通用模板，自动发送的全部条件都满足（MVP 中只是建议）"]
    return Route.REVIEW, [*failed, *blockers]


@dataclass(frozen=True)
class NoReplyDecision:
    """“不回复”的判断。`auto` 为 True 时案件直接设为 `no_reply_needed`，不生成草稿。"""

    proposal: NoReplyProposal
    auto: bool
    reason_zh: str


def _strong_agent_reply(case: Case) -> bool:
    return any(
        m.role is MessageRole.AGENT and m.role_strength is RoleEvidenceStrength.STRONG
        for m in case.messages
    )


def _system_only(case: Case) -> bool:
    return all(
        m.role is MessageRole.SYSTEM
        and any(e.kind is RoleEvidenceKind.SYSTEM_PATTERN for e in m.role_evidence)
        for m in case.messages
    )


def find_duplicate_of(case: Case, others: Iterable[Case]) -> Case | None:
    """内容、设备、App 版本和问题发生时间都相同、并且有可信客服回复的另一个线程（最早的）。

    样本没有账号和发件地址，只能按内容判断；生产中应按账号或发件地址判断（提案 0003 §8）。
    """
    key = duplicate_key(case)
    if key is None:
        return None
    found = [
        other
        for other in others
        if other.id != case.id and duplicate_key(other) == key and _strong_agent_reply(other)
    ]
    return min(found, key=lambda c: thread_order(c.id), default=None)


def decide_no_reply(
    case: Case, analysis: CaseAnalysis | None, others: Iterable[Case]
) -> NoReplyDecision | None:
    """是否不回复，以及是否自动生效。返回 None 表示需要回复。"""
    if len(case.messages) == 1 and case.messages[0].role is MessageRole.PLAYER:
        duplicate = find_duplicate_of(case, others)
        if duplicate is not None:
            proposal = NoReplyProposal(
                reason=NoReplyReason.DUPLICATE,
                reason_zh=f"与已回复的线程 {duplicate.id} 是同一个咨询（重复提交）",
                duplicate_of=duplicate.id,
            )
            return NoReplyDecision(proposal=proposal, auto=True, reason_zh=proposal.reason_zh)
    if analysis is None or analysis.no_reply is None:
        return None
    proposal = analysis.no_reply
    if (
        proposal.reason is NoReplyReason.SYSTEM_MESSAGE
        and _system_only(case)
        and not analysis.injection_signals
    ):
        return NoReplyDecision(
            proposal=proposal, auto=True, reason_zh="线程只含系统邮件，分析没有发现玩家的问题"
        )
    why = {
        NoReplyReason.DUPLICATE: "代码没有确认重复提交（或对应线程没有可信的回复）",
        NoReplyReason.SYSTEM_MESSAGE: "不是每封都有强的系统证据，或疑似提示注入",
        NoReplyReason.OTHER: "原因不明的“不回复”永远不自动生效",
    }[proposal.reason]
    return NoReplyDecision(proposal=proposal, auto=False, reason_zh=f"需要人工确认：{why}")
