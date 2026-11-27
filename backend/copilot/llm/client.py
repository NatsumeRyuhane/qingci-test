"""OpenAI 兼容端点的 LLM 客户端。"""

import json
import re
from typing import Any, Literal, Protocol, TypedDict

import httpx2
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from copilot.config import Endpoint, LLMTask, Settings


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMOutputError(RuntimeError):
    """多次重试后，模型输出仍然无法通过校验。"""


class LLMClient(Protocol):
    """业务代码依赖的接口。"""

    async def complete_text(self, task: LLMTask, messages: list[Message]) -> str:
        """返回纯文本回复（例如回译）。"""
        ...

    async def complete_json[T: BaseModel](
        self, task: LLMTask, messages: list[Message], output: type[T]
    ) -> T:
        """返回通过 `output` 校验的结构化结果。"""
        ...


_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """不支持 JSON 模式的服务商常把 JSON 包在 ```json 代码块里。"""
    match = _FENCE.match(text)
    return match.group(1) if match else text


def _schema_instruction(output: type[BaseModel]) -> Message:
    schema = json.dumps(output.model_json_schema(), ensure_ascii=False)
    return {
        "role": "system",
        "content": (
            "只输出一个符合下面 JSON Schema 的 JSON 对象，不要输出任何其他文字。\n"
            f"JSON Schema：{schema}"
        ),
    }


class OpenAICompatibleClient:
    """对任意 OpenAI 兼容端点调用 Chat Completions。

    - 结构化输出不依赖 `json_schema` 模式：一律在 prompt 中给出 schema，用 Pydantic 校验，
      失败时把错误反馈给模型重试（最多 `LLM_MAX_RETRIES` 次）。`LLM_JSON_MODE` 只决定额外
      发送哪种 `response_format`。
    - 限流（429）、5xx 和连接错误的重试交给 SDK。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport_retries: int = 2,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        """`http_client` 供测试注入（例如带 `httpx2.MockTransport` 的客户端）。"""
        self._settings = settings
        self._transport_retries = transport_retries
        self._http_client = http_client
        self._clients: dict[tuple[str, str], AsyncOpenAI] = {}

    def _client_for(self, endpoint: Endpoint) -> AsyncOpenAI:
        key = (endpoint.base_url, endpoint.api_key.get_secret_value())
        if key not in self._clients:
            self._clients[key] = AsyncOpenAI(
                base_url=endpoint.base_url,
                api_key=endpoint.api_key.get_secret_value(),
                timeout=self._settings.llm_timeout_seconds,
                max_retries=self._transport_retries,
                http_client=self._http_client,
            )
        return self._clients[key]

    async def _chat(
        self, task: LLMTask, messages: list[Message], response_format: dict[str, Any] | None
    ) -> str | None:
        """返回模型回复的文本；没有内容时返回 None，由调用方决定报错还是重试。"""
        endpoint = self._settings.llm_endpoint(task)
        extra: dict[str, Any] = {}
        if response_format is not None:
            extra["response_format"] = response_format
        if self._settings.llm_service_tier is not None:
            # 放在请求体的顶层（OpenRouter 的约定）。不用 SDK 的同名参数，避免受它的取值限制。
            extra["extra_body"] = {"service_tier": self._settings.llm_service_tier}
        response = await self._client_for(endpoint).chat.completions.create(
            model=endpoint.model,
            messages=list(messages),  # type: ignore[arg-type]
            **extra,
        )
        content = response.choices[0].message.content if response.choices else None
        return content or None

    async def complete_text(self, task: LLMTask, messages: list[Message]) -> str:
        content = await self._chat(task, messages, response_format=None)
        if content is None:
            raise LLMOutputError(f"任务 {task.value} 的模型返回了空内容")
        return content

    async def complete_json[T: BaseModel](
        self, task: LLMTask, messages: list[Message], output: type[T]
    ) -> T:
        response_format = self._response_format(output)
        conversation: list[Message] = [_schema_instruction(output), *messages]
        last_error: ValidationError | None = None
        for _ in range(self._settings.llm_max_retries + 1):
            content = await self._chat(task, conversation, response_format)
            if content is None:
                # 空回复也算一次失败的尝试，用同样的对话重试。不追加空的 assistant 消息，
                # 部分服务商会拒绝内容为空的消息。
                continue
            try:
                return output.model_validate_json(_strip_code_fence(content))
            except ValidationError as error:
                last_error = error
                conversation = [
                    *conversation,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "上面的输出没有通过校验，请修正后只输出 JSON 对象。错误：\n"
                            f"{error.json(include_url=False)}"
                        ),
                    },
                ]
        raise LLMOutputError(
            f"任务 {task.value} 的输出在 {self._settings.llm_max_retries + 1} 次尝试后"
            f"仍未通过 {output.__name__} 的校验（空回复也计为失败）"
        ) from last_error

    def _response_format(self, output: type[BaseModel]) -> dict[str, Any] | None:
        mode = self._settings.llm_json_mode
        if mode == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {"name": output.__name__, "schema": output.model_json_schema()},
            }
        if mode == "json_object":
            return {"type": "json_object"}
        return None
