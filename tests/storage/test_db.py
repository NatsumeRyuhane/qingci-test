from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from typer.testing import CliRunner

from copilot.cli import app
from copilot.storage import create_engine_for, upgrade_database


def test_sqlite_engine_creates_parent_directory_and_sets_pragmas(tmp_path: Path) -> None:
    db_file = tmp_path / "nested" / "copilot.db"
    engine = create_engine_for(f"sqlite:///{db_file}")

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    assert db_file.exists()
    engine.dispose()


def test_upgrade_creates_version_table(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'copilot.db'}"

    upgrade_database(url)

    engine = create_engine_for(url)
    assert "alembic_version" in inspect(engine).get_table_names()
    engine.dispose()


def test_cli_db_upgrade_uses_database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from copilot.config import get_settings

    db_file = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["db", "upgrade"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0, result.output
    assert db_file.exists()


def test_migrations_have_a_single_head() -> None:
    """并行的分支各自新增迁移时，合并后会出现两个 head，`copilot db upgrade` 会失败。
    这时把后合入的那条链的第一个迁移的 `down_revision` 改为另一条链的 head。"""
    from alembic.script import ScriptDirectory

    from copilot.storage.db import alembic_config

    heads = ScriptDirectory.from_config(alembic_config("sqlite://")).get_heads()
    assert len(heads) == 1, f"迁移有多个 head：{heads}"
