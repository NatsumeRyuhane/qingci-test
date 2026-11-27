"""坐席流程的服务函数。只依赖 `Repository` 和 `Drafter` 接口。"""

from dataclasses import dataclass

from copilot.contracts import Case, Feedback, Message, OutboundMail, RoleEvidence
from copilot.contracts.enums import CaseStatus, FeedbackAction, MessageRole, RoleEvidenceKind
from copilot.draft import Drafter, DraftResult, latest_player_message
from copilot.storage import NotFoundError, Repository
from copilot.storage.repository import Clock, utc_now
from copilot.workbench.text import levenshtein

CLOSED_STATUSES = frozenset({CaseStatus.REPLIED, CaseStatus.NO_REPLY_NEEDED})
"""已结案的状态。玩家追问会让案件回到 `open`，在那之前不能再起草或处理。"""

SENT_BY_WORKBENCH = "由工作台发出（#8）"
"""系统发出的回复的角色依据说明。发件人是我们自己，所以是强证据（`metadata`）。"""


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


def generate_draft(
    repo: Repository, drafter: Drafter, case_id: str, *, template_id: str | None = None
) -> DraftResult:
    """为案件最新一封玩家邮件（重新）分析并起草，保存结果。

    案件变为 `drafted`，类别更新为分析得出的类别；已升级的案件保持 `escalated`，
    重新起草不会撤销升级。
    """
    case = _require_case(repo, case_id)
    if case.status in CLOSED_STATUSES:
        raise ConflictError(f"案件已结案（{case.status.value}），不能起草")
    try:
        result = drafter.draft(case, template_id=template_id)
    except ValueError as exc:  # 例如线程中没有玩家邮件
        raise ConflictError(str(exc)) from exc

    repo.add_analysis(result.analysis)
    repo.add_draft(result.draft)
    # `Case.category` 优先采用分析结果（见契约）
    update: dict[str, object] = {"category": result.analysis.category}
    if case.status is not CaseStatus.ESCALATED:
        update["status"] = CaseStatus.DRAFTED
    repo.save_case(case.model_copy(update=update))
    return result


def submit_feedback(
    repo: Repository,
    draft_id: str,
    *,
    actor: str,
    action: FeedbackAction,
    final_body_ja: str | None = None,
    reason: str | None = None,
    clock: Clock = utc_now,
) -> FeedbackResult:
    """记录客服对草稿的处理，并相应地推进案件。

    - 批准 / 修改后批准：回复进入发件箱，并作为客服邮件追加到案件，案件变为 `replied`
    - 升级处理：案件变为 `escalated`，必须填写理由
    - 驳回：案件回到 `open`，等待重新起草，必须填写理由

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
    if action in {FeedbackAction.ESCALATED, FeedbackAction.REJECTED} and reason is None:
        raise InvalidRequestError("升级处理和驳回需要填写理由")
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

    now = clock()
    feedback = Feedback(
        draft_id=draft.id,
        actor=actor,
        action=action,
        final_body_ja=body,
        reason=reason,
        edit_distance=distance,
        created_at=now,
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
    else:
        case = case.model_copy(update={"status": CaseStatus.OPEN})
    repo.save_case(case)
    return FeedbackResult(feedback=feedback, mail=mail, case=case)
