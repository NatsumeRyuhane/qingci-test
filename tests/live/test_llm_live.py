"""对真实 LLM 端点的冒烟测试，只用合成数据，只做结构性断言（不断言具体文本）。

默认不运行。运行方式：在 .env 或环境变量中配置 LLM_BASE_URL、LLM_API_KEY、
LLM_MODEL_ANALYZE，然后执行 `pytest -m live`。CI 的 llm-live job 使用仓库 secrets。
"""

import pytest
from pydantic import BaseModel, Field

from copilot.config import LLMTask, Settings
from copilot.llm import OpenAICompatibleClient

pytestmark = [pytest.mark.live, pytest.mark.anyio]

# SYNTHETIC：合成数据，并非来自客户数据
SYNTHETIC_INQUIRY = """お問い合わせ内容の種類 : 不具合について
ご利用環境 : iOS
お問い合わせ内容：
テスト用の架空の問い合わせです。アプリを起動するとタイトル画面で止まってしまいます。"""


class SmokeAnalysis(BaseModel):
    category: str = Field(description="お問い合わせ内容の種類 的原文")
    summary_zh: str = Field(description="用简体中文写的一句话摘要")


async def test_structured_output_against_real_endpoint() -> None:
    client = OpenAICompatibleClient(Settings())

    result = await client.complete_json(
        LLMTask.ANALYZE,
        [
            {"role": "system", "content": "你是客服助手，阅读日语咨询并提取信息。"},
            {"role": "user", "content": SYNTHETIC_INQUIRY},
        ],
        SmokeAnalysis,
    )

    assert result.category.strip()
    assert result.summary_zh.strip()
