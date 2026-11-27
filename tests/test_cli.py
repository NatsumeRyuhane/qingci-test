from typer.testing import CliRunner

from copilot import __version__
from copilot.cli import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
    assert "version" in result.output


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_openapi_outputs_spec() -> None:
    import json

    result = runner.invoke(app, ["openapi"])
    assert result.exit_code == 0
    assert "/v1/health" in json.loads(result.output)["paths"]
