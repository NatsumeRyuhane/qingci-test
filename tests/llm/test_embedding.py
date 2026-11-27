"""Embedding 客户端：线路级测试（httpx2.MockTransport）和假实现的测试。"""

import json
import math

import httpx2
import pytest
from pydantic import SecretStr

from copilot.config import MissingConfigError, Settings
from copilot.llm import FakeEmbeddingClient, OpenAIEmbeddingClient

BASE_URL = "https://embed.example/v1"

pytestmark = pytest.mark.anyio


def make_settings(**values: object) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url=BASE_URL,
        llm_api_key=SecretStr("test-key"),
        embedding_model="embed-model",
        **values,
    )


def embedding_reply(request: httpx2.Request) -> httpx2.Response:
    """每条输入返回 [下标, 长度]，并故意打乱顺序，检查客户端按 index 重排。"""
    body = json.loads(request.content)
    data = [
        {"object": "embedding", "index": i, "embedding": [float(i), float(len(text))]}
        for i, text in enumerate(body["input"])
    ]
    return httpx2.Response(
        200,
        json={
            "object": "list",
            "model": body["model"],
            "data": list(reversed(data)),
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        },
    )


async def test_embed_batches_and_orders_by_index() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return embedding_reply(request)

    client = OpenAIEmbeddingClient(
        make_settings(),
        batch_size=2,
        transport_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    vectors = await client.embed(["あ", "いい", "ううう"])
    assert vectors == [[0.0, 1.0], [1.0, 2.0], [0.0, 3.0]]
    assert [json.loads(r.content)["input"] for r in requests] == [["あ", "いい"], ["ううう"]]
    assert all(json.loads(r.content)["model"] == "embed-model" for r in requests)
    assert str(requests[0].url) == f"{BASE_URL}/embeddings"
    assert client.model_name == "embed-model"


async def test_embed_empty_input_makes_no_request() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("不应发出请求")

    client = OpenAIEmbeddingClient(
        make_settings(), http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    assert await client.embed([]) == []


async def test_embed_rejects_wrong_count() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": "embed-model",
                "data": [{"object": "embedding", "index": 0, "embedding": [1.0]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    client = OpenAIEmbeddingClient(
        make_settings(),
        transport_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(RuntimeError, match="条数"):
        await client.embed(["a", "b"])


def test_missing_model_is_reported_by_name() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, llm_base_url=BASE_URL, llm_api_key=SecretStr("secret-value")
    )
    client = OpenAIEmbeddingClient(settings)
    with pytest.raises(MissingConfigError, match="EMBEDDING_MODEL") as info:
        _ = client.model_name
    assert "secret-value" not in str(info.value)


def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        OpenAIEmbeddingClient(make_settings(), batch_size=0)


async def test_fake_is_deterministic_unit_vectors_and_records_calls() -> None:
    fake = FakeEmbeddingClient({"固定": [1.0, 0.0]}, dim=16)
    first = await fake.embed(["ガチャ", "固定"])
    second = await fake.embed(["ガチャ"])
    assert first[0] == second[0]
    assert math.isclose(math.sqrt(sum(x * x for x in first[0])), 1.0)
    assert first[1] == [1.0, 0.0]
    assert fake.calls == [["ガチャ", "固定"], ["ガチャ"]]
    assert fake.model_name == "fake-embedding"


def test_cache_namespace_has_endpoint_and_model_but_no_key() -> None:
    client = OpenAIEmbeddingClient(make_settings())
    assert client.cache_namespace == f"{BASE_URL}|embed-model"
    assert "test-key" not in client.cache_namespace
