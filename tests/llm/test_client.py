"""OpenAICompatibleClient 的线路级测试：用 httpx2.MockTransport 模拟兼容端点的 HTTP 交互。"""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, SecretStr

from copilot.config import JsonMode, LLMTask, Settings
from copilot.llm import LLMOutputError, OpenAICompatibleClient

BASE_URL = "https://llm.example/v1"

pytestmark = pytest.mark.anyio


class Triage(BaseModel):
    category: str
    urgent: bool


def make_settings(json_mode: JsonMode = "json_object", **values: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url=BASE_URL,
        llm_api_key=SecretStr("test-key"),
        llm_model_analyze="analyze-model",
        llm_json_mode=json_mode,
        llm_max_retries=1,
        **values,
    )


def chat_reply(content: str | None) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "analyze-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


class FakeEndpoint:
    """按顺序返回预设响应，并记录收到的请求。"""

    def __init__(self, *responses: httpx2.Response) -> None:
        self._responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        return response

    def body(self, index: int = -1) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(self.requests[index].content)
        return body


def make_client(
    endpoint: FakeEndpoint | Callable[[httpx2.Request], httpx2.Response],
    settings: Settings | None = None,
    **kwargs: Any,
) -> OpenAICompatibleClient:
    handler = endpoint.handler if isinstance(endpoint, FakeEndpoint) else endpoint
    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return OpenAICompatibleClient(settings or make_settings(), http_client=http_client, **kwargs)


VALID = '{"category": "不具合について", "urgent": false}'
MESSAGES: list[Any] = [{"role": "user", "content": "テスト用の架空の問い合わせ"}]


async def test_json_object_mode() -> None:
    endpoint = FakeEndpoint(chat_reply(VALID))
    client = make_client(endpoint, make_settings("json_object"))

    result = await client.complete_json(LLMTask.ANALYZE, MESSAGES, Triage)

    assert result == Triage(category="不具合について", urgent=False)
    request = endpoint.requests[0]
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    body = endpoint.body()
    assert body["model"] == "analyze-model"
    assert body["response_format"] == {"type": "json_object"}
    # schema 总是写进 prompt，不依赖服务商是否支持 json_schema
    assert "JSON Schema" in body["messages"][0]["content"]


