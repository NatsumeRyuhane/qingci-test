"""数据库引擎、ORM 基类与迁移入口。"""

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# 固定约束命名规则：Alembic 的自动迁移和以后换到 Postgres 时都依赖稳定的约束名。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine_for(database_url: str) -> Engine:
    """按 URL 创建引擎。SQLite 文件所在目录不存在时自动创建，并打开外键约束和 WAL。"""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return create_engine(url)

    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL：读写可以并发，适合 API 服务和 CLI 同时访问。仍然只有一个写者（见 D-010）。
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def alembic_config(database_url: str) -> Config:
    """不依赖工作目录中的 alembic.ini，容器和 CLI 中都能用。"""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_database(database_url: str, revision: str = "head") -> None:
    """把数据库迁移到指定版本（默认最新）。"""
    command.upgrade(alembic_config(database_url), revision)
