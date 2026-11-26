"""从环境变量（本地通过 `.env`）加载配置（D-005）。

每新增一个变量，都要同步到 `.env.example`。`tests/test_config.py` 会检查两者一致。
"""

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMTask(StrEnum):
    """使用 LLM 的任务。每个任务可以单独配置模型，以及可选的端点和 key（D-010）。"""

    ANALYZE = "analyze"
    DRAFT = "draft"
    BACKTRANSLATE = "backtranslate"
    JUDGE = "judge"


JsonMode = Literal["json_schema", "json_object", "none"]


class MissingConfigError(RuntimeError):
    """用到某项功能时，它需要的配置没有填写。"""


class Endpoint(BaseModel):
    """一个 OpenAI 兼容端点及要使用的模型。"""

    base_url: str
    api_key: SecretStr
    model: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM：任意 OpenAI 兼容端点 ---
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_json_mode: JsonMode = "json_object"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    llm_model_analyze: str | None = None
    llm_model_draft: str | None = None
    llm_model_backtranslate: str | None = None
    llm_model_judge: str | None = None

    llm_base_url_analyze: str | None = None
    llm_base_url_draft: str | None = None
    llm_base_url_backtranslate: str | None = None
    llm_base_url_judge: str | None = None

    llm_api_key_analyze: SecretStr | None = None
    llm_api_key_draft: SecretStr | None = None
    llm_api_key_backtranslate: SecretStr | None = None
    llm_api_key_judge: SecretStr | None = None

    # --- Embedding：OpenAI 兼容的 /v1/embeddings ---
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str | None = None

    # --- 数据与存储 ---
    sample_data_path: str = "data/raw/脱敏数据2026.2.10.xlsx"
    database_url: str = "sqlite:///data/copilot.db"

    def llm_endpoint(self, task: LLMTask) -> Endpoint:
        """某个任务实际使用的端点。

        任务专用的设置优先，没有时回落到全局的 `LLM_BASE_URL` 和 `LLM_API_KEY`。
        """
        suffix = task.value
        return _build_endpoint(
            f"任务 {suffix}",
            base_url=("LLM_BASE_URL", getattr(self, f"llm_base_url_{suffix}") or self.llm_base_url),
            api_key=("LLM_API_KEY", getattr(self, f"llm_api_key_{suffix}") or self.llm_api_key),
            model=(f"LLM_MODEL_{suffix.upper()}", getattr(self, f"llm_model_{suffix}")),
        )

    def embedding_endpoint(self) -> Endpoint:
        """embedding 使用的端点。没有单独配置端点和 key 时，回落到全局的 LLM 端点。"""
        return _build_endpoint(
            "embedding",
            base_url=("EMBEDDING_BASE_URL", self.embedding_base_url or self.llm_base_url),
            api_key=("EMBEDDING_API_KEY", self.embedding_api_key or self.llm_api_key),
            model=("EMBEDDING_MODEL", self.embedding_model),
        )


def _build_endpoint(
    what: str,
    *,
    base_url: tuple[str, str | None],
    api_key: tuple[str, SecretStr | None],
    model: tuple[str, str | None],
) -> Endpoint:
    """每个参数是（环境变量名, 值）。缺少时只报告变量名，绝不输出值（D-005）。"""
    missing = [name for name, value in (base_url, api_key, model) if not value]
    if missing or base_url[1] is None or api_key[1] is None or model[1] is None:
        raise MissingConfigError(f"{what} 缺少配置：{', '.join(missing)}（见 .env.example）")
    return Endpoint(base_url=base_url[1], api_key=api_key[1], model=model[1])


@lru_cache
def get_settings() -> Settings:
    """进程内共享的配置。测试中直接构造 `Settings(...)`，不要用这个函数。"""
    return Settings()
