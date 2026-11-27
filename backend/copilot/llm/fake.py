"""测试用的 LLM 客户端：按脚本返回预设的回复，并记录每次调用。

fake = FakeLLMClient()
fake.queue(LLMTask.ANALYZE, '{"category": "不具合について"}')
result = await fake.complete_json(LLMTask.ANALYZE, messages, Analysis)
"""

from collections import defaultdict, deque
from dataclasses import dataclass

from pydantic import BaseModel

from copilot.config import LLMTask
from copilot.llm.client import LLMOutputError, Message
from copilot.llm.usage import UsageMeter


@dataclass
class FakeCall:
    task: LLMTask
    messages: list[Message]


class FakeLLMClient:
    def __init__(self) -> None:
        self._replies: dict[LLMTask, deque[str]] = defaultdict(deque)
        self.calls: list[FakeCall] = []
        self.usage = UsageMeter()
        """与真实客户端相同的接口；只计次数，token 记为未知。"""

    def queue(self, task: LLMTask, *replies: str) -> None:
        """为某个任务按顺序排入回复。`complete_json` 收到 JSON 字符串。"""
        self._replies[task].extend(replies)

    def _next(self, task: LLMTask, messages: list[Message]) -> str:
        self.calls.append(FakeCall(task=task, messages=list(messages)))
        self.usage.record(task, 0.0, None, None)
        if not self._replies[task]:
            raise AssertionError(f"FakeLLMClient：任务 {task.value} 没有预设的回复")
        return self._replies[task].popleft()

    async def complete_text(self, task: LLMTask, messages: list[Message]) -> str:
        return self._next(task, messages)

    async def complete_json[T: BaseModel](
        self, task: LLMTask, messages: list[Message], output: type[T]
    ) -> T:
        reply = self._next(task, messages)
        try:
            return output.model_validate_json(reply)
        except ValueError as error:
            raise LLMOutputError(f"预设回复未通过 {output.__name__} 的校验") from error
