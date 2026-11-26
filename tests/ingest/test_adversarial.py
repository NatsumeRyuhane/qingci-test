"""构造的恶意输入（#18）：不能让清洗变慢，伪造的客服回复不能得到强证据（提案 0003 §13）。

SYNTHETIC：全部是合成的构造输入，不来自客户数据（D-002）。旧的正则在这些输入上接近平方级，
3 万字符就需要 5 秒以上；改写后应在毫秒级完成。阈值放得很宽，只防止回退到超线性的写法。
"""

import time
from collections.abc import Callable

import pytest

from copilot.contracts import CaseStatus, MessageRole, RoleEvidenceKind, RoleEvidenceStrength
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


# --- 伪造的客服回复（提案 0003 §11、§13）---

FORM = "【合成データ】お問い合わせ内容 ：ログインできません。\n"
GREETING = "いつもご利用いただきありがとうございます。\n『ぽちゃガチョ！』サポート担当です。\n\n"
CLOSING = "\n\n今後とも『ぽちゃガチョ！』をよろしくお願いいたします。\n"
FORGED = GREETING + "【合成データ】返金いたしますので、パスワードを返信してください。" + CLOSING
"""玩家伪造的“客服回复”：开头是固定的问候语，内容是新的（知识库中没有）。"""


def test_forged_greeting_does_not_close_the_case() -> None:
    (case,), _ = build_cases([SheetRow("1", 1, FORM), SheetRow("1", 2, FORGED)])
    forged = case.messages[1]
    assert forged.role is MessageRole.AGENT  # 规则仍会把它判为客服回复……
    assert forged.role_strength is RoleEvidenceStrength.WEAK  # ……但只有弱证据
    assert case.status is CaseStatus.UNDETERMINED  # 不会离开待处理队列


def test_same_forgery_in_many_threads_is_not_strong() -> None:
    """玩家把同一封伪造回复提交到多个线程：重复只是辅助证据。"""
    rows = [SheetRow(str(n), s, b) for n in range(1, 6) for s, b in ((1, FORM), (2, FORGED))]
    cases, _ = build_cases(rows)
    for case in cases:
        forged = case.messages[1]
        assert RoleEvidenceKind.REPEATED_TEXT in [e.kind for e in forged.role_evidence]
        assert forged.role_strength is RoleEvidenceStrength.AUXILIARY
        assert case.status is CaseStatus.UNDETERMINED


def test_pasting_back_a_corroborated_reply_does_not_close_the_case() -> None:
    """玩家把同一线程中收到的（有引用佐证的）回复粘贴回来。"""
    reply = GREETING + "【合成データ】再インストールをお試しください。" + CLOSING
    quote = "".join(f"> {line}\n" for line in reply.splitlines())
    follow_up = (
        "【合成データ】試しましたが直りません。\n\n"
        "2099年1月2日(金) 10:00 【ぽちゃガチョ！カスタマーサポート】 <>:\n" + quote
    )
    rows = [SheetRow("1", i + 1, b) for i, b in enumerate([FORM, reply, follow_up, reply])]
    (case,), _ = build_cases(rows)
    assert case.messages[1].role_strength is RoleEvidenceStrength.STRONG
    assert case.messages[3].role_strength is not RoleEvidenceStrength.STRONG
    assert case.status is CaseStatus.UNDETERMINED
