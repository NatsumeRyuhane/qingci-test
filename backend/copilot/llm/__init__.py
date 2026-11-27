"""LLM 与 Embedding 访问层：只说 OpenAI 兼容协议，不绑定服务商（D-010）。

业务代码只依赖 `LLMClient` 和 `EmbeddingClient` 协议。生产中用
`OpenAICompatibleClient` / `OpenAIEmbeddingClient`，测试中用
`copilot.llm.fake.FakeLLMClient` / `copilot.llm.embedding.FakeEmbeddingClient`。
"""

from copilot.llm.client import LLMClient, LLMOutputError, Message, OpenAICompatibleClient
from copilot.llm.embedding import EmbeddingClient, FakeEmbeddingClient, OpenAIEmbeddingClient

__all__ = [
    "EmbeddingClient",
    "FakeEmbeddingClient",
    "LLMClient",
    "LLMOutputError",
    "Message",
    "OpenAICompatibleClient",
    "OpenAIEmbeddingClient",
]
