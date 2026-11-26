"""合成测试数据（SYNTHETIC，D-002）。说明见 tests/fixtures/README.md。"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

FIXTURES_DIR = Path(__file__).parent
CONTRACTS_DIR = FIXTURES_DIR / "contracts"


def load_items(name: str) -> list[dict[str, Any]]:
    """读取 `contracts/<name>.json` 中的 `items`（原始 JSON 对象）。"""
    data = json.loads((CONTRACTS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert data["_synthetic"].startswith("SYNTHETIC"), f"{name}.json 缺少 SYNTHETIC 标注"
    items: list[dict[str, Any]] = data["items"]
    return items


def load_models[M: BaseModel](model: type[M], name: str) -> list[M]:
    """读取 `contracts/<name>.json` 并校验为契约模型。"""
    return [model.model_validate(item) for item in load_items(name)]
