"""模拟邮箱的端点（演示用，#8）。

演示时没有真实的邮件系统：来信从这里投递，系统发出的回复都留在发件箱，不会真正发出。
"""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from copilot.api.cases import CaseIdQuery
from copilot.api.deps import Repo
from copilot.contracts import OutboundMail


class Outbox(BaseModel):
    items: list[OutboundMail]
    total: int


def router() -> APIRouter:
    r = APIRouter(prefix="/sim", tags=["sim"])

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
