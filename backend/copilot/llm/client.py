"""OpenAI 兼容端点的 LLM 客户端。"""

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict

import httpx2
from openai import AsyncOpenAI, PermissionDeniedError
from pydantic import BaseModel, ValidationError

from copilot.config import Endpoint, LLMTask, Settings
from copilot.llm.usage import CallLog, UsageMeter


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMOutputError(RuntimeError):
    """多次重试后，模型输出仍然无法通过校验。"""


class LLMClient(Protocol):
    """业务代码依赖的接口。"""

    usage: UsageMeter
    """按任务累计的调用次数、token 和耗时（`copilot.llm.usage`）。"""

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
        retry_backoff_seconds: float = 0.5,
        http_client: httpx2.AsyncClient | None = None,
        log_dir: Path | None = None,
    ) -> None:
        """`http_client` 供测试注入（例如带 `httpx2.MockTransport` 的客户端）。`log_dir` 为空时
        取 `LLM_LOG_DIR`；两者都没有时不写调试日志。"""
        self._settings = settings
        self.usage = UsageMeter()
        """按任务累计的调用次数、token 和耗时。"""
        directory = log_dir or (Path(settings.llm_log_dir) if settings.llm_log_dir else None)
        self._log = CallLog(directory)
        self._transport_retries = transport_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._http_client = http_client
        self._clients: dict[tuple[str, str], AsyncOpenAI] = {}

    def with_usage(self, usage: UsageMeter | None = None) -> "OpenAICompatibleClient":
        """返回共享底层连接池和日志、但使用独立 `UsageMeter` 的客户端（供并发评测按案件统计）。"""
        clone = object.__new__(OpenAICompatibleClient)
        clone._settings = self._settings
        clone.usage = usage if usage is not None else UsageMeter()
        clone._log = self._log
        clone._transport_retries = self._transport_retries
        clone._retry_backoff_seconds = self._retry_backoff_seconds
        clone._http_client = self._http_client
        clone._clients = self._clients
        return clone

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

    async def aclose(self) -> None:
        """关闭所有缓存的底层 AsyncOpenAI 客户端，避免跨事件循环复用连接池。"""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    @staticmethod
    def _is_transient_region_403(error: PermissionDeniedError) -> bool:
        """服务商（例如 OpenRouter）间歇性的地区 403，重试通常可以成功（#7）。"""
        return "not available in your region" in str(error).lower()

    async def _chat(
        self, task: LLMTask, messages: list[Message], response_format: dict[str, Any] | None
    ) -> str | None:
        """返回模型回复的文本；没有内容时返回 None，由调用方决定报错还是重试。"""
        endpoint = self._settings.llm_endpoint(task)
        extra: dict[str, Any] = {}
        if response_format is not None:
            extra["response_format"] = response_format
        # 显式设置合理的 max_tokens，避免 OpenRouter 按模型上限（如 65536）预扣额度导致 402。
        max_tokens = {
            LLMTask.JUDGE: 2048,
            LLMTask.ANALYZE: 4096,
            LLMTask.BACKTRANSLATE: 4096,
            LLMTask.DRAFT: 8192,
            LLMTask.TRANSLATE: 4096,
        }.get(task)
        if max_tokens is not None:
            extra["max_tokens"] = max_tokens
        body: dict[str, Any] = {}
        if self._settings.llm_service_tier is not None:
            # 放在请求体的顶层（OpenRouter 的约定）。不用 SDK 的同名参数，避免受它的取值限制。
            body["service_tier"] = self._settings.llm_service_tier
        reasoning = self._settings.llm_reasoning(task)
        if reasoning == "off":
            body["reasoning"] = {"enabled": False}
        elif reasoning != "default":
            body["reasoning"] = {"effort": reasoning}
        if body:
            extra["extra_body"] = body
        started = time.perf_counter()
        client = self._client_for(endpoint)
        response = None
        for attempt in range(self._transport_retries + 1):
            try:
                response = await client.chat.completions.create(
                    model=endpoint.model,
                    messages=list(messages),  # type: ignore[arg-type]
                    **extra,
                )
                break
            except PermissionDeniedError as exc:
                if not self._is_transient_region_403(exc) or attempt >= self._transport_retries:
                    raise
                if self._retry_backoff_seconds > 0:
                    await asyncio.sleep(self._retry_backoff_seconds * (2**attempt))
        assert response is not None
        seconds = time.perf_counter() - started
        content = response.choices[0].message.content if response.choices else None
        usage = response.usage
        prompt = usage.prompt_tokens if usage is not None else None
        completion = usage.completion_tokens if usage is not None else None
        self.usage.record(task, seconds, prompt, completion)
        self._log.write(
            task=task,
            model=endpoint.model,
            messages=messages,
            response=content,
            seconds=seconds,
            prompt_tokens=prompt,
            completion_tokens=completion,
            at=datetime.now(UTC),
        )
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
