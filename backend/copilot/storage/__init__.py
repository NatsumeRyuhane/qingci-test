"""存储层。

业务代码只依赖仓库接口（`Repository`，见 `repository.py`）和向量索引接口，
不直接接触 SQLAlchemy session（D-010）。MVP 的实现基于 SQLite；需要时新增
Postgres + pgvector 实现，并通过同一套接口测试。
"""

from copilot.storage.db import Base, create_engine_for, upgrade_database
from copilot.storage.repository import (
    NotFoundError,
    Page,
    Repository,
    SqlRepository,
    session_scope,
)

__all__ = [
    "Base",
    "NotFoundError",
    "Page",
    "Repository",
    "SqlRepository",
    "create_engine_for",
    "session_scope",
    "upgrade_database",
]
