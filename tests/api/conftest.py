from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot.api import create_app
from copilot.config import Settings
from copilot.storage import upgrade_database


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    """连接一个已迁移的临时 SQLite 数据库的应用。"""
    url = f"sqlite:///{tmp_path / 'copilot.db'}"
    upgrade_database(url)
    return create_app(Settings(database_url=url))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
