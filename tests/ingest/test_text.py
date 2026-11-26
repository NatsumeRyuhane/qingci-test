"""引用去除、字符修复、空白规范化和问候语/结尾拆分。

SYNTHETIC：全部是模仿样本格式编写的合成数据（带 【合成データ】 标记），不来自客户数据（D-002）。
"""

import pytest

from copilot.ingest.text import (
    CharFix,
    QuoteFormat,
    fix_chars,
    normalize_whitespace,
    split_frame,
    split_quote,
)

SUPPORT = "【ぽちゃガチョ！カスタマーサポート】"
GREETING = "いつもご利用いただきありがとうございます。\n『ぽちゃガチョ！』サポート担当です。"
CLOSING = (
    "今後とも『ぽちゃガチョ！』をよろしくお願いいたします。\n"
    "※本メール内容の無断掲載、無断複製、転送はお控えください。"
)


# --- 引用 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "head", "fmt"),
    [
        pytest.param(
            "【合成データ】直りました\n\n> 前のメール\n>> さらに前",
            "【合成データ】直りました\n\n",
            QuoteFormat.ANGLE,
            id="angle-nested",
        ),
        pytest.param(
            f"【合成データ】まだです\n\n2099年1月2日(金) 10:00 {SUPPORT} <>:\n\n> 前",
            "【合成データ】まだです\n\n",
            QuoteFormat.GMAIL,
            id="gmail",
        ),
        pytest.param(
            "【合成データ】まだです\n2099年1月2日(金) 午後6:12 テスト <>:\n> 前",
            "【合成データ】まだです\n",
            QuoteFormat.GMAIL,
            id="gmail-ampm",
        ),
        pytest.param(
            "【合成データ】解決\niPhoneから送信\n\n"
            f"2099/01/02 10:00、{SUPPORT} <>のメール:\n\n?\nいつも",
            "【合成データ】解決\niPhoneから送信\n\n",
            QuoteFormat.IPHONE,
            id="iphone-without-angle",
        ),
        pytest.param(
            "【合成データ】追記\n------ Original Message ------\n 木曜日, 2099/1/2 10:00",
            "【合成データ】追記\n",
            QuoteFormat.SEPARATOR,
            id="original-message",
        ),
        pytest.param(
            "【合成データ】追記\n\n_________________________\n\n2099/01/02 10:00:00 【…】:\n前",
            "【合成データ】追記\n\n",
            QuoteFormat.SEPARATOR,
            id="underscore-line",
        ),
        pytest.param(
            "【合成データ】Thanks\n\nOn Fri, Jan 2, 2099 at 10:00 AM Test <> wrote:\n> prev",
            "【合成データ】Thanks\n\n",
            QuoteFormat.ENGLISH,
            id="english",
        ),
        pytest.param(
            "【合成データ】terima kasih\n\n"
            f"Pada Jum, 2 Jan 2099 pukul 10.00 {SUPPORT} <\n> menulis:\n\n> prev",
            "【合成データ】terima kasih\n\n",
            QuoteFormat.OTHER_LEADER,
            id="other-language-wrapped",
        ),
        pytest.param(
            f"【合成データ】追記\n----------------\nFrom: {SUPPORT} <>\n"
            "日付: 2099年01月02日 10:00\n件名: テスト\n\n前",
            "【合成データ】追記",
            QuoteFormat.HEADER_BLOCK,
            id="header-block-with-rule-above",
        ),
    ],
)
def test_split_quote_formats(text: str, head: str, fmt: QuoteFormat) -> None:
    result = split_quote(text)
    assert result.format is fmt
    assert result.head == head
    assert result.head + result.quoted == text
    assert not result.inline


def test_split_quote_compressed_into_one_line() -> None:
    text = "【合成データ】解決しました 2099/01/02 10:00、テスト <>のメール: > いつも"
    result = split_quote(text)
    assert result.inline
    assert result.head == "【合成データ】解決しました "
    assert result.quoted.startswith("2099/01/02")


def test_split_quote_without_quote() -> None:
    result = split_quote("【合成データ】引用なし")
    assert result.format is None
    assert result.quoted == ""


def test_earliest_quote_start_wins() -> None:
    text = "【合成データ】本文\n> 引用\n2099年1月2日(金) 10:00 テスト <>:\n> もっと前"
    assert split_quote(text).format is QuoteFormat.ANGLE


# --- 字符修复 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "fixed", "rule"),
    [
        (
            "お問い合わせ内容の種類 : ご意見?ご要望",
            "お問い合わせ内容の種類 : ご意見・ご要望",
            CharFix.KNOWN_PHRASE,
        ),
        ("?発生日時\n?所持数", "・発生日時\n・所持数", CharFix.BULLET),
        (
            "運営?開発チームとアカウント?メール",
            "運営・開発チームとアカウント・メール",
            CharFix.SEPARATOR,
        ),
        ("?\n本文", "\n本文", CharFix.LONE),
        ("?いつもご利用いただき", "いつもご利用いただき", CharFix.LONE),
    ],
)
def test_fix_chars_rules(text: str, fixed: str, rule: CharFix) -> None:
    result, fixes = fix_chars(text)
    assert result == fixed
    assert set(fixes) == {rule}


@pytest.mark.parametrize(
    "text",
    [
        "届いていますか?",
        "ですか?よろしくお願いします",
        "全角の問号は？そのまま",
        "1.4?",
    ],
)
def test_fix_chars_keeps_real_question_marks(text: str) -> None:
    result, fixes = fix_chars(text)
    assert result == text
    assert not fixes


# --- 空白 ---------------------------------------------------------------------


def test_normalize_whitespace() -> None:
    text = "\n\n\n【合成データ】一行目   \n\n\n\n　二行目　\n\n"
    assert normalize_whitespace(text) == "【合成データ】一行目\n\n　二行目"


# --- 问候语和结尾 -------------------------------------------------------------


def test_split_frame() -> None:
    body = f"{GREETING}\n\n【合成データ】ご案内いたします。\n\n{CLOSING}"
    greeting, closing = split_frame(body)
    assert greeting == GREETING
    assert closing == CLOSING
    assert body.startswith(greeting)
    assert body.endswith(closing)


def test_split_frame_notice_only() -> None:
    notice = "※本メール内容の無断掲載、無断複製、転送はお控えください。"
    body = f"{GREETING}\n\n【合成データ】ご案内いたします。\n\n{notice}"
    assert split_frame(body) == (GREETING, notice)


def test_split_frame_without_frame() -> None:
    assert split_frame("【合成データ】問題が解決しました。") == ("", "")


def test_greeting_far_from_start_is_not_used() -> None:
    body = "【合成データ】" + "あ" * 400 + "\n『ぽちゃガチョ！』サポート担当です。"
    assert split_frame(body)[0] == ""
