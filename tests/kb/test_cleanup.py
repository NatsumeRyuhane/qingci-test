"""模板正文的清理。SYNTHETIC：全部是合成文本，形式仿照历史邮件的硬换行（D-002）。"""

import pytest

from copilot.kb.cleanup import clean_body, drop_trailing_closing, unwrap_lines


def test_unwraps_lines_broken_mid_word() -> None:
    wrapped = (
        "【合成データ】上記をお試しいただいたうえで、ご自身での復旧が難しい場合、お客様のアカウ\n"
        "ントの状況を確認したいため、下記情報をお送りいただけないでし\n"
        "ょうか。"
    )
    assert unwrap_lines(wrapped) == (
        "【合成データ】上記をお試しいただいたうえで、ご自身での復旧が難しい場合、お客様のアカウ"
        "ントの状況を確認したいため、下記情報をお送りいただけないでしょうか。"
    )


@pytest.mark.parametrize(
    "text",
    [
        "【合成データ】一文目です。\n二文目です。",  # 句末
        "【合成データ】情報の送付をお願いいたします\n\n次の段落",  # 空行（段落）
        "-----------------------------------------------------\n・プレイヤー名：",  # 分隔线、列表
        # 下一行是列表
        "【合成データ】以下の内容をご確認いただけますようお願いいたします\n・項目一",
        "■注意事項\n【合成データ】アプリを再起動してください。",  # 短的标题行
        "【合成データ】確認のうえで以下の番号をお知らせください：\n123456",  # 冒号
        "【合成データ】受け取り期間は次の通りとなっております。予めご了承\n【2099年1月1日】",
        "【合成データ】お客様のプレイヤー名を{{player_name}}\nと記載してください。",  # 占位符
    ],
)
def test_keeps_intentional_line_breaks(text: str) -> None:
    assert unwrap_lines(text) == text


def test_unwrapping_keeps_paragraphs_and_lists() -> None:
    text = (
        "【合成データ】※購入時のレシートメール、もしくは購入履歴のスクリーンショットをお送\n"
        "りください。\n"
        "※注文番号、日付を確認できるもの\n"
        "\n"
        "-----\n"
        "・プレイヤー名："
    )
    assert unwrap_lines(text) == text.replace("お送\nり", "お送り")


@pytest.mark.parametrize(
    "closing",
    [
        "引き続き『ぽちゃガチョ！』をよろしくお願いいたします。",
        "今後とも『ぽちゃガチョ！』を何卒よろしくお願いいたします。",
        "今後とも、『ぽちゃガチョ！』をよろしくお願い致します",
    ],
)
def test_drops_trailing_game_closing(closing: str) -> None:
    body = f"【合成データ】ご連絡ありがとうございます。\n\n{closing}\n"
    assert drop_trailing_closing(body) == "【合成データ】ご連絡ありがとうございます。"


def test_keeps_closing_like_sentences_with_content() -> None:
    body = (
        "【合成データ】ご連絡ありがとうございます。\n"
        "これからもチーム一同尽力して参りますので、何卒よろしくお願い申し上げます。"
    )
    assert drop_trailing_closing(body) == body
    middle = "引き続き『ぽちゃガチョ！』をよろしくお願いいたします。\n【合成データ】追伸です。"
    assert drop_trailing_closing(middle) == middle  # 只去掉最后一行


def test_clean_body_does_both() -> None:
    body = (
        "【合成データ】情報が不足している場合など、対応できかねる場合がございますのでお願\n"
        "い申し上げます。\n\n引き続き『ぽちゃガチョ！』をよろしくお願いいたします。"
    )
    assert clean_body(body) == (
        "【合成データ】情報が不足している場合など、対応できかねる場合がございますので"
        "お願い申し上げます。"
    )
