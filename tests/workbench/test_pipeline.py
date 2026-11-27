"""`PipelineDrafter`：通过工作台调用 #6 的起草流水线。

SYNTHETIC：合成的知识库（与 tests/draft/test_service.py 相同）和 fake LLM，不读取本地 .env、
数据库和样本。
"""

from pathlib import Path

import pytest
from pydantic import SecretStr

from copilot.config import LLMTask, Settings
from copilot.contracts import CaseStatus, Route
from copilot.ingest import write_cases
from copilot.kb.store import KnowledgeStore
from copilot.llm import LLMOutputError
from copilot.llm.fake import FakeLLMClient
from copilot.storage import create_engine_for, upgrade_database
from copilot.workbench import DrafterFailedError, DrafterUnavailableError
from copilot.workbench.pipeline import PipelineDrafter
from tests.draft.test_service import (
    KNOWLEDGE,
    NOW,
    SNAPSHOT,
    THANKS_CASE,
    analysis_reply,
    draft_reply,
)


def _settings(url: str, **extra: object) -> Settings:
    values: dict[str, object] = {
        "database_url": url,
        "llm_base_url": "https://llm.example/v1",
        "llm_api_key": SecretStr("test-key"),
        "llm_model_analyze": "m-analyze",
        "llm_model_draft": "m-draft",
        "llm_model_backtranslate": "m-back",
    } | extra
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


@pytest.fixture
def url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'copilot.db'}"
    upgrade_database(url)
    return url


@pytest.fixture
def publish(url: str) -> None:
    KnowledgeStore(create_engine_for(url)).add_all(
        [*SNAPSHOT.intents, *SNAPSHOT.templates, *SNAPSHOT.policies]
    )


def _drafter(url: str, tmp_path: Path, fake: FakeLLMClient, **extra: object) -> PipelineDrafter:
    cases = tmp_path / "cases.jsonl"
    write_cases([THANKS_CASE], cases)
    return PipelineDrafter(
        _settings(url, **extra),
        llm=fake,
        embedder=None,
        knowledge_dir=KNOWLEDGE,
        cases_path=cases,
        split_path=tmp_path / "no-split.json",
        clock=lambda: NOW,
        now=lambda: NOW,
    )


@pytest.mark.usefixtures("publish")
def test_drafts_with_the_real_pipeline(url: str, tmp_path: Path) -> None:
    fake = FakeLLMClient()
    fake.queue(LLMTask.ANALYZE, analysis_reply())
    fake.queue(LLMTask.DRAFT, draft_reply())
    fake.queue(LLMTask.BACKTRANSLATE, "【合成】回译")
    outcome = _drafter(url, tmp_path, fake).draft(THANKS_CASE.model_copy(update={"id": "901"}))

    assert outcome.draft is not None
    assert outcome.draft.route is Route.AUTO_SEND
    assert outcome.draft.back_translation_zh == "【合成】回译"
    assert outcome.draft.time_context is not None
    assert outcome.proposed_status is CaseStatus.DRAFTED


@pytest.mark.usefixtures("publish")
def test_backtranslate(url: str, tmp_path: Path) -> None:
    fake = FakeLLMClient()
    fake.queue(LLMTask.BACKTRANSLATE, "【合成】编辑后的回译")
    assert _drafter(url, tmp_path, fake).backtranslate("【合成データ】") == "【合成】编辑后的回译"


def test_unpublished_knowledge_base_is_unavailable(url: str, tmp_path: Path) -> None:
    with pytest.raises(DrafterUnavailableError, match="kb publish"):
        _drafter(url, tmp_path, FakeLLMClient()).draft(THANKS_CASE)


@pytest.mark.usefixtures("publish")
def test_missing_model_configuration_is_unavailable(url: str, tmp_path: Path) -> None:
    drafter = _drafter(url, tmp_path, FakeLLMClient(), llm_model_draft=None)
    with pytest.raises(DrafterUnavailableError, match="LLM_MODEL_DRAFT"):
        drafter.draft(THANKS_CASE)
    with pytest.raises(DrafterUnavailableError):
        drafter.backtranslate("x")


@pytest.mark.usefixtures("publish")
def test_llm_failure_is_reported_without_details(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLLMClient()

    async def broken(*_: object, **__: object) -> str:
        raise LLMOutputError("含有邮件内容的错误信息")

    monkeypatch.setattr(fake, "complete_text", broken)
    with pytest.raises(DrafterFailedError) as excinfo:
        _drafter(url, tmp_path, fake).backtranslate("x")
    assert "邮件内容" not in str(excinfo.value)
