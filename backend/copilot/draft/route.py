"""分流与“不回复”的判断（#6，D-001，提案 0003 §5、§8、§13）。

规则全部由代码执行，写在 docs/architecture.md 的“分流规则”中，每条都有测试。按顺序：

1. **升级（`escalate`）**，任一条件成立：玩家邮件中出现升级政策的短语；分析提议的敏感标签
   命中升级政策；情绪为 `angry`；找不到合适的模板。
2. **自动发送（`auto_send`）**：`AutoSendGate` 的 12 个关卡**一个不过，全部不过**
   （D-011）。`approve_auto_send` 逐一评估每个关卡，只有每个关卡都有结果并且通过，
   `AutoSendApproval.granted` 才为 True；缺少任何一个关卡的结果也不批准。
3. 其余为**人工审核（`review`）**，每个不通过的关卡的理由都写进 `route_reasons`。时间窗口无法
   确定（`template_window` 不通过）也在这里。

MVP 中不会真正发送任何邮件，`auto_send` 只是界面中显示的建议。

“不回复”（提案 0003 §8）只在两种可解释的情况下自动生效，其他提议都转人工审核：

- 重复提交：线程只有最初的那封咨询（之后没有追问），由代码按 `duplicate_key` 确认（不依赖
  模型），并且对应的线程有可信的客服回复。表单属于第一封邮件，线程中有了追问就不能只按
  表单判断，追问可能是另一个问题；
- 只含系统邮件：每封都有强的系统证据，分析也认为其中没有玩家的问题（提议 `system_message`），
  并且没有疑似注入。
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

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
    ReviewStatus,
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


class AutoSendGate(StrEnum):
    """自动发送的关卡。**一个不过，全部不过**：只有每一个关卡都有结果并且通过，
    `AutoSendApproval.granted` 才为 True（D-011，提案 0003 §5、§13）。"""

    CHECKS_PASSED = "checks_passed"
    """`DraftCheckKind` 的每一项检查都有结果并且通过。"""
    TEMPLATE_ALLOWED = "template_allowed"
    """模板已批准、风险为 `low`、`auto_send_allowed`。"""
    TEMPLATE_GENERIC = "template_generic"
    """模板是通用模板：不限时、没有占位符。"""
    TEMPLATE_IN_INTENT = "template_in_intent"
    """模板属于分类出的意图（由这里核对模板的意图，不只信任 `Candidate.in_intent`）。"""
    INTENT_ELIGIBLE = "intent_eligible"
    """传入的意图就是分析分类出的意图，并且在自动发送允许清单中（`Intent.auto_send_eligible`）。"""
    INTENT_CONFIDENCE = "intent_confidence"
    """意图分类置信度不低于阈值。"""
    DRAFT_CONFIDENCE = "draft_confidence"
    """草稿信心值不低于阈值。"""
    NO_SENSITIVITY_TAGS = "no_sensitivity_tags"
    """分析没有提议任何敏感标签。"""
    SENTIMENT_OK = "sentiment_ok"
    """情绪不是 `negative` 或 `angry`。"""
    FIRST_REPLY = "first_reply"
    """线程中的第一封客服回复（`first_reply`）。"""
    NO_INJECTION = "no_injection"
    """没有疑似提示注入。"""
    REPLY_CONFIRMED = "reply_confirmed"
    """确实需要回复：案件不是 `undetermined`，也没有“不回复”的提议。"""


type Gate = Callable[[RouteInput], list[str]]
"""一个关卡：返回不通过的理由，空列表表示通过。"""


def _checks_passed(inp: RouteInput) -> list[str]:
    problems = [
        f"检查未通过（{CHECK_NAMES_ZH.get(c.kind, c.kind.value)}）：{c.detail_zh or '未说明'}"
        for c in inp.checks
        if not c.passed
    ]
    missing = [CHECK_NAMES_ZH.get(k, k.value) for k in DraftCheckKind if k not in done(inp.checks)]
    if missing:
        problems.append(f"缺少检查结果：{'、'.join(missing)}")
    return problems


def _template_allowed(inp: RouteInput) -> list[str]:
    if inp.candidate is None:
        return ["没有使用模板"]
    template = inp.candidate.template
    if (
        template.review_status is not ReviewStatus.APPROVED
        or template.risk is not RiskLevel.LOW
        or not template.auto_send_allowed
    ):
        return [f"模板不允许自动发送（风险 {template.risk.value}）"]
    return []


def _template_generic(inp: RouteInput) -> list[str]:
    if inp.candidate is None:
        return ["没有使用模板"]
    return ["模板是限时模板或带占位符，不是通用模板"] if inp.candidate.specific else []


def _template_in_intent(inp: RouteInput) -> list[str]:
    """模板属于分类出的意图。不只信任调用方算好的 `Candidate.in_intent`：不限时的模板还要
    由这里直接核对模板的意图（限时模板永远过不了 `template_generic`，不影响结果）。"""
    candidate = inp.candidate
    if candidate is None:
        return ["没有使用模板"]
    template = candidate.template
    if candidate.in_intent and (candidate.time_bound or template.intent == inp.analysis.intent):
        return []
    return [f"所选模板属于意图 {template.intent}，不是分类出的 {inp.analysis.intent}"]


def _intent_eligible(inp: RouteInput) -> list[str]:
    """意图在自动发送允许清单中，并且就是分析分类出的那个意图。调用方传错意图时不通过。"""
    if inp.intent is None:
        return [f"意图 {inp.analysis.intent} 不在意图目录中"]
    if inp.intent.id != inp.analysis.intent:
        return [f"传入的意图 {inp.intent.id} 与分析分类出的 {inp.analysis.intent} 不一致"]
    if not inp.intent.auto_send_eligible:
        return [f"意图 {inp.analysis.intent} 不在自动发送允许清单中"]
    return []


def _intent_confidence(inp: RouteInput) -> list[str]:
    confidence = inp.analysis.intent_confidence
    if confidence is not None and confidence >= inp.min_confidence:
        return []
    shown = "无" if confidence is None else f"{confidence:.2f}"
    return [f"意图分类置信度 {shown} 低于 {inp.min_confidence:.2f}"]


def _draft_confidence(inp: RouteInput) -> list[str]:
    if inp.draft_confidence >= inp.min_confidence:
        return []
    return [f"草稿信心值 {inp.draft_confidence:.2f} 低于 {inp.min_confidence:.2f}"]


def _no_sensitivity_tags(inp: RouteInput) -> list[str]:
    tags = inp.analysis.sensitivity_tags
    return [f"有敏感标签：{', '.join(t.value for t in tags)}"] if tags else []


def _sentiment_ok(inp: RouteInput) -> list[str]:
    sentiment = inp.analysis.sentiment
    return [f"玩家情绪为 {sentiment.value}"] if sentiment in NEGATIVE else []


def _first_reply(inp: RouteInput) -> list[str]:
    return [] if first_reply(inp.case, inp.analysis.message_seq) else ["不是线程中的第一封客服回复"]


def _no_injection(inp: RouteInput) -> list[str]:
    signals = inp.analysis.injection_signals
    return [f"疑似提示注入：{'；'.join(signals)}"] if signals else []


def _reply_confirmed(inp: RouteInput) -> list[str]:
    problems = []
    if inp.case.status is CaseStatus.UNDETERMINED:
        judged = inp.analysis.thread_judgement
        detail = f"，分析判断为 {judged.value}" if judged else ""
        problems.append(f"案件状态待判断（undetermined{detail}），请确认是否需要回复")
    if inp.analysis.no_reply is not None:
        problems.append(f"分析提议不回复：{inp.analysis.no_reply.reason_zh}")
    return problems


GATES: dict[AutoSendGate, Gate] = {
    AutoSendGate.CHECKS_PASSED: _checks_passed,
    AutoSendGate.TEMPLATE_ALLOWED: _template_allowed,
    AutoSendGate.TEMPLATE_GENERIC: _template_generic,
    AutoSendGate.TEMPLATE_IN_INTENT: _template_in_intent,
    AutoSendGate.INTENT_ELIGIBLE: _intent_eligible,
    AutoSendGate.INTENT_CONFIDENCE: _intent_confidence,
    AutoSendGate.DRAFT_CONFIDENCE: _draft_confidence,
    AutoSendGate.NO_SENSITIVITY_TAGS: _no_sensitivity_tags,
    AutoSendGate.SENTIMENT_OK: _sentiment_ok,
    AutoSendGate.FIRST_REPLY: _first_reply,
    AutoSendGate.NO_INJECTION: _no_injection,
    AutoSendGate.REPLY_CONFIRMED: _reply_confirmed,
}
"""每个关卡的判断。`tests/draft/test_route.py` 检查它覆盖 `AutoSendGate` 的全部取值。"""


@dataclass(frozen=True)
class AutoSendApproval:
    """自动发送的批准：每个关卡的结果（不通过的理由）。"""

    results: Mapping[AutoSendGate, Sequence[str]]

    @property
    def missing(self) -> list[AutoSendGate]:
        """没有结果的关卡。"""
        return [gate for gate in AutoSendGate if gate not in self.results]

    @property
    def granted(self) -> bool:
        """**全部**关卡都有结果并且通过时才为 True。缺少任何一个关卡的结果都不批准。"""
        return not self.missing and not any(self.results.values())

    @property
    def reasons(self) -> list[str]:
        """不批准的理由，按关卡顺序；缺少的关卡也列出。"""
        found = [r for gate in AutoSendGate for r in self.results.get(gate, ())]
        if self.missing:
            found.append(f"缺少关卡结果：{'、'.join(g.value for g in self.missing)}")
        return found


def approve_auto_send(inp: RouteInput) -> AutoSendApproval:
    """逐一评估每个关卡，返回批准。是否自动发送只看 `AutoSendApproval.granted`。"""
    return AutoSendApproval(results={gate: tuple(GATES[gate](inp)) for gate in AutoSendGate})


def route(inp: RouteInput) -> tuple[Route, list[str]]:
    """分流结果及理由（至少一条）。"""
    escalate = escalation_reasons(inp.case, inp.analysis, inp.escalation_triggers)
    if inp.analysis.sentiment is Sentiment.ANGRY:
        escalate.append("玩家情绪非常负面（angry）")
    if inp.candidate is None:
        escalate.append("找不到合适的模板")
    if escalate:
        return Route.ESCALATE, escalate

    approval = approve_auto_send(inp)
    if approval.granted:
        return Route.AUTO_SEND, ["低风险的通用模板，自动发送的全部关卡都通过（MVP 中只是建议）"]
    return Route.REVIEW, approval.reasons


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
    """内容、设备、App 版本和问题发生时间都相同、有可信客服回复、并且**更早提交**的另一个
    线程（最早的）。只看更早的线程：当前案件不能因为之后才提交、之后才得到回复的重复线程
    而得不到草稿。

    样本没有账号和发件地址，只能按内容判断；生产中应按账号或发件地址判断（提案 0003 §8）。
    """
    key = duplicate_key(case)
    if key is None:
        return None
    position = thread_order(case.id)
    found = [
        other
        for other in others
        if thread_order(other.id) < position
        and duplicate_key(other) == key
        and _strong_agent_reply(other)
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
