"""案件与草稿的端点（#8）。只做请求和响应的转换，流程在 `copilot.workbench` 中。"""

from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import AwareDatetime, BaseModel, Field

from copilot.api.deps import DrafterDep, Repo
from copilot.contracts import Case, CaseAnalysis, Draft, Feedback, OutboundMail
from copilot.contracts.case import CASE_ID_CHARS
from copilot.contracts.enums import (
    CaseStatus,
    FeedbackAction,
    InquiryCategory,
    Route,
    Sentiment,
    Urgency,
)
from copilot.draft import latest_player_message
from copilot.storage import NotFoundError
from copilot.workbench import generate_draft, submit_feedback

PREVIEW_CHARS = 80
MAX_BODY_CHARS = 20_000
"""客服编辑后的正文上限。远大于样本中最长的回复，只用来拒绝异常的请求。"""

CaseIdPath = Annotated[str, Path(pattern=rf"^{CASE_ID_CHARS}$")]
CaseIdQuery = Annotated[str | None, Query(pattern=rf"^{CASE_ID_CHARS}$")]
DraftIdPath = Annotated[str, Path(min_length=1, max_length=64)]


class CaseSummary(BaseModel):
    """收件箱中的一行。"""

    id: str
    status: CaseStatus
    category: InquiryCategory | None
    """案件的类别（`Case.category`）：有分析结果时是分析得出的类别，否则是表单中玩家的选择。"""
    route: Route | None
    """最新草稿的分流结果，还没有草稿时为空。"""
    summary_zh: str | None
    """最新分析的中文摘要，还没有分析时为空。"""
    urgency: Urgency | None
    sentiment: Sentiment | None
    preview_ja: str
    """最新一封玩家邮件的开头（日语），没有分析时可以先看这个。"""
    message_count: int
    last_message_at: AwareDatetime | None
    """最新一封邮件的发送时间，未知时为空。"""


class CaseList(BaseModel):
    items: list[CaseSummary]
    total: int
    """满足筛选条件的案件总数。"""


class CaseDetail(BaseModel):
    """案件详情：线程、最新分析、最新草稿及其反馈。"""

    case: Case
    analysis: CaseAnalysis | None
    draft: Draft | None
    feedback: list[Feedback]
    """最新草稿收到的反馈，按时间顺序。"""


class DraftRequest(BaseModel):
    template_id: str | None = Field(default=None, max_length=128)
    """客服指定的模板 ID，不指定时由起草服务选择。"""


class BacktranslateRequest(BaseModel):
    body_ja: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    """客服编辑后的日语正文。"""


class BacktranslateResponse(BaseModel):
    back_translation_zh: str


class FeedbackRequest(BaseModel):
    """客服对草稿的处理。编辑距离和时间由服务端计算。"""

    actor: str = Field(min_length=1, max_length=64)
    """界面上选择的“当前客服”（MVP 中不是身份，见 #13）。"""
    action: FeedbackAction
    final_body_ja: str | None = Field(default=None, max_length=MAX_BODY_CHARS)
    """修改后批准时必填：修改后的正文。"""
    reason: str | None = Field(default=None, max_length=2000)
    """升级处理和驳回时必填，修改后批准时可选。"""


class FeedbackResponse(BaseModel):
    feedback: Feedback
    mail: OutboundMail | None
    """批准或修改后批准时进入发件箱的回复。"""
    case_status: CaseStatus
    """处理后的案件状态。"""


def _summary(repo: Repo, case: Case) -> CaseSummary:
    analysis = repo.latest_analysis(case.id)
    draft = repo.latest_draft(case.id)
    try:
        body = latest_player_message(case).body
    except ValueError:
        body = case.messages[-1].body
    return CaseSummary(
        id=case.id,
        status=case.status,
        category=case.category,
        route=draft.route if draft else None,
        summary_zh=analysis.summary_zh if analysis else None,
        urgency=analysis.urgency if analysis else None,
        sentiment=analysis.sentiment if analysis else None,
        preview_ja=" ".join(body.split())[:PREVIEW_CHARS],
        message_count=len(case.messages),
        last_message_at=case.messages[-1].sent_at,
    )


def _detail(repo: Repo, case_id: str) -> CaseDetail:
    case = repo.get_case(case_id)
    if case is None:
        raise NotFoundError(f"案件不存在：{case_id}")
    draft = repo.latest_draft(case_id)
    return CaseDetail(
        case=case,
        analysis=repo.latest_analysis(case_id),
        draft=draft,
        feedback=repo.list_feedback(draft_id=draft.id) if draft else [],
    )


def router() -> APIRouter:
    r = APIRouter(tags=["cases"])

    @r.get("/cases")
    def list_cases(
        repo: Repo,
        status: CaseStatus | None = None,
        category: InquiryCategory | None = None,
        route: Route | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> CaseList:
        """收件箱：按最近更新时间倒序列出案件，可按状态、类别和分流结果筛选。"""
        page = repo.list_cases(
            status=status, category=category, route=route, limit=limit, offset=offset
        )
        return CaseList(items=[_summary(repo, case) for case in page.items], total=page.total)

    @r.get("/cases/{case_id}")
    def get_case(repo: Repo, case_id: CaseIdPath) -> CaseDetail:
        """案件详情。"""
        return _detail(repo, case_id)

    @r.post("/cases/{case_id}/drafts", status_code=201)
    def create_draft(
        repo: Repo, drafter: DrafterDep, case_id: CaseIdPath, body: DraftRequest
    ) -> CaseDetail:
        """为最新一封玩家邮件（重新）生成分析和草稿。已结案的案件返回 409。"""
        generate_draft(repo, drafter, case_id, template_id=body.template_id)
        return _detail(repo, case_id)

    @r.post("/drafts/{draft_id}/backtranslate")
    def backtranslate(
        repo: Repo, drafter: DrafterDep, draft_id: DraftIdPath, body: BacktranslateRequest
    ) -> BacktranslateResponse:
        """把客服编辑后的日语正文回译为中文。不修改草稿本身。"""
        if repo.get_draft(draft_id) is None:
            raise NotFoundError(f"草稿不存在：{draft_id}")
        return BacktranslateResponse(back_translation_zh=drafter.backtranslate(body.body_ja))

    @r.post("/drafts/{draft_id}/feedback", status_code=201)
    def create_feedback(
        repo: Repo, draft_id: DraftIdPath, body: FeedbackRequest
    ) -> FeedbackResponse:
        """批准（进入发件箱）、修改后批准、升级处理或驳回。

        只能处理案件的最新草稿；案件已结案或玩家在起草后又来信时返回 409。
        """
        result = submit_feedback(
            repo,
            draft_id,
            actor=body.actor,
            action=body.action,
            final_body_ja=body.final_body_ja,
            reason=body.reason,
        )
        return FeedbackResponse(
            feedback=result.feedback, mail=result.mail, case_status=result.case.status
        )

    return r
