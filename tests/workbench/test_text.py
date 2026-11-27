import pytest

from copilot.workbench import levenshtein


@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [
        ("", "", 0),
        ("abc", "abc", 0),
        ("", "abc", 3),
        ("kitten", "sitting", 3),
        ("お問い合わせありがとうございます", "お問い合わせ誠にありがとうございます", 2),
        (
            "前半はそのまま。ここを変更。後半もそのまま。",
            "前半はそのまま。ここを修正した。後半もそのまま。",
            4,
        ),
        ("aaaa", "aa", 2),
    ],
)
def test_levenshtein(a: str, b: str, distance: int) -> None:
    assert levenshtein(a, b) == distance
    assert levenshtein(b, a) == distance
