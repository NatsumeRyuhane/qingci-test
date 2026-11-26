from pathlib import Path

import pytest
from pydantic import SecretStr

from copilot.config import LLMTask, MissingConfigError, Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def make_settings(**values: object) -> Settings:
    # _env_file=None：不读取开发者本地的 .env，测试只看传入的值
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_env_example_lists_exactly_the_settings_fields() -> None:
    names = {
        line.split("=", 1)[0].strip()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    fields = {name.upper() for name in Settings.model_fields}
    assert names == fields


def test_env_example_has_no_secret_values() -> None:
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "API_KEY" in line and not line.lstrip().startswith("#"):
            assert line.split("=", 1)[1].strip() == "", line


def test_llm_endpoint_falls_back_to_global() -> None:
    settings = make_settings(
        llm_base_url="https://global.example/v1",
        llm_api_key=SecretStr("global-key"),
        llm_model_draft="draft-model",
    )
    endpoint = settings.llm_endpoint(LLMTask.DRAFT)
    assert endpoint.base_url == "https://global.example/v1"
    assert endpoint.api_key.get_secret_value() == "global-key"
    assert endpoint.model == "draft-model"


def test_llm_endpoint_task_override_wins() -> None:
    settings = make_settings(
        llm_base_url="https://global.example/v1",
        llm_api_key=SecretStr("global-key"),
        llm_model_judge="judge-model",
        llm_base_url_judge="https://judge.example/v1",
        llm_api_key_judge=SecretStr("judge-key"),
    )
    endpoint = settings.llm_endpoint(LLMTask.JUDGE)
    assert endpoint.base_url == "https://judge.example/v1"
    assert endpoint.api_key.get_secret_value() == "judge-key"


def test_llm_endpoint_reports_missing_names_without_values() -> None:
    settings = make_settings(llm_api_key=SecretStr("do-not-print"))
    with pytest.raises(MissingConfigError) as excinfo:
        settings.llm_endpoint(LLMTask.ANALYZE)
    message = str(excinfo.value)
    assert "LLM_MODEL_ANALYZE" in message
    assert "LLM_BASE_URL" in message
    assert "do-not-print" not in message


def test_api_keys_are_not_shown_in_repr() -> None:
    settings = make_settings(llm_api_key=SecretStr("do-not-print"))
    assert "do-not-print" not in repr(settings)


def test_embedding_endpoint_falls_back_to_llm_endpoint() -> None:
    settings = make_settings(
        llm_base_url="https://global.example/v1",
        llm_api_key=SecretStr("global-key"),
        embedding_model="embed-model",
    )
    endpoint = settings.embedding_endpoint()
    assert endpoint.base_url == "https://global.example/v1"
    assert endpoint.model == "embed-model"


def test_unfilled_env_example_loads_and_empty_values_count_as_missing() -> None:
    # 直接把 .env.example 复制成 .env 时，所有值都是空字符串
    settings = Settings(_env_file=ENV_EXAMPLE)  # type: ignore[call-arg]
    with pytest.raises(MissingConfigError):
        settings.llm_endpoint(LLMTask.DRAFT)


def test_empty_task_key_falls_back_to_global() -> None:
    settings = make_settings(
        llm_base_url="https://global.example/v1",
        llm_api_key=SecretStr("global-key"),
        llm_api_key_draft=SecretStr(""),
        llm_base_url_draft="",
        llm_model_draft="draft-model",
    )
    endpoint = settings.llm_endpoint(LLMTask.DRAFT)
    assert endpoint.api_key.get_secret_value() == "global-key"
    assert endpoint.base_url == "https://global.example/v1"
