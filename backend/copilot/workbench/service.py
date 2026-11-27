"""坐席流程的服务函数。只依赖 `Repository` 和 `Drafter` 接口。"""

from collections.abc import Collection
from dataclasses import dataclass

from copilot.contracts import (
    Case,
    Draft,
    DraftCheck,
    Feedback,
    Message,
    OutboundMail,
    RoleEvidence,
)
from copilot.contracts.enums import (
    CaseStatus,
    DraftCheckKind,
    FeedbackAction,
    MessageRole,
    RoleEvidenceKind,
)
from copilot.draft.service import DraftOutcome
from copilot.storage import NotFoundError, Repository
from copilot.storage.repository import Clock, utc_now
from copilot.workbench.drafting import Drafter, latest_player_message
from copilot.workbench.editing import TEXT_INDEPENDENT_CHECKS
from copilot.workbench.text import levenshtein

CLOSED_STATUSES = frozenset({CaseStatus.REPLIED, CaseStatus.NO_REPLY_NEEDED})
"""已结案的状态。玩家追问会让案件回到 `open`，在那之前不能再起草或处理。"""

ACTIONABLE_STATUSES = frozenset(set(CaseStatus) - CLOSED_STATUSES)
"""需要人工处理的状态：待处理、已起草、已升级、待判断。"""

SENT_BY_WORKBENCH = "由工作台发出（#8）"
"""系统发出的回复的角色依据说明。发件人是我们自己，所以是强证据（`metadata`）。"""


NEEDS_REASON = frozenset(
    {FeedbackAction.ESCALATED, FeedbackAction.REJECTED, FeedbackAction.NO_REPLY}
)


def edited_checks(drafter: Drafter, draft: Draft, body_ja: str) -> list[DraftCheck]:
    """编辑后的正文的自检：重新运行只依赖正文的检查，与正文无关的检查沿用草稿原来的结果。"""
    kept = [c for c in draft.checks if c.kind in TEXT_INDEPENDENT_CHECKS]
    return [*drafter.check(body_ja), *kept]


class ConflictError(RuntimeError):
    """请求与案件当前的状态冲突，例如为已回复的案件起草。"""


class InvalidRequestError(ValueError):
    """请求本身不合理，例如“修改后批准”但正文没有修改。"""


@dataclass(frozen=True)
class FeedbackResult:
    feedback: Feedback
    mail: OutboundMail | None
    """批准或修改后批准时进入发件箱的回复，其他操作为空。"""
    case: Case
    """处理后的案件。"""


def _require_case(repo: Repository, case_id: str) -> Case:
    case = repo.get_case(case_id)
    if case is None:
        raise NotFoundError(f"案件不存在：{case_id!r}")
    return case


def generate_draft(repo: Repository, drafter: Drafter, case_id: str) -> DraftOutcome:
    """为案件最新一封玩家邮件（重新）分析并起草，保存结果。

    - 生成了草稿：案件变为起草服务建议的状态（`drafted`，分流为升级时 `escalated`）。
      已升级的案件保持 `escalated`，重新起草不会撤销升级。
    - “不回复”自动生效（#6，提案 0003 §8）：没有草稿，案件变为 `no_reply_needed`。
      保存的分析中 `no_reply` 是最终的判断（代码确认的重复提交，或分析的提议）。
    - 类别更新为分析得出的类别（`Case.category` 优先采用分析结果）。
    """
    case = _require_case(repo, case_id)
    if case.status in CLOSED_STATUSES:
        raise ConflictError(f"案件已结案（{case.status.value}），不能起草")
    try:
        outcome = drafter.draft(case)
    except ValueError as exc:  # 例如线程中没有玩家邮件
        raise ConflictError(str(exc)) from exc
    apply_outcome(repo, case, outcome)
    return outcome


def apply_outcome(repo: Repository, case: Case, outcome: DraftOutcome) -> None:
    """保存起草结果并推进案件（规则见 `generate_draft`）。`case` 是起草时读到的案件。"""
    analysis = outcome.analysis
    if outcome.no_reply is not None:
        analysis = analysis.model_copy(update={"no_reply": outcome.no_reply})
    repo.add_analysis(analysis)
    update: dict[str, object] = {"category": analysis.category}
    if outcome.draft is not None:
        repo.add_draft(outcome.draft)
    if case.status is not CaseStatus.ESCALATED:
        update["status"] = outcome.proposed_status or CaseStatus.DRAFTED
    repo.save_case(case.model_copy(update=update))


