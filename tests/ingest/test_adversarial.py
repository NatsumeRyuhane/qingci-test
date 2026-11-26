"""构造的恶意输入不能让清洗变慢（#18）。

SYNTHETIC：全部是合成的构造输入，不来自客户数据（D-002）。旧的正则在这些输入上接近平方级，
3 万字符就需要 5 秒以上；改写后应在毫秒级完成。阈值放得很宽，只防止回退到超线性的写法。
"""

import time
from collections.abc import Callable

import pytest

from copilot.contracts import MessageRole
from copilot.ingest import SheetRow, build_cases
from copilot.ingest.text import SUPPORT_SENDER_NAME, normalize_whitespace, split_quote
from copilot.ingest.timestamps import outermost_header

N = 50_000
LIMIT_SECONDS = 1.0

INPUTS: dict[str, str] = {
    "gmail-whitespace": "2024年1月1日(月)" + " " * N + "x",
    "gmail-repeated-prefix": "2024年1月1日(月)1:00 " * (N // 18),
    "iphone-repeated-prefix": "2024/01/01 1:00、" * (N // 15),
    "generic-addresses": "<>" * (N // 2),
    "generic-support-name": SUPPORT_SENDER_NAME * (N // 18),
    "trailing-spaces": " " * N + "x",
    "trailing-rule": "【合成データ】\n----" + " " * N + "x",
    "stacked-rules": "【合成データ】" + "\n----\n" * (N // 6),
    "many-question-marks": "?" * N,
}


def _timed(fn: Callable[[], object]) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


@pytest.mark.parametrize("name", list(INPUTS))
def test_cleaning_is_fast_on_adversarial_input(name: str) -> None:
    text = INPUTS[name]
    assert _timed(lambda: split_quote(text)) < LIMIT_SECONDS
    assert _timed(lambda: normalize_whitespace(text)) < LIMIT_SECONDS
    assert _timed(lambda: outermost_header(text)) < LIMIT_SECONDS
    assert _timed(lambda: build_cases([SheetRow("1", 1, text)])) < LIMIT_SECONDS


def test_stacked_rules_are_still_removed() -> None:
    result = split_quote("【合成データ】本文\n\n----\n\n=====\n" + "> 引用")
    assert result.head.rstrip() == "【合成データ】本文"
    assert "----" not in result.head and "=====" not in result.head


def test_long_sender_name_is_not_a_header() -> None:
    text = "2099年1月2日(金) 10:00 " + "あ" * 300 + " <>:\n> 前"
    (case,), _ = build_cases([SheetRow("1", 2, "【合成データ】本文\n" + text)])
    assert case.messages[0].role is MessageRole.PLAYER
