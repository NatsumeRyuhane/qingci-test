import pytest

from copilot.config import Settings


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉进程环境中与配置同名的变量（例如开发者 shell 或 llm-live job 中设置的），
    让测试只看自己传入的值。live 测试在 tests/live/conftest.py 中关掉这个隔离。"""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