def submit_feedback(
    repo: Repository,
    draft_id: str,
    *,
    actor: str,
    action: FeedbackAction,
    drafter: Drafter,
    final_body_ja: str | None = None,
    reason: str | None = None,
    final_body_zh: str | None = None,
    ja_edited_directly: bool = False,
    confirmed_failed_checks: Collection[DraftCheckKind] = (),
    clock: Clock = utc_now,
) -> FeedbackResult:
    """记录客服对草稿的处理，并相应地推进案件。

    - 批准 / 修改后批准：回复进入发件箱，并作为客服邮件追加到案件，案件变为 `replied`。
      发出的正文有未通过的自检时，必须在 `confirmed_failed_checks` 中逐项确认，否则拒绝发送
      （修改后的正文由服务端重新自检，不信任客户端的结果）。
    - 升级处理：案件变为 `escalated`，必须填写理由
    - 驳回：案件回到 `open`，等待重新起草，必须填写理由
    - 无需回复：案件变为 `no_reply_needed`，必须填写理由

    只能处理案件的最新草稿，并且草稿回复的必须是最新一封玩家邮件：玩家在起草之后又来信时，
    旧草稿没有考虑新的内容，需要重新起草。
    """
    draft = repo.get_draft(draft_id)
    if draft is None:
        raise NotFoundError(f"草稿不存在：{draft_id!r}")
    case = _require_case(repo, draft.case_id)
    if case.status in CLOSED_STATUSES:
        raise ConflictError(f"案件已结案（{case.status.value}），不能再处理")
    latest = repo.latest_draft(case.id)
    if latest is None or latest.id != draft.id:
        raise ConflictError("这不是案件的最新草稿，请刷新后处理最新草稿")
    if latest_player_message(case).seq != draft.message_seq:
        raise ConflictError("玩家在起草之后又有来信，请重新起草")

    reason = (reason or "").strip() or None
    if action in NEEDS_REASON and reason is None:
        raise InvalidRequestError("升级处理、驳回和无需回复需要填写理由")
    body: str | None = None
    distance = 0
    if action is FeedbackAction.EDITED:
        if not (final_body_ja or "").strip():
            raise InvalidRequestError("修改后批准需要提供修改后的正文")
        assert final_body_ja is not None
        distance = levenshtein(draft.body_ja, final_body_ja)
        if distance == 0:
            raise InvalidRequestError("正文没有修改，请直接批准")
        body = final_body_ja
    elif action is FeedbackAction.APPROVED and final_body_ja not in {None, draft.body_ja}:
        raise InvalidRequestError("正文有修改，请使用“修改后批准”")

    sends = action in {FeedbackAction.APPROVED, FeedbackAction.EDITED}
    failed: list[DraftCheckKind] = []
    if sends:
        checks = draft.checks if body is None else edited_checks(drafter, draft, body)
        failed = sorted({c.kind for c in checks if not c.passed})
        unconfirmed = [k.value for k in failed if k not in set(confirmed_failed_checks)]
        if unconfirmed:
            raise InvalidRequestError(f"有未通过的自检：{', '.join(unconfirmed)}。确认后才能发送")

    now = clock()
    feedback = Feedback(
        draft_id=draft.id,
        actor=actor,
        action=action,
        final_body_ja=body,
        reason=reason,
        edit_distance=distance,
        created_at=now,
        final_body_zh=(final_body_zh or None) if sends else None,
        ja_edited_directly=ja_edited_directly and sends,
        confirmed_failed_checks=failed,
    )
    repo.add_feedback(feedback)

    mail = None
    if action in {FeedbackAction.APPROVED, FeedbackAction.EDITED}:
        sent = body or draft.body_ja
        seq = case.messages[-1].seq + 1
        reply = Message(
            thread_id=case.id,
            seq=seq,
            role=MessageRole.AGENT,
            raw_body=sent,
            body=sent,
            sent_at=now,
            role_evidence=[RoleEvidence(kind=RoleEvidenceKind.METADATA, detail=SENT_BY_WORKBENCH)],
        )
        mail = OutboundMail(
            case_id=case.id,
            message_seq=seq,
            draft_id=draft.id,
            body_ja=sent,
            sent_by=actor,
            sent_at=now,
        )
        repo.add_outbound_mail(mail)
        case = Case.model_validate(
            case.model_dump() | {"messages": [*case.messages, reply], "status": CaseStatus.REPLIED}
        )
    elif action is FeedbackAction.ESCALATED:
        case = case.model_copy(update={"status": CaseStatus.ESCALATED})
    elif action is FeedbackAction.NO_REPLY:
        case = case.model_copy(update={"status": CaseStatus.NO_REPLY_NEEDED})
    else:
        case = case.model_copy(update={"status": CaseStatus.OPEN})
    repo.save_case(case)
    return FeedbackResult(feedback=feedback, mail=mail, case=case)
