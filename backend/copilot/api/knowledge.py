"""知识库与指标的端点（#8）。

**只返回已批准的模板版本**。未审核的条目在界面中只能作为参考；其中只有“重复出现”证据的
模板在所有者批准前不能展示给客服（提案 0003 §4、§11），而已发布的 `ReplyTemplate` 中没有
记录证据强度，无法区分，所以一律不返回。草稿引用的模板都是提供给模型的、已批准的版本。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import Engine

from copilot.api.deps import Repo, engine_of
from copilot.contracts import FeedbackAction, InquiryCategory, ReplyTemplate
from copilot.kb.store import KnowledgeStore
from copilot.storage import NotFoundError
from copilot.workbench.metrics import Tally, compute_metrics


def knowledge_store(engine: Annotated[Engine, Depends(engine_of)]) -> KnowledgeStore:
    return KnowledgeStore(engine)


Store = Annotated[KnowledgeStore, Depends(knowledge_store)]
TemplateIdPath = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
"""与 `EntryId` 相同的字符规则。"""


class TemplateList(BaseModel):
    items: list[ReplyTemplate]
    """每个模板当前生效的版本，只含已批准的，按 ID 排序。"""


class MetricsRow(BaseModel):
    key: str
    """模板 ID、分流结果，或 `total`。"""
    handled: int
    """收到过处理的草稿数。"""
    approved: int
    edited: int
    escalated: int
    rejected: int
    no_reply: int
    """客服判断无需回复的草稿数。"""
    approved_rate: float
    """原样批准率。"""
    edited_rate: float
    """修改后批准率。"""
    escalated_rate: float
    rejected_rate: float
    no_reply_rate: float
    avg_edit_distance: float | None
    """发出的回复的平均编辑距离（字符）。没有发出的回复时为空。"""

    @classmethod
    def of(cls, tally: Tally) -> "MetricsRow":
        count = tally.counts.get
        return cls(
            key=tally.key,
            handled=tally.handled,
            approved=count(FeedbackAction.APPROVED, 0),
            edited=count(FeedbackAction.EDITED, 0),
            escalated=count(FeedbackAction.ESCALATED, 0),
            rejected=count(FeedbackAction.REJECTED, 0),
            no_reply=count(FeedbackAction.NO_REPLY, 0),
            approved_rate=tally.rate(FeedbackAction.APPROVED),
            edited_rate=tally.rate(FeedbackAction.EDITED),
            escalated_rate=tally.rate(FeedbackAction.ESCALATED),
            rejected_rate=tally.rate(FeedbackAction.REJECTED),
            no_reply_rate=tally.rate(FeedbackAction.NO_REPLY),
            avg_edit_distance=tally.avg_edit_distance,
        )


class MetricsResponse(BaseModel):
    """每封草稿以最后一次处理为准。"""

    total: MetricsRow
    by_template: list[MetricsRow]
    by_route: list[MetricsRow]


def router() -> APIRouter:
    r = APIRouter(tags=["knowledge"])

    @r.get("/templates")
    def list_templates(
        store: Store,
        intent: Annotated[str | None, Query(max_length=128)] = None,
        category: InquiryCategory | None = None,
    ) -> TemplateList:
        """当前生效、已批准的模板，可按意图和类别筛选。"""
        items = [
            t
            for t in store.current(ReplyTemplate)
            if t.model_visible
            and (intent is None or t.intent == intent)
            and (category is None or t.category is category)
        ]
        return TemplateList(items=sorted(items, key=lambda t: t.id))

    @r.get("/templates/{template_id}")
    def get_template(
        store: Store,
        template_id: TemplateIdPath,
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> ReplyTemplate:
        """一个模板。指定 `version` 时返回该版本（草稿引用的是具体版本），否则返回当前版本。
        未批准的版本按不存在处理。"""
        if version is None:
            found = next((t for t in store.current(ReplyTemplate) if t.id == template_id), None)
        else:
            found = next(
                (
                    t
                    for t in store.entries(ReplyTemplate)
                    if t.id == template_id and t.version == version
                ),
                None,
            )
        if found is None or not found.model_visible:
            raise NotFoundError(f"模板不存在或未批准：{template_id}")
        return found

    @r.get("/metrics", tags=["metrics"])
    def metrics(repo: Repo) -> MetricsResponse:
        """按模板和分流结果统计原样批准率、修改后批准率、升级率、驳回率和平均编辑距离。"""
        result = compute_metrics(repo)
        return MetricsResponse(
            total=MetricsRow.of(result.total),
            by_template=[MetricsRow.of(t) for t in result.by_template],
            by_route=[MetricsRow.of(t) for t in result.by_route],
        )

    return r
