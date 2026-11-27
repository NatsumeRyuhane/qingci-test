"""LLM 调用的用量统计与本地调试日志（#6）。

- `UsageMeter` 按任务累计调用次数、token 和耗时，供 CLI 报告单个案件的延迟和成本。服务商
  没有返回 token 数时只计次数和耗时，token 记为未知（`None`），不猜测。
- `CallLog` 把每次请求的消息和模型的原始回复写成本地 JSONL（`LLM_LOG_DIR`），用于调试
  prompt。日志含有客户数据，所以只写在被 git 忽略的 `data/` 下，文件权限为 0600；**永远
  不记录 API key**（请求头不写入日志）。没有配置目录时不写。
"""

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from copilot.config import LLMTask


@dataclass
class TaskUsage:
    """一个任务的累计用量。"""

    calls: int = 0
    seconds: float = 0.0
    prompt_tokens: int | None = 0
    completion_tokens: int | None = 0

    def add(self, seconds: float, prompt: int | None, completion: int | None) -> None:
        self.calls += 1
        self.seconds += seconds
        # 任何一次没有 token 数，累计值就是未知：部分已知的和会低估成本。
        self.prompt_tokens = (
            None if prompt is None or self.prompt_tokens is None else (self.prompt_tokens + prompt)
        )
        self.completion_tokens = (
            None
            if completion is None or self.completion_tokens is None
            else self.completion_tokens + completion
        )


@dataclass
class UsageMeter:
    """按任务累计的用量。`reset` 后重新开始，便于逐个案件统计。"""

    tasks: dict[LLMTask, TaskUsage] = field(default_factory=dict)

    def record(
        self, task: LLMTask, seconds: float, prompt: int | None, completion: int | None
    ) -> None:
        self.tasks.setdefault(task, TaskUsage()).add(seconds, prompt, completion)

    def reset(self) -> None:
        self.tasks.clear()

    def total(self) -> TaskUsage:
        total = TaskUsage()
        for usage in self.tasks.values():
            total.calls += usage.calls
            total.seconds += usage.seconds
            for name in ("prompt_tokens", "completion_tokens"):
                mine, theirs = getattr(total, name), getattr(usage, name)
                setattr(total, name, None if mine is None or theirs is None else mine + theirs)
        return total

    def lines(self) -> list[str]:
        """每个任务一行，最后是合计。耗时是各次调用之和（不含并行带来的重叠）。"""

        def fmt(name: str, usage: TaskUsage) -> str:
            tokens = (
                "token 未知"
                if usage.prompt_tokens is None or usage.completion_tokens is None
                else f"输入 {usage.prompt_tokens} · 输出 {usage.completion_tokens} token"
            )
            return f"{name}：{usage.calls} 次 · {usage.seconds:.1f} 秒 · {tokens}"

        rows = [fmt(task.value, usage) for task, usage in sorted(self.tasks.items())]
        return [*rows, fmt("合计", self.total())]


class CallLog:
    """每次 LLM 调用写一行 JSONL，文件按日期命名。`directory` 为空时什么都不做。"""

    def __init__(self, directory: Path | None) -> None:
        self._directory = directory

    def write(
        self,
        *,
        task: LLMTask,
        model: str,
        messages: Sequence[Any],
        response: str | None,
        seconds: float,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        at: datetime,
    ) -> None:
        if self._directory is None:
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{at:%Y%m%d}.jsonl"
        record = {
            "at": at.isoformat(),
            "task": task.value,
            "model": model,
            "seconds": round(seconds, 3),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "messages": list(messages),
            "response": response,
        }
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
