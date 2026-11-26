"""Alembic 迁移环境。

数据库 URL：通过 `copilot db upgrade` 调用时由 alembic_config() 传入；通过 Alembic 命令行
（读取 alembic.ini）调用时，从 DATABASE_URL 读取。
"""

from alembic import context

from copilot.config import get_settings
from copilot.storage.db import Base, create_engine_for

config = context.config
target_metadata = Base.metadata

if config.config_file_name is not None:
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    assert url, "缺少 sqlalchemy.url"
    engine = create_engine_for(url)
    with engine.connect() as connection:
        # render_as_batch：SQLite 不支持大部分 ALTER TABLE，Alembic 用“建新表再复制”的方式迁移
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
