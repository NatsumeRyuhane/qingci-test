"""LLM 访问层：只说 OpenAI Chat Completions 兼容协议，不绑定服务商（D-010）。

业务代码只依赖 `LLMClient` 协议。生产中用 `OpenAICompatibleClient`，测试中用
`copilot.llm.fake.FakeLLMClient`。
"""

from copilot.llm.client import LLMClient, LLMOutputError, Message, OpenAICompatibleClient

__all__ = ["LLMClient", "LLMOutputError", "Message", "OpenAICompatibleClient"]
