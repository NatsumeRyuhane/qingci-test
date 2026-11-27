"""从环境变量（本地通过 `.env`）加载配置（D-005）。

每新增一个变量，都要同步到 `.env.example`。`tests/test_config.py` 会检查两者一致。
"""

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMTask(StrEnum):
    """使用 LLM 的任务。每个任务可以单独配置模型，以及可选的端点和 key（D-010）。"""

    ANALYZE = "analyze"
    DRAFT = "draft"
    BACKTRANSLATE = "backtranslate"
    JUDGE = "judge"
    ANNOTATE = "annotate"
    """知识库模板的标注（#5）：中文说明、意图、风险等。离线批处理，调用次数少，可以用较强的模型。"""


JsonMode = Literal["json_schema", "json_object", "none"]
ServiceTier = Literal["default", "flex", "priority"]
"""请求的服务等级（例如 OpenRouter 的 `service_tier`）。flex 更便宜、优先级更低。"""


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
    llm_service_tier: ServiceTier | None = None
    llm_log_dir: str | None = None
    """每次 LLM 调用的消息和回复写成 JSONL 的目录（调试用，含客户数据）。为空时不写。"""

    llm_model_analyze: str | None = None
    llm_model_draft: str | None = None
    llm_model_backtranslate: str | None = None
    llm_model_judge: str | None = None
    llm_model_annotate: str | None = None

    llm_base_url_analyze: str | None = None
    llm_base_url_draft: str | None = None
    llm_base_url_backtranslate: str | None = None
    llm_base_url_judge: str | None = None
    llm_base_url_annotate: str | None = None

    llm_api_key_analyze: SecretStr | None = None
    llm_api_key_draft: SecretStr | None = None
    llm_api_key_backtranslate: SecretStr | None = None
    llm_api_key_judge: SecretStr | None = None
    llm_api_key_annotate: SecretStr | None = None

    # --- Embedding：OpenAI 兼容的 /v1/embeddings ---
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str | None = None

    # --- 起草与分流（#6） ---
    draft_auto_send_min_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    draft_problem_time_lookback_days: int = Field(default=7, ge=1)

    # --- 数据与存储 ---
    sample_data_path: str = "data/raw/脱敏数据2026.2.10.xlsx"
    database_url: str = "sqlite:///data/copilot.db"

    @field_validator("llm_service_tier", "llm_log_dir", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        # 直接复制 .env.example 时值为空字符串，表示不指定。
        return None if value == "" else value

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
