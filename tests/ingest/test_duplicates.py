"""重复提交的识别。SYNTHETIC：全部是合成数据。"""

from copilot.ingest.duplicates import MIN_CONTENT_CHARS, duplicate_groups, duplicate_key
from tests.cases import case

TEXT = "【合成データ】ログインボーナスが受け取れません。何度試しても同じです。"


def test_same_inquiry_is_grouped_in_thread_order() -> None:
    cases = [
        case("12", content=TEXT),
        case("3", content=TEXT),
        case("10", content=TEXT),
        case("5", content="【合成データ】別の内容の問い合わせです。ガチャが回せません。"),
    ]
    assert duplicate_groups(cases) == [["3", "10", "12"]]


def test_width_and_whitespace_do_not_matter() -> None:
    narrow = "【合成データ】ﾛｸﾞｲﾝﾎﾞｰﾅｽ が 受け取れません。何度試しても同じです。"
    wide = "【合成データ】ログインボーナスが\n受け取れません。何度試しても同じです。"
    assert duplicate_key(case("1", content=narrow)) == duplicate_key(case("2", content=wide))


def test_other_fields_must_match() -> None:
    base = case("1", content=TEXT)
    for other in (
        case("2", content=TEXT, device="SYN-OTHER"),
        case("2", content=TEXT, app_version="9.9.8"),
        case("2", content=TEXT, occurred_at="2099-01-03 09:00"),
    ):
        assert duplicate_groups([base, other]) == []


def test_short_or_missing_content_is_not_compared() -> None:
    short = "【合成】" + "あ" * (MIN_CONTENT_CHARS - 5)
    assert duplicate_key(case("1", content=short)) is None
    assert duplicate_key(case("1")) is None  # 没有表单
    assert duplicate_groups([case("1", content=short), case("2", content=short)]) == []
