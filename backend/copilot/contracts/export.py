"""导出契约模型的 JSON Schema，供 REST API（#10）和 A2A（#11）对外发布。"""

import json
from pathlib import Path
from typing import Any

from copilot.contracts import EXPORTED_MODELS

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def json_schemas() -> dict[str, dict[str, Any]]:
    """每个导出模型的 JSON Schema（校验模式，即系统接受什么样的输入），按模型名索引。"""
    return {
        model.__name__: {"$schema": JSON_SCHEMA_DIALECT, **model.model_json_schema()}
        for model in EXPORTED_MODELS
    }


def dumps(schema: Any) -> str:
    """稳定的 JSON 文本：键排序、保留中文，便于比较差异。"""
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_json_schemas(out_dir: Path) -> list[Path]:
    """把每个模型写成 `<out_dir>/<模型名>.schema.json`，返回写入的文件。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in json_schemas().items():
        path = out_dir / f"{name}.schema.json"
        path.write_text(dumps(schema), encoding="utf-8")
        written.append(path)
    return written
