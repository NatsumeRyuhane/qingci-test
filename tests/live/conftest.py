import pytest


@pytest.fixture(autouse=True)
def _isolate_environment() -> None:
    """覆盖 tests/conftest.py 中的同名 fixture：live 测试需要读取真实的环境变量和 .env。"""
