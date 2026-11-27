"""LLM 用量统计与本地调试日志（#6）。SYNTHETIC：消息内容都是合成的。"""

import json
import stat
from pathlib import Path
from typing import Any

import httpx2
import pytest

from copilot.config import LLMTask
from copilot.llm.fake import FakeLLMClient
from copilot.llm.usage import UsageMeter
from tests.llm.test_client import MESSAGES, FakeEndpoint, make_client, make_settings

pytestmark = pytest.mark.anyio


def reply_with_usage(content: str, prompt: int, completion: int) -> httpx2.Response:
    body: dict[str, Any] = {
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
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }
    return httpx2.Response(200, json=body)


async def test_usage_is_recorded_per_task() -> None:
    endpoint = FakeEndpoint(reply_with_usage("【合成】回答", 120, 30))
    client = make_client(endpoint)
    await client.complete_text(LLMTask.ANALYZE, MESSAGES)
    await client.complete_text(LLMTask.ANALYZE, MESSAGES)
    usage = client.usage.tasks[LLMTask.ANALYZE]
    assert (usage.calls, usage.prompt_tokens, usage.completion_tokens) == (2, 240, 60)
    assert usage.seconds >= 0
    assert client.usage.lines()[-1].startswith("合计：2 次")


async def test_missing_token_counts_make_the_total_unknown() -> None:
    meter = UsageMeter()
    meter.record(LLMTask.DRAFT, 1.0, 100, 10)
    meter.record(LLMTask.DRAFT, 1.0, None, None)
    assert meter.tasks[LLMTask.DRAFT].prompt_tokens is None
    assert "token 未知" in meter.lines()[-1]
    meter.reset()
    assert meter.tasks == {}


async def test_call_log_is_private_and_has_no_key(tmp_path: Path) -> None:
    endpoint = FakeEndpoint(reply_with_usage("【合成】回答", 5, 2))
    client = make_client(endpoint, log_dir=tmp_path / "logs")
    await client.complete_text(LLMTask.ANALYZE, MESSAGES)
    files = list((tmp_path / "logs").glob("*.jsonl"))
    assert len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    text = files[0].read_text(encoding="utf-8")
    assert "test-key" not in text
    record = json.loads(text)
    assert record["task"] == "analyze"
    assert record["messages"] == MESSAGES
    assert record["response"] == "【合成】回答"
    assert (record["prompt_tokens"], record["completion_tokens"]) == (5, 2)


async def test_log_dir_comes_from_settings(tmp_path: Path) -> None:
    endpoint = FakeEndpoint(reply_with_usage("【合成】", 1, 1))
    client = make_client(endpoint, make_settings(llm_log_dir=str(tmp_path / "from-env")))
    await client.complete_text(LLMTask.ANALYZE, MESSAGES)
    assert list((tmp_path / "from-env").glob("*.jsonl"))


async def test_no_log_without_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    endpoint = FakeEndpoint(reply_with_usage("【合成】", 1, 1))
    await make_client(endpoint).complete_text(LLMTask.ANALYZE, MESSAGES)
    assert list(tmp_path.rglob("*.jsonl")) == []


async def test_fake_client_counts_calls() -> None:
    fake = FakeLLMClient()
    fake.queue(LLMTask.DRAFT, "【合成】")
    await fake.complete_text(LLMTask.DRAFT, MESSAGES)
    assert fake.usage.tasks[LLMTask.DRAFT].calls == 1
