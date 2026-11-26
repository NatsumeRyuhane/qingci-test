"""表单解析与发送方判断。

SYNTHETIC：全部是模仿样本格式编写的合成数据（带 【合成データ】 标记），不来自客户数据（D-002）。
"""

import pytest

from copilot.contracts import InquiryCategory, MessageRole, Platform
from copilot.ingest.form import parse_fields, to_inquiry_form
from copilot.ingest.roles import RoleReason, detect_role

FORM = "\n".join(
    [
        "お問い合わせ",
        "アカウントID : ",
        "",
        "プレイヤー名 : ",
        "アプリバージョン : 9.9.9",
        "ご利用のOSバージョン : ",
        "ご利用端末名 : テスト端末　1",
        "お問い合わせ内容の種類 : ご意見・ご要望",
        "ご利用環境 : Google Play",
        "問題が発生した日時 : 2099-01-02 10:00",
        "メールアドレス : ",
        "お問い合わせ内容 ：【合成データ】一行目",
        "二行目",
        "",
        "添付ファイル ：",
    ]
)


def test_parse_fields() -> None:
    fields = parse_fields(FORM)
    assert fields["アプリバージョン"] == "9.9.9"
    assert fields["お問い合わせ内容の種類"] == "ご意見・ご要望"
    assert fields["お問い合わせ内容"] == "【合成データ】一行目\n二行目"
    assert fields["アカウントID"] == ""
    assert fields["添付ファイル"] == ""


def test_to_inquiry_form_treats_empty_as_missing() -> None:
    form = to_inquiry_form(parse_fields(FORM))
    assert form.app_version == "9.9.9"
    assert form.os_version is None
    assert form.device == "テスト端末　1"
    assert form.category is InquiryCategory.FEEDBACK
    assert form.platform is Platform.GOOGLE_PLAY
    assert form.occurred_at == "2099-01-02 10:00"
    assert form.content == "【合成データ】一行目\n二行目"
    assert form.has_attachment is None


@pytest.mark.parametrize(
    ("value", "category"),
    [
        ("不具合について", InquiryCategory.BUG),
        ("アイテム購入時に問題が発生した", InquiryCategory.PURCHASE),
        ("データの引き継ぎについて", InquiryCategory.DATA_TRANSFER),
        ("【合成データ】その他", InquiryCategory.OTHER),
    ],
)
def test_category_mapping(value: str, category: InquiryCategory) -> None:
    assert to_inquiry_form({"お問い合わせ内容の種類": value}).category is category


@pytest.mark.parametrize(
    ("value", "platform"),
    [("iOS", Platform.IOS), ("Google Play", Platform.GOOGLE_PLAY), ("PC", Platform.OTHER)],
)
def test_platform_mapping(value: str, platform: Platform) -> None:
    assert to_inquiry_form({"ご利用環境": value}).platform is platform


@pytest.mark.parametrize(("value", "expected"), [("なし", False), ("test.png", True)])
def test_attachment(value: str, expected: bool) -> None:
    assert to_inquiry_form({"添付ファイル": value}).has_attachment is expected


def test_repeated_field_keeps_first() -> None:
    fields = parse_fields("アプリバージョン : 1.0\nアプリバージョン : 2.0")
    assert fields["アプリバージョン"] == "1.0"


# --- 发送方 -------------------------------------------------------------------

AGENT = (
    "いつもご利用いただきありがとうございます。\n『ぽちゃガチョ！』サポート担当です。\n\n"
    "【合成データ】"
)


@pytest.mark.parametrize(
    ("seq", "raw", "body", "role", "reason"),
    [
        (2, "  \n", "", MessageRole.UNKNOWN, RoleReason.EMPTY),
        (2, AGENT, AGENT, MessageRole.AGENT, RoleReason.GREETING),
        (
            1,
            "【合成データ】\n  The mail system\n",
            "【合成データ】\n  The mail system",
            MessageRole.SYSTEM,
            RoleReason.SYSTEM,
        ),
        (
            1,
            "【合成データ】メールボックスのメール一括返信作業のレポートです。",
            "【合成データ】メールボックスのメール一括返信作業のレポートです。",
            MessageRole.SYSTEM,
            RoleReason.SYSTEM,
        ),
        (1, FORM, FORM, MessageRole.PLAYER, RoleReason.FORM),
        (
            3,
            "【合成データ】\n今後とも『ぽちゃガチョ！』をよろしくお願いいたします。",
            "【合成データ】\n今後とも『ぽちゃガチョ！』をよろしくお願いいたします。",
            MessageRole.AGENT,
            RoleReason.CLOSING_ONLY,
        ),
        (2, "> 【合成データ】引用だけ", "", MessageRole.UNKNOWN, RoleReason.EMPTY_AFTER_QUOTE),
        (
            1,
            "【合成データ】ご担当者様",
            "【合成データ】ご担当者様",
            MessageRole.PLAYER,
            RoleReason.FIRST_MESSAGE,
        ),
        (
            3,
            "【合成データ】まだ直りません",
            "【合成データ】まだ直りません",
            MessageRole.PLAYER,
            RoleReason.FOLLOW_UP,
        ),
    ],
)
def test_detect_role(seq: int, raw: str, body: str, role: MessageRole, reason: RoleReason) -> None:
    assert detect_role(seq, raw, body) == (role, reason)


def test_bounce_containing_a_form_is_system() -> None:
    body = (
        "【合成データ】The mail system\n\n<>: host mx.example.test[192.0.2.1]\n    said: 550\n\n"
        + FORM
    )
    assert detect_role(1, body, body)[0] is MessageRole.SYSTEM
