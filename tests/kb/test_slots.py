"""槽位与特定内容的识别（#5）。SYNTHETIC：全部是合成数据。"""

import time

from copilot.contracts import ReplyTemplate
from copilot.kb.slots import SpanKind, detect_slots, find_spans

WINDOW = "【合成データ】3月2日11:00～14:00の間、ストアで購入できない不具合が発生しておりました。"
ITEM = "【合成データ】「きらきらの羽」を2024年3月10日に付与いたしました。"


def test_find_spans_prefers_windows_and_datetimes() -> None:
    kinds = [(s.kind, s.text) for s in find_spans(WINDOW)]
    assert kinds == [(SpanKind.WINDOW, "3月2日11:00～14:00")]
    text = "【合成データ】2024/03/02 15:53:57 のご購入、全角は１２：３０、数は1,200"
    assert [(s.kind, s.text) for s in find_spans(text)] == [
        (SpanKind.DATETIME, "2024/03/02 15:53:57"),
        (SpanKind.TIME, "１２：３０"),
        (SpanKind.NUMBER, "1,200"),
    ]


def test_fixed_incident_window_is_specific_not_a_slot() -> None:
    result = detect_slots(WINDOW, [WINDOW + "\nご不便をおかけしました。"])
    assert result.slots == [] and result.body_ja == WINDOW
    assert [s.text for s in result.specific] == ["3月2日11:00～14:00"]


def test_values_that_vary_become_slots() -> None:
    variant = ITEM.replace("きらきらの羽", "ふわふわの卵").replace("3月10日", "3月12日")
    result = detect_slots(ITEM, [variant])
    assert result.body_ja == "【合成データ】「{{name_1}}」を{{date_1}}に付与いたしました。"
    assert [(s.name, s.example_ja) for s in result.slots] == [
        ("name_1", "きらきらの羽"),
        ("date_1", "2024年3月10日"),
    ]
    assert result.specific == []


def test_detected_slots_form_a_valid_template_body() -> None:
    variant = ITEM.replace("3月10日", "3月12日")
    result = detect_slots(ITEM, [variant])
    ReplyTemplate.model_validate(
        {
            "id": "tpl-syn",
            "version": 1,
            "known_from": "2099-01-01T00:00:00+09:00",
            "recorded_at": "2099-01-01T00:00:00+09:00",
            "origin": "manual",
            "intent": "syn",
            "category": "bug",
            "title_zh": "合成",
            "description_zh": "",
            "use_when_zh": "",
            "avoid_when_zh": "",
            "body_ja": result.body_ja,
            "slots": [s.model_dump() for s in result.slots],
        }
    )


def test_large_edits_through_a_date_do_not_make_it_a_slot() -> None:
    longer = ITEM + "ご確認ください。"
    shorter = "【合成データ】付与いたしました。"
    result = detect_slots(longer, [shorter])
    assert result.slots == []


def test_span_search_is_linear_on_adversarial_input() -> None:
    for text in ("1" * 50_000, "1月" * 25_000, "「" * 50_000, "11:" * 20_000):
        start = time.perf_counter()
        find_spans(text)
        assert time.perf_counter() - start < 1.0


def test_long_variants_are_not_diffed() -> None:
    long = "【合成データ】" + "あい" * 30_000
    start = time.perf_counter()
    result = detect_slots(ITEM, [long + "3月10日"])
    detect_slots(long, [long[::-1]])
    assert time.perf_counter() - start < 1.0
    assert result.slots == []
