from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot.api import create_app
from copilot.config import Settings
from copilot.contracts import Case
from copilot.storage import SqlRepository, create_engine_for, session_scope, upgrade_database


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    """连接一个已迁移的临时 SQLite 数据库、使用占位起草服务的应用。"""
    url = f"sqlite:///{tmp_path / 'copilot.db'}"
    upgrade_database(url)
    return create_app(
        Settings(database_url=url, workbench_drafter="stub", workbench_auto_draft=False)
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def seed(app: FastAPI, client: TestClient) -> Callable[..., None]:
    """直接通过仓库写入合成案件（不经过 API）。"""

    def _seed(*cases: Case) -> None:
        engine = create_engine_for(app.state.database_url)
        with session_scope(engine) as session:
            repo = SqlRepository(session)
            for item in cases:
                repo.save_case(item)
        engine.dispose()

    return _seed
