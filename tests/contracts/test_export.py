import json
from pathlib import Path

from typer.testing import CliRunner

from copilot.cli import app
from copilot.contracts import EXPORTED_MODELS
from copilot.contracts.export import JSON_SCHEMA_DIALECT, json_schemas

runner = CliRunner()
NAMES = {model.__name__ for model in EXPORTED_MODELS}


def test_json_schemas_cover_exported_models() -> None:
    schemas = json_schemas()
    assert set(schemas) == NAMES
    for name, schema in schemas.items():
        assert schema["$schema"] == JSON_SCHEMA_DIALECT
        assert schema["title"] == name
        assert schema["description"], f"{name} 的 schema 缺少说明"


def test_schema_carries_field_docs_and_version() -> None:
    case = json_schemas()["Case"]
    assert case["properties"]["schema_version"]["const"] == 1
    assert "预留（#13）" in case["properties"]["queue"]["description"]
    assert case["additionalProperties"] is False


def test_export_to_stdout() -> None:
    result = runner.invoke(app, ["contracts", "export"])
    assert result.exit_code == 0, result.output
    assert set(json.loads(result.output)) == NAMES


def test_export_to_directory(tmp_path: Path) -> None:
    result = runner.invoke(app, ["contracts", "export", "--out-dir", str(tmp_path / "schemas")])
    assert result.exit_code == 0, result.output
    files = sorted((tmp_path / "schemas").glob("*.schema.json"))
    assert {path.name.removesuffix(".schema.json") for path in files} == NAMES
    for path in files:
        assert json.loads(path.read_text(encoding="utf-8"))["$schema"] == JSON_SCHEMA_DIALECT