async def test_json_schema_mode_sends_schema() -> None:
    endpoint = FakeEndpoint(chat_reply(VALID))

    await make_client(endpoint, make_settings("json_schema")).complete_json(
        LLMTask.ANALYZE, MESSAGES, Triage
    )

    response_format = endpoint.body()["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "Triage"
    assert "category" in response_format["json_schema"]["schema"]["properties"]


async def test_none_mode_omits_response_format_and_accepts_code_fence() -> None:
    endpoint = FakeEndpoint(chat_reply(f"```json\n{VALID}\n```"))

    result = await make_client(endpoint, make_settings("none")).complete_json(
        LLMTask.ANALYZE, MESSAGES, Triage
    )

    assert result.category == "不具合について"
    assert "response_format" not in endpoint.body()


async def test_invalid_output_is_retried_with_validation_error() -> None:
    incomplete = '{"category": "不具合について"}'
    endpoint = FakeEndpoint(chat_reply(incomplete), chat_reply(VALID))

    result = await make_client(endpoint).complete_json(LLMTask.ANALYZE, MESSAGES, Triage)

    assert result.urgent is False
    assert len(endpoint.requests) == 2
    retry_messages = endpoint.body(1)["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": incomplete}
    assert "urgent" in retry_messages[-1]["content"]


async def test_gives_up_after_max_retries() -> None:
    endpoint = FakeEndpoint(chat_reply("これはJSONではありません"))

    with pytest.raises(LLMOutputError):
        await make_client(endpoint).complete_json(LLMTask.ANALYZE, MESSAGES, Triage)
    assert len(endpoint.requests) == 2  # 首次 + LLM_MAX_RETRIES=1


async def test_empty_text_content_fails_immediately() -> None:
    endpoint = FakeEndpoint(chat_reply(None))

    with pytest.raises(LLMOutputError):
        await make_client(endpoint).complete_text(LLMTask.ANALYZE, MESSAGES)
    assert len(endpoint.requests) == 1


async def test_empty_json_content_is_retried() -> None:
    endpoint = FakeEndpoint(chat_reply(None), chat_reply(VALID))

    result = await make_client(endpoint).complete_json(LLMTask.ANALYZE, MESSAGES, Triage)

    assert result.category == "不具合について"
    assert len(endpoint.requests) == 2
    # 重试时不追加空的 assistant 消息，对话与第一次相同
    assert endpoint.body(1)["messages"] == endpoint.body(0)["messages"]


@pytest.mark.parametrize("content", [None, ""])
async def test_repeated_empty_json_content_gives_up_after_max_retries(content: str | None) -> None:
    endpoint = FakeEndpoint(chat_reply(content))

    with pytest.raises(LLMOutputError):
        await make_client(endpoint).complete_json(LLMTask.ANALYZE, MESSAGES, Triage)
    assert len(endpoint.requests) == 2  # 首次 + LLM_MAX_RETRIES=1


async def test_rate_limit_is_retried_by_transport() -> None:
    endpoint = FakeEndpoint(
        httpx2.Response(429, headers={"retry-after-ms": "1"}, json={"error": {}}),
        chat_reply("翻訳結果"),
    )
    client = make_client(endpoint, transport_retries=1)

    assert await client.complete_text(LLMTask.ANALYZE, MESSAGES) == "翻訳結果"
    assert len(endpoint.requests) == 2


async def test_complete_text_sends_no_response_format() -> None:
    endpoint = FakeEndpoint(chat_reply("中文回译"))

    assert await make_client(endpoint).complete_text(LLMTask.ANALYZE, MESSAGES) == "中文回译"
    assert "response_format" not in endpoint.body()


async def test_task_override_uses_its_own_endpoint() -> None:
    endpoint = FakeEndpoint(chat_reply("ok"))
    settings = make_settings(
        llm_model_judge="judge-model",
        llm_base_url_judge="https://judge.example/v1",
        llm_api_key_judge=SecretStr("judge-key"),
    )

    await make_client(endpoint, settings).complete_text(LLMTask.JUDGE, MESSAGES)

    request = endpoint.requests[0]
    assert str(request.url) == "https://judge.example/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer judge-key"
    assert endpoint.body()["model"] == "judge-model"


async def test_service_tier_is_sent_when_configured() -> None:
    endpoint = FakeEndpoint(chat_reply(VALID))
    settings = make_settings(llm_service_tier="flex")
    await make_client(endpoint, settings).complete_json(LLMTask.ANALYZE, MESSAGES, Triage)
    assert endpoint.body()["service_tier"] == "flex"


async def test_service_tier_is_omitted_by_default() -> None:
    endpoint = FakeEndpoint(chat_reply("テキスト"))
    await make_client(endpoint).complete_text(LLMTask.ANALYZE, MESSAGES)
    assert "service_tier" not in endpoint.body()


async def test_transient_region_403_is_retried() -> None:
    endpoint = FakeEndpoint(
        httpx2.Response(
            403,
            json={"error": {"message": "This model is not available in your region."}},
        ),
        chat_reply("翻訳結果"),
    )
    client = make_client(endpoint, transport_retries=1, retry_backoff_seconds=0.0)

    assert await client.complete_text(LLMTask.ANALYZE, MESSAGES) == "翻訳結果"
    assert len(endpoint.requests) == 2


async def test_non_region_403_is_not_retried() -> None:
    from openai import PermissionDeniedError

    endpoint = FakeEndpoint(
        httpx2.Response(403, json={"error": {"message": "Invalid API key permissions"}}),
        chat_reply("翻訳結果"),
    )
    client = make_client(endpoint, transport_retries=2, retry_backoff_seconds=0.0)

    with pytest.raises(PermissionDeniedError):
        await client.complete_text(LLMTask.ANALYZE, MESSAGES)
    assert len(endpoint.requests) == 1


async def test_with_usage_isolates_meter() -> None:
    endpoint = FakeEndpoint(chat_reply("ok1"), chat_reply("ok2"))
    base = make_client(endpoint)
    scoped = base.with_usage()

    await base.complete_text(LLMTask.ANALYZE, MESSAGES)
    await scoped.complete_text(LLMTask.ANALYZE, MESSAGES)

    assert base.usage.total().calls == 1
    assert scoped.usage.total().calls == 1


async def test_reasoning_is_omitted_by_default_and_off_for_translation() -> None:
    """翻译默认关闭推理：推理模型可能把 max_tokens 全部用在推理上而返回空内容。"""
    endpoint = FakeEndpoint(chat_reply("訳文"), chat_reply("草稿"))
    settings = make_settings(llm_model_draft="draft-model", llm_service_tier="flex")
    client = make_client(endpoint, settings)
    await client.complete_text(LLMTask.TRANSLATE, [{"role": "user", "content": "x"}])
    await client.complete_text(LLMTask.DRAFT, [{"role": "user", "content": "x"}])
    translate, draft = (json.loads(r.content) for r in endpoint.requests)
    assert translate["reasoning"] == {"enabled": False}
    assert translate["service_tier"] == "flex"
    assert translate["model"] == "draft-model"  # 翻译默认用起草模型
    assert translate["max_tokens"] == 4096
    assert "reasoning" not in draft


async def test_backtranslation_reasoning_is_off_by_default() -> None:
    """D-019：回译是纯翻译，默认关闭推理。"""
    endpoint = FakeEndpoint(chat_reply("回译"))
    client = make_client(endpoint, make_settings(llm_model_backtranslate="b"))
    await client.complete_text(LLMTask.BACKTRANSLATE, [{"role": "user", "content": "x"}])
    assert endpoint.body()["reasoning"] == {"enabled": False}


async def test_reasoning_effort_is_sent_when_configured() -> None:
    endpoint = FakeEndpoint(chat_reply("x"))
    client = make_client(endpoint, make_settings(llm_model_draft="d", llm_reasoning_draft="low"))
    await client.complete_text(LLMTask.DRAFT, [{"role": "user", "content": "x"}])
    assert endpoint.body()["reasoning"] == {"effort": "low"}
