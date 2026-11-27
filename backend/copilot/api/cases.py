"""案件与草稿的端点（#8）。只做请求和响应的转换，流程在 `copilot.workbench` 中。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import AwareDatetime, BaseModel, Field

from copilot.api.deps import AutoDrafterDep, DrafterDep, Repo
from copilot.contracts import (
    Case,
    CaseAnalysis,
    Draft,
    DraftCheck,
    DraftCheckKind,
    Feedback,
    OutboundMail,
)
from copilot.contracts.case import CASE_ID_CHARS
from copilot.contracts.enums import (
    CaseStatus,
    FeedbackAction,
    InquiryCategory,
    Route,
    Sentiment,
    Urgency,
)
from copilot.storage import NotFoundError
from copilot.storage.repository import Sort
from copilot.workbench import (
    ACTIONABLE_STATUSES,
    ConflictError,
    edited_checks,
    generate_draft,
    latest_player_message,
    submit_feedback,
)
from copilot.workbench.autodraft import AutoDrafter

PREVIEW_CHARS = 80
MAX_BODY_CHARS = 20_000
"""客服编辑后的正文上限。远大于样本中最长的回复，只用来拒绝异常的请求。"""

CaseIdPath = Annotated[str, Path(pattern=rf"^{CASE_ID_CHARS}$")]
CaseIdQuery = Annotated[str | None, Query(pattern=rf"^{CASE_ID_CHARS}$")]
DraftIdPath = Annotated[str, Path(min_length=1, max_length=64)]


class DraftingStatus(BaseModel):
    """后台自动起草的状态。没有在进行、也没有失败时为空。"""

    state: Literal["queued", "running", "failed"]
    """排队中、起草中，或失败（不会自动重试，可以手动生成）。"""
    since: AwareDatetime
    error: str | None = None
    """失败的原因（中文），不含邮件内容。"""


def _drafting(autodrafter: AutoDrafter | None, case_id: str) -> DraftingStatus | None:
    job = autodrafter.status(case_id) if autodrafter else None
    if job is None:
        return None
    return DraftingStatus(state=job.state, since=job.updated_at, error=job.error)


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
    drafting: DraftingStatus | None = None
    """后台自动起草的状态。"""


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
    drafting: DraftingStatus | None = None
    """后台自动起草的状态。"""


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
    """升级处理、驳回和无需回复时必填，修改后发送时可选。"""
    final_body_zh: str | None = Field(default=None, max_length=MAX_BODY_CHARS)
    """客服编辑的中文回复，发送时记录。"""
    ja_edited_directly: bool = False
    """客服是否直接改过日语正文。"""
    confirmed_failed_checks: list[DraftCheckKind] = Field(default_factory=list)
    """客服确认后仍然发送的未通过自检项。发出的正文有未确认的未通过项时拒绝发送（422）。"""


class TranslateRequest(BaseModel):
    body_zh: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    """客服编辑的中文回复。"""
    base_zh: str | None = Field(default=None, max_length=MAX_BODY_CHARS)
    """上一次对齐时的中文（草稿的回译，或上一次翻译时的中文）。与 `base_ja` 一起给出时，
    只重新翻译改过的段落。"""
    base_ja: str | None = Field(default=None, max_length=MAX_BODY_CHARS)
    """与 `base_zh` 逐段对应的日语（可以包含客服直接改过的段落）。"""


class CheckRequest(BaseModel):
    body_ja: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    """要检查的日语正文（客服直接改过的，或由中文翻译得到的）。"""


class EditResult(BaseModel):
    body_ja: str
    checks: list[DraftCheck]
    """对 `body_ja` 的自检：只依赖正文的检查重新运行，模板有效期沿用草稿的结果。"""


class FeedbackResponse(BaseModel):
    feedback: Feedback
    mail: OutboundMail | None
    """批准或修改后批准时进入发件箱的回复。"""
    case_status: CaseStatus
    """处理后的案件状态。"""


def _summary(repo: Repo, case: Case, autodrafter: AutoDrafter | None = None) -> CaseSummary:
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
        drafting=_drafting(autodrafter, case.id),
    )


def _require_draft(repo: Repo, draft_id: str) -> Draft:
    draft = repo.get_draft(draft_id)
    if draft is None:
        raise NotFoundError(f"草稿不存在：{draft_id}")
    return draft


def _detail(repo: Repo, case_id: str, autodrafter: AutoDrafter | None = None) -> CaseDetail:
    case = repo.get_case(case_id)
    if case is None:
        raise NotFoundError(f"案件不存在：{case_id}")
    draft = repo.latest_draft(case_id)
    return CaseDetail(
        case=case,
        analysis=repo.latest_analysis(case_id),
        draft=draft,
        feedback=repo.list_feedback(draft_id=draft.id) if draft else [],
        drafting=_drafting(autodrafter, case_id),
    )


def router() -> APIRouter:
    r = APIRouter(tags=["cases"])

    @r.get("/cases")
    def list_cases(
        repo: Repo,
        autodrafter: AutoDrafterDep,
        status: CaseStatus | None = None,
        category: InquiryCategory | None = None,
        route: Route | None = None,
        actionable: bool = False,
        sort: Sort = "newest",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> CaseList:
        """收件箱：按最近更新时间列出案件（`sort=oldest` 为先进先出），可按状态、类别和分流结果
        筛选。`actionable=true` 只列需要人工处理的案件（未结案的）。"""
        page = repo.list_cases(
            status=status,
            statuses=ACTIONABLE_STATUSES if actionable else None,
            category=category,
            route=route,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        return CaseList(
            items=[_summary(repo, case, autodrafter) for case in page.items], total=page.total
        )

    @r.get("/cases/{case_id}")
    def get_case(repo: Repo, autodrafter: AutoDrafterDep, case_id: CaseIdPath) -> CaseDetail:
        """案件详情。"""
        return _detail(repo, case_id, autodrafter)

    @r.post("/cases/{case_id}/drafts", status_code=201)
    def create_draft(
        repo: Repo, drafter: DrafterDep, autodrafter: AutoDrafterDep, case_id: CaseIdPath
    ) -> CaseDetail:
        """为最新一封玩家邮件（重新）生成分析和草稿。

        “不回复”自动生效时没有草稿，案件变为 `no_reply_needed`。已结案的案件返回 409；
        起草服务未配置时返回 503，调用失败时返回 502。
        """
        if autodrafter is not None and autodrafter.busy(case_id):
            raise ConflictError("正在自动分析和起草，请稍候")
        generate_draft(repo, drafter, case_id)
        if autodrafter is not None:
            autodrafter.forget(case_id)
        return _detail(repo, case_id, autodrafter)

    @r.post("/drafts/{draft_id}/backtranslate")
    def backtranslate(
        repo: Repo, drafter: DrafterDep, draft_id: DraftIdPath, body: BacktranslateRequest
    ) -> BacktranslateResponse:
        """把客服编辑后的日语正文回译为中文。不修改草稿本身。"""
        _require_draft(repo, draft_id)
        return BacktranslateResponse(back_translation_zh=drafter.backtranslate(body.body_ja))

    @r.post("/drafts/{draft_id}/translate")
    def translate(
        repo: Repo, drafter: DrafterDep, draft_id: DraftIdPath, body: TranslateRequest
    ) -> EditResult:
        """把客服编辑的中文回复译成日语（LLM），并对译文自检。不修改草稿本身。

        给出基准时只重新翻译改过的段落，没有改过的段落保留基准日语；没有修改时不调用 LLM。"""
        draft = _require_draft(repo, draft_id)
        body_ja = drafter.translate(body.body_zh, base_zh=body.base_zh, base_ja=body.base_ja)
        return EditResult(body_ja=body_ja, checks=edited_checks(drafter, draft, body_ja))

    @r.post("/drafts/{draft_id}/check")
    def check(
        repo: Repo, drafter: DrafterDep, draft_id: DraftIdPath, body: CheckRequest
    ) -> EditResult:
        """对客服直接改过的日语正文自检。只在代码中运行，不调用 LLM。"""
        draft = _require_draft(repo, draft_id)
        return EditResult(body_ja=body.body_ja, checks=edited_checks(drafter, draft, body.body_ja))

    @r.post("/drafts/{draft_id}/feedback", status_code=201)
    def create_feedback(
        repo: Repo, drafter: DrafterDep, draft_id: DraftIdPath, body: FeedbackRequest
    ) -> FeedbackResponse:
        """批准（进入发件箱）、修改后发送、升级处理、驳回或无需回复。

        只能处理案件的最新草稿；案件已结案或玩家在起草后又来信时返回 409。发出的正文有
        未确认的未通过自检时返回 422。
        """
        result = submit_feedback(
            repo,
            draft_id,
            actor=body.actor,
            action=body.action,
            drafter=drafter,
            final_body_ja=body.final_body_ja,
            reason=body.reason,
            final_body_zh=body.final_body_zh,
            ja_edited_directly=body.ja_edited_directly,
            confirmed_failed_checks=body.confirmed_failed_checks,
        )
        return FeedbackResponse(
            feedback=result.feedback, mail=result.mail, case_status=result.case.status
        )

    return r
