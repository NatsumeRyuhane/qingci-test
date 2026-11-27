"""ORM 表。只在存储层内部使用，业务代码通过 `copilot.storage.repository` 访问（D-010）。

每张表保存契约对象的完整 JSON（`doc`），另外把筛选和排序要用的字段拆成单独的列。
契约是唯一的数据定义：读出时用契约模型校验 `doc`，拆出的列只是索引，写入时由仓库同步。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from copilot.storage.db import Base


class CaseRow(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str | None] = mapped_column(String(32), index=True)
    route: Mapped[str | None] = mapped_column(String(32), index=True)
    """最新草稿的分流结果，没有草稿时为空。收件箱按它筛选。"""
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    """最近一次写入的时间（来信、生成草稿、处理）。收件箱按它倒序排列。"""
    doc: Mapped[dict[str, object]] = mapped_column(JSON)


class AnalysisRow(Base):
    __tablename__ = "case_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    doc: Mapped[dict[str, object]] = mapped_column(JSON)


class DraftRow(Base):
    __tablename__ = "drafts"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    """写入顺序，用来确定“最新草稿”。`created_at` 可能相同，不能单独依赖它。"""
    id: Mapped[str] = mapped_column(String(64), unique=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    doc: Mapped[dict[str, object]] = mapped_column(JSON)


class FeedbackRow(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    doc: Mapped[dict[str, object]] = mapped_column(JSON)


class OutboxRow(Base):
    __tablename__ = "outbox"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    """写入顺序，发件箱按它倒序排列。"""
    id: Mapped[str] = mapped_column(String(64), unique=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id", ondelete="CASCADE"))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    doc: Mapped[dict[str, object]] = mapped_column(JSON)
