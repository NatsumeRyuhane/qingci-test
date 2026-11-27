"""`copilot eval` CLI 冒烟测试（CI 只用合成数据与 fake LLM）。SYNTHETIC：全部是合成数据。"""

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import copilot.cli as cli_module
from copilot.cli import app
from copilot.config import LLMTask, Settings
from copilot.contracts import RoleEvidenceKind, RoleEvidenceStrength
from copilot.eval import Split, split_cases, write_manifest
from copilot.ingest import write_cases
from copilot.ingest.timestamps import JST
from copilot.kb.cluster import MatchKind
from copilot.kb.labels import (
    LABELS_DIR,
    NO_REPLY_FILE,
    REPLIES_FILE,
    ReplyLabel,
    Resolution,
    write_jsonl,
)
from copilot.kb.store import KnowledgeStore
from copilot.llm.fake import FakeLLMClient
from copilot.storage import create_engine_for, upgrade_database
from tests.cases import agent, case, player
from tests.draft.test_service import (
    KNOWLEDGE,
    SNAPSHOT,
    THANKS_BODY,
    analysis_reply,
    draft_reply,
)

T2 = datetime(2024, 3, 5, 12, 0, tzinfo=JST)


def test_eval_cli_cases_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "copilot.db"
    db_url = f"sqlite:///{db_path}"
    upgrade_database(db_url)
    store = KnowledgeStore(create_engine_for(db_url))
    store.add_all(
        [
            *SNAPSHOT.intents,
            *SNAPSHOT.templates,
            *SNAPSHOT.policies,
            *SNAPSHOT.notices,
            *SNAPSHOT.facts,
            *SNAPSHOT.glossary,
        ]
    )

    cases = [
        case(
            str(i),
            player(str(i), 1, f"【合成】ありがとう {i}"),
            agent(str(i), 2, THANKS_BODY, RoleEvidenceKind.METADATA).model_copy(
                update={"sent_at": T2}
            ),
            content=f"【合成】ありがとう {i}",
        )
        for i in range(1, 11)
    ]
    cases_file = tmp_path / "cases.jsonl"
    split_file = tmp_path / "split.json"
    kb_dir = tmp_path / "kb"
    write_cases(cases, cases_file)
    manifest = split_cases(cases)
    write_manifest(manifest, split_file)

    dev_cases = manifest.select(cases, Split.DEV)
    target = dev_cases[0]
    write_jsonl(
        [
            ReplyLabel(
                message_id=f"{target.id}:2",
                split=Split.DEV,
                evidence=RoleEvidenceStrength.STRONG,
                template_id="tpl-syn-thanks",
                template_version=1,
                match=MatchKind.EXACT,
                resolution=Resolution.RESOLVED,
                inquiry_message_id=f"{target.id}:1",
                first_agent_reply=True,
            )
        ],
        kb_dir / LABELS_DIR / REPLIES_FILE,
    )
    write_jsonl([], kb_dir / LABELS_DIR / NO_REPLY_FILE)

    fake = FakeLLMClient()
    fake.queue(LLMTask.ANALYZE, analysis_reply())
    fake.queue(LLMTask.DRAFT, draft_reply())
    fake.queue(LLMTask.BACKTRANSLATE, "【合成】感谢反馈")

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://llm.example/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model_analyze="fake-a",
        llm_model_draft="fake-d",
        llm_model_backtranslate="fake-b",
        database_url=db_url,
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "_llm_client", lambda _s: fake)
    monkeypatch.setattr(cli_module, "_embedding_client", lambda _s: None)

    report = tmp_path / "report.md"
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "--split",
            "dev",
            "--suite",
            "cases",
            "--cases",
            str(cases_file),
            "--split-file",
            str(split_file),
            "--kb-dir",
            str(kb_dir),
            "--knowledge",
            str(KNOWLEDGE),
            "--no-strict-replay",
            "--no-judge",
            "--limit",
            "1",
            "--out",
            str(report),
        ],
    )
    assert result.exit_code == 0, result.output
    text = report.read_text(encoding="utf-8")
    assert "离线评测报告（dev）" in text
    assert "模板 Top-1 准确率" in text
    assert "【合成】ありがとう" not in text  # D-002：报告不含客户原文


def test_eval_cli_rejects_train_split() -> None:
    result = CliRunner().invoke(app, ["eval", "--split", "train"])
    assert result.exit_code == 2


def test_eval_cli_rejects_empty_or_missing_security_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "copilot.db"
    db_url = f"sqlite:///{db_path}"
    upgrade_database(db_url)
    store = KnowledgeStore(create_engine_for(db_url))
    store.add_all([*SNAPSHOT.intents, *SNAPSHOT.templates])

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://llm.example/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model_analyze="fake-a",
        llm_model_draft="fake-d",
        llm_model_backtranslate="fake-b",
        database_url=db_url,
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "_llm_client", lambda _s: FakeLLMClient())

    missing = CliRunner().invoke(
        app,
        [
            "eval",
            "--suite",
            "security",
            "--knowledge",
            str(KNOWLEDGE),
            "--security-dir",
            str(tmp_path / "missing"),
            "--no-judge",
        ],
    )
    assert missing.exit_code == 2

    empty_dir = tmp_path / "empty_sec"
    empty_dir.mkdir()
    empty = CliRunner().invoke(
        app,
        [
            "eval",
            "--suite",
            "security",
            "--knowledge",
            str(KNOWLEDGE),
            "--security-dir",
            str(empty_dir),
            "--no-judge",
        ],
    )
    assert empty.exit_code == 2
