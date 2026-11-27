"""`copilot draft` 命令行（#6）。SYNTHETIC：全部是合成数据。不读取本地 .env、数据库和样本。"""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from copilot import cli
from copilot.config import LLMTask, Settings
from copilot.draft.service import DraftOutcome
from copilot.ingest import write_cases
from copilot.kb.store import KnowledgeStore
from copilot.llm.fake import FakeLLMClient
from copilot.storage import create_engine_for, upgrade_database
from tests.draft.test_service import (
    KNOWLEDGE,
    SNAPSHOT,
    THANKS_CASE,
    analysis_reply,
    draft_reply,
)

runner = CliRunner()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    url = f"sqlite:///{tmp_path / 'copilot.db'}"
    upgrade_database(url)
    KnowledgeStore(create_engine_for(url)).add_all(
        [*SNAPSHOT.intents, *SNAPSHOT.templates, *SNAPSHOT.policies]
    )
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=url,
        llm_base_url="https://llm.example/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model_analyze="m-analyze",
        llm_model_draft="m-draft",
        llm_model_backtranslate="m-back",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    def fake_client(_: Settings) -> FakeLLMClient:
        fake = FakeLLMClient()
        fake.queue(LLMTask.ANALYZE, analysis_reply())
        fake.queue(LLMTask.DRAFT, draft_reply())
        fake.queue(LLMTask.BACKTRANSLATE, "【合成】回译")
        return fake

    monkeypatch.setattr(cli, "_llm_client", fake_client)
    monkeypatch.chdir(tmp_path)  # embedding 缓存等默认路径都落在临时目录
    cases = tmp_path / "cases.jsonl"
    write_cases([THANKS_CASE], cases)
    return {"cases": cases, "tmp": tmp_path}


def _args(env: dict[str, Path], *extra: str) -> list[str]:
    return [
        "draft", "900", "--cases", str(env["cases"]), "--split", str(env["tmp"] / "none.json"),
        "--knowledge", str(KNOWLEDGE), "--now", "2099-03-05T12:00", *extra,
    ]  # fmt: skip


def test_draft_prints_summary_and_usage(env: dict[str, Path]) -> None:
    result = runner.invoke(cli.app, _args(env))
    assert result.exit_code == 0, result.output
    assert "分流：auto_send" in result.output
    assert "只用 BM25" in result.output
    assert "合计：3 次" in result.output


def test_draft_json_and_out_file(env: dict[str, Path]) -> None:
    out = env["tmp"] / "outcome.json"
    result = runner.invoke(cli.app, _args(env, "--json", "--out", str(out)))
    assert result.exit_code == 0, result.output
    outcome = DraftOutcome.model_validate_json(result.output)
    assert outcome.draft is not None and outcome.draft.case_id == "900"
    assert json.loads(out.read_text(encoding="utf-8"))["analysis"]["intent"] == "syn_thanks"


def test_unknown_case_and_bad_time(env: dict[str, Path]) -> None:
    args = _args(env)
    args[1] = "999"
    assert runner.invoke(cli.app, args).exit_code == 1
    bad = runner.invoke(cli.app, [*_args(env)[:-2], "--now", "昨日"])
    assert bad.exit_code == 2


def test_missing_model_configuration(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    result = runner.invoke(cli.app, _args(env))
    assert result.exit_code == 1
    assert "LLM_MODEL_ANALYZE" in result.output
