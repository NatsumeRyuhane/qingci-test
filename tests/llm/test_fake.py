import pytest
from pydantic import BaseModel

from copilot.config import LLMTask
from copilot.llm import LLMOutputError
from copilot.llm.fake import FakeLLMClient

pytestmark = pytest.mark.anyio


class Reply(BaseModel):
    text: str


async def test_returns_queued_replies_in_order_and_records_calls() -> None:
    fake = FakeLLMClient()
    fake.queue(LLMTask.DRAFT, '{"text": "一"}', '{"text": "二"}')
    messages = [{"role": "user", "content": "x"}]

    first = await fake.complete_json(LLMTask.DRAFT, messages, Reply)  # type: ignore[arg-type]
    second = await fake.complete_json(LLMTask.DRAFT, messages, Reply)  # type: ignore[arg-type]

    assert (first.text, second.text) == ("一", "二")
    assert [call.task for call in fake.calls] == [LLMTask.DRAFT, LLMTask.DRAFT]


async def test_missing_reply_fails_loudly() -> None:
    with pytest.raises(AssertionError):
        await FakeLLMClient().complete_text(LLMTask.ANALYZE, [])


async def test_invalid_queued_reply_raises_output_error() -> None:
    fake = FakeLLMClient()
    fake.queue(LLMTask.ANALYZE, "not json")
    with pytest.raises(LLMOutputError):
        await fake.complete_json(LLMTask.ANALYZE, [], Reply)
