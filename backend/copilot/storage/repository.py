"""仓库接口与 SQLite（SQLAlchemy）实现。

业务代码（API、模拟邮箱、以后的 #10、#11）只依赖 `Repository` 接口（D-010）。
一个 `SqlRepository` 绑定一个 session，自身不提交：调用方用 `session_scope()` 划定事务，
例如 API 的一个请求就是一个事务，“写入反馈 + 进入发件箱 + 更新案件”要么全部成功，要么全部不生效。
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from copilot.contracts import Case, CaseAnalysis, Draft, Feedback, OutboundMail
from copilot.contracts.enums import CaseStatus, InquiryCategory, Route
from copilot.storage.tables import AnalysisRow, CaseRow, DraftRow, FeedbackRow, OutboxRow

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class NotFoundError(LookupError):
    """引用的对象不存在，例如为不存在的案件写草稿。"""


@dataclass(frozen=True)
class Page[T]:
    """分页结果。`total` 是满足筛选条件的总数，不受 `limit` 和 `offset` 影响。"""

    items: list[T]
    total: int


class Repository(Protocol):
    """案件、分析、草稿、反馈与发件箱的存储。"""

    def save_case(self, case: Case) -> None:
        """新建或整体替换一个案件，并把它的更新时间设为现在。"""
        ...

    def get_case(self, case_id: str) -> Case | None: ...

    def list_cases(
        self,
        *,
        status: CaseStatus | None = None,
        category: InquiryCategory | None = None,
        route: Route | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Case]:
        """按更新时间倒序列出案件。`route` 按最新草稿的分流结果筛选。"""
        ...

    def add_analysis(self, analysis: CaseAnalysis) -> None: ...

    def latest_analysis(self, case_id: str) -> CaseAnalysis | None: ...

    def add_draft(self, draft: Draft) -> None:
        """保存草稿，并把它作为案件的最新草稿（收件箱按它的分流结果筛选）。"""
        ...

    def get_draft(self, draft_id: str) -> Draft | None: ...

    def latest_draft(self, case_id: str) -> Draft | None: ...

    def add_feedback(self, feedback: Feedback) -> None: ...

    def list_feedback(self, *, draft_id: str | None = None) -> list[Feedback]:
        """按写入顺序列出反馈，可只列一封草稿的。"""
        ...

    def add_outbound_mail(self, mail: OutboundMail) -> None: ...

    def list_outbound_mail(
        self, *, case_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> Page[OutboundMail]:
        """发件箱，最新发出的在前，可只列一个案件的。"""
        ...


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """一个事务：正常结束时提交，抛出异常时回滚。"""
    with Session(engine) as session, session.begin():
        yield session


class SqlRepository:
    """`Repository` 的 SQLAlchemy 实现。"""

    def __init__(self, session: Session, *, clock: Clock = utc_now) -> None:
        self._session = session
        self._clock = clock

    # --- 案件 ---

    def save_case(self, case: Case) -> None:
        row = self._session.get(CaseRow, case.id)
        if row is None:
            row = CaseRow(id=case.id, route=None)
            self._session.add(row)
        row.status = case.status.value
        row.category = case.category.value if case.category else None
        row.updated_at = self._clock()
        row.doc = case.model_dump(mode="json")
        self._session.flush()

    def get_case(self, case_id: str) -> Case | None:
        row = self._session.get(CaseRow, case_id)
        return None if row is None else Case.model_validate(row.doc)

    def list_cases(
        self,
        *,
        status: CaseStatus | None = None,
        category: InquiryCategory | None = None,
        route: Route | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Case]:
        query = select(CaseRow)
        if status is not None:
            query = query.where(CaseRow.status == status.value)
        if category is not None:
            query = query.where(CaseRow.category == category.value)
        if route is not None:
            query = query.where(CaseRow.route == route.value)
        total = self._session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self._session.scalars(
            query.order_by(CaseRow.updated_at.desc(), CaseRow.id).limit(limit).offset(offset)
        )
        return Page(items=[Case.model_validate(row.doc) for row in rows], total=total)

    def _require_case(self, case_id: str) -> CaseRow:
        row = self._session.get(CaseRow, case_id)
        if row is None:
            raise NotFoundError(f"案件不存在：{case_id!r}")
        return row

    # --- 分析 ---

    def add_analysis(self, analysis: CaseAnalysis) -> None:
        self._require_case(analysis.case_id)
        self._session.add(
            AnalysisRow(
                case_id=analysis.case_id,
                created_at=analysis.created_at,
                doc=analysis.model_dump(mode="json"),
            )
        )
        self._session.flush()

    def latest_analysis(self, case_id: str) -> CaseAnalysis | None:
        row = self._session.scalars(
            select(AnalysisRow)
            .where(AnalysisRow.case_id == case_id)
            .order_by(AnalysisRow.id.desc())
            .limit(1)
        ).first()
        return None if row is None else CaseAnalysis.model_validate(row.doc)

    # --- 草稿 ---

    def add_draft(self, draft: Draft) -> None:
        case_row = self._require_case(draft.case_id)
        self._session.add(
            DraftRow(
                id=draft.id,
                case_id=draft.case_id,
                created_at=draft.created_at,
                doc=draft.model_dump(mode="json"),
            )
        )
        case_row.route = draft.route.value
        case_row.updated_at = self._clock()
        self._session.flush()

    def get_draft(self, draft_id: str) -> Draft | None:
        row = self._session.scalars(select(DraftRow).where(DraftRow.id == draft_id)).first()
        return None if row is None else Draft.model_validate(row.doc)

    def latest_draft(self, case_id: str) -> Draft | None:
        row = self._session.scalars(
            select(DraftRow)
            .where(DraftRow.case_id == case_id)
            .order_by(DraftRow.row_id.desc())
            .limit(1)
        ).first()
        return None if row is None else Draft.model_validate(row.doc)

    # --- 反馈 ---

    def add_feedback(self, feedback: Feedback) -> None:
        if self.get_draft(feedback.draft_id) is None:
            raise NotFoundError(f"草稿不存在：{feedback.draft_id!r}")
        self._session.add(
            FeedbackRow(
                draft_id=feedback.draft_id,
                action=feedback.action.value,
                created_at=feedback.created_at,
                doc=feedback.model_dump(mode="json"),
            )
        )
        self._session.flush()

    def list_feedback(self, *, draft_id: str | None = None) -> list[Feedback]:
        query = select(FeedbackRow).order_by(FeedbackRow.id)
        if draft_id is not None:
            query = query.where(FeedbackRow.draft_id == draft_id)
        return [Feedback.model_validate(row.doc) for row in self._session.scalars(query)]

    # --- 发件箱 ---

    def add_outbound_mail(self, mail: OutboundMail) -> None:
        self._require_case(mail.case_id)
        if self.get_draft(mail.draft_id) is None:
            raise NotFoundError(f"草稿不存在：{mail.draft_id!r}")
        self._session.add(
            OutboxRow(
                id=mail.id,
                case_id=mail.case_id,
                draft_id=mail.draft_id,
                sent_at=mail.sent_at,
                doc=mail.model_dump(mode="json"),
            )
        )
        self._session.flush()

    def list_outbound_mail(
        self, *, case_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> Page[OutboundMail]:
        query = select(OutboxRow)
        if case_id is not None:
            query = query.where(OutboxRow.case_id == case_id)
        total = self._session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self._session.scalars(
            query.order_by(OutboxRow.row_id.desc()).limit(limit).offset(offset)
        )
        return Page(items=[OutboundMail.model_validate(row.doc) for row in rows], total=total)
