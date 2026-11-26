"""存储层。

业务代码只依赖仓库接口（`CaseRepository` 等，随 #3 的契约定义）和向量索引接口，
不直接接触 SQLAlchemy session（D-010）。MVP 的实现基于 SQLite；需要时新增
Postgres + pgvector 实现，并通过同一套接口测试。
"""

from copilot.storage.db import Base, create_engine_for, upgrade_database

__all__ = ["Base", "create_engine_for", "upgrade_database"]
