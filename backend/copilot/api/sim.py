"""模拟邮箱的端点（演示用，#8）。

演示时没有真实的邮件系统：来信从这里投递，系统发出的回复都留在发件箱，不会真正发出。
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, Field

from copilot.api.cases import MAX_BODY_CHARS, CaseIdQuery
from copilot.api.deps import AutoDrafterDep, Repo
from copilot.contracts import CaseId, CaseStatus, InquiryForm, OutboundMail
from copilot.workbench import receive_mail


class InboundMailRequest(BaseModel):
    """一封模拟的玩家来信。"""

    case_id: CaseId | None = None
    """对已有案件追问时填写；为空时新建一个线程。"""
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    """邮件正文（日语），按原样保存，清洗规则与导入相同。"""
    form: InquiryForm | None = None
    """游戏内表单，只能在新线程中附带。"""


class InboundMailResponse(BaseModel):
    case_id: str
    message_seq: int
    """这封来信在案件中的 `seq`。"""
    case_status: CaseStatus


class Outbox(BaseModel):
    items: list[OutboundMail]
    total: int


def router() -> APIRouter:
    r = APIRouter(prefix="/sim", tags=["sim"])

    @r.post("/inbox", status_code=201)
    def inbox(
        repo: Repo,
        autodrafter: AutoDrafterDep,
        background: BackgroundTasks,
        body: InboundMailRequest,
    ) -> InboundMailResponse:
        """投递一封玩家来信：新建线程，或对已有案件追问。案件随之出现在工作台的收件箱中，
        开启自动起草时立即开始分析和起草。"""
        case = receive_mail(repo, body=body.body, case_id=body.case_id, form=body.form)
        if autodrafter is not None:
            background.add_task(autodrafter.wake)  # 响应发出（事务已提交）之后再唤醒
        return InboundMailResponse(
            case_id=case.id, message_seq=case.messages[-1].seq, case_status=case.status
        )

    @r.get("/outbox")
    def outbox(
        repo: Repo,
        case_id: CaseIdQuery = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Outbox:
        """发件箱：系统发出的回复，最新的在前，可只看一个案件的。"""
        page = repo.list_outbound_mail(case_id=case_id, limit=limit, offset=offset)
        return Outbox(items=page.items, total=page.total)

    return r
