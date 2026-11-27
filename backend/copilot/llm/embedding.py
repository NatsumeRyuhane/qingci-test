"""OpenAI 兼容端点的 Embedding 客户端与测试用假实现（D-010）。

业务代码只依赖 `EmbeddingClient` 协议。生产中用 `OpenAIEmbeddingClient`，
测试中用 `FakeEmbeddingClient`。
"""

import hashlib
import math
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Protocol

import httpx2
from openai import AsyncOpenAI

from copilot.config import Endpoint, Settings

DEFAULT_BATCH_SIZE = 64
"""单次 `/v1/embeddings` 请求最多发送的文本条数。"""


class EmbeddingClient(Protocol):
    """业务代码依赖的 embedding 接口。"""

    @property
    def model_name(self) -> str:
        """当前使用的模型名，用于区分缓存。"""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把 `texts` 编码为等长的向量列表，顺序与输入一致。"""
        ...


class OpenAIEmbeddingClient:
    """调用任意 OpenAI 兼容端点的 `/v1/embeddings`。"""

    def __init__(
        self,
        settings: Settings,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        transport_retries: int = 2,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError(f"batch_size 必须至少为 1，实际为 {batch_size}")
        self._settings = settings
        self._batch_size = batch_size
        self._transport_retries = transport_retries
        self._http_client = http_client
        self._client: AsyncOpenAI | None = None
        self._endpoint: Endpoint | None = None

    def _get_endpoint(self) -> Endpoint:
        if self._endpoint is None:
            self._endpoint = self._settings.embedding_endpoint()
        return self._endpoint

    @property
    def model_name(self) -> str:
        return self._get_endpoint().model

    def _get_client(self) -> AsyncOpenAI:
        endpoint = self._get_endpoint()
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=endpoint.base_url,
                api_key=endpoint.api_key.get_secret_value(),
                timeout=self._settings.llm_timeout_seconds,
                max_retries=self._transport_retries,
                http_client=self._http_client,
            )
        return self._client

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []
        endpoint = self._get_endpoint()
        client = self._get_client()
        vectors: list[list[float]] = []
        for start in range(0, len(items), self._batch_size):
            batch = items[start : start + self._batch_size]
            response = await client.embeddings.create(model=endpoint.model, input=batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            if len(ordered) != len(batch):
                raise RuntimeError(
                    f"embedding 返回条数与输入不一致：输入 {len(batch)}，返回 {len(ordered)}"
                )
            vectors.extend([float(x) for x in item.embedding] for item in ordered)
        return vectors


def _hashed_vector(text: str, dim: int) -> list[float]:
    """用字符 bigram 哈希生成确定性的单位向量，供测试在未指定预设向量时使用。"""
    norm = unicodedata.normalize("NFKC", text).lower()
    vec = [0.0] * dim
    tokens = [norm[i : i + 2] for i in range(max(len(norm) - 1, 0))] or ([norm] if norm else [])
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
        bucket = int.from_bytes(digest, "big") % dim
        vec[bucket] += 1.0
    length = math.sqrt(math.sumprod(vec, vec))
    if length == 0.0:
        return vec
    return [x / length for x in vec]


class FakeEmbeddingClient:
    """测试用的确定性 embedding 客户端，并记录每次调用以便断言缓存命中。"""

    def __init__(
        self,
        vectors: Mapping[str, Sequence[float]] | None = None,
        *,
        model_name: str = "fake-embedding",
        dim: int = 32,
    ) -> None:
        self._vectors = {k: [float(x) for x in v] for k, v in (vectors or {}).items()}
        self._model_name = model_name
        self._dim = dim
        self.calls: list[list[str]] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        self.calls.append(items)
        return [
            list(self._vectors[text]) if text in self._vectors else _hashed_vector(text, self._dim)
            for text in items
        ]
