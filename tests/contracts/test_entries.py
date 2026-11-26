"""意图、事实、政策、术语表条目、公告与事件的校验规则。数据全部是合成的。"""

import time
from datetime import datetime

import pytest
from pydantic import ValidationError

from copilot.contracts import Fact, Intent, Notice, Policy, select_valid
from copilot.ingest.timestamps import JST
from tests.fixtures import load_items, load_models

# --- Intent ---


def test_only_low_risk_intents_can_be_auto_send_eligible() -> None:
    item = load_items("intent")[2] | {"default_risk": "normal"}
    with pytest.raises(ValidationError, match="允许清单"):
        Intent.model_validate(item)


# --- Policy ---


def test_policy_matches_phrases_after_normalization() -> None:
    policy = load_models(Policy, "policy")[0]
    # 全角半角、兼容字符在 NFKC 规范化后视为相同
    assert policy.matching_phrases("【合成データ】全額を返金いたします。") == ["返金いたします"]
    assert policy.matching_phrases("【合成データ】調査いたします。") == []


def test_policy_matching_is_linear_on_adversarial_input() -> None:
    """固定短语的子串匹配，构造的长输入也不会回溯（#18）。"""
    policy = load_models(Policy, "policy")[0]
    text = "返金" * 50_000 + "いたしま"
    start = time.perf_counter()
    assert policy.matching_phrases(text) == []
    assert time.perf_counter() - start < 1.0


def test_policy_needs_a_checkable_condition() -> None:
    item = load_items("policy")[1] | {"phrases_ja": [], "sensitivity_tags": []}
    with pytest.raises(ValidationError, match="可以由代码检查"):
        Policy.model_validate(item)


def test_forbidden_promise_needs_phrases() -> None:
    item = load_items("policy")[0] | {"phrases_ja": []}
    with pytest.raises(ValidationError, match="phrases_ja"):
        Policy.model_validate(item)


def test_forbidden_promise_cannot_use_tags() -> None:
    item = load_items("policy")[0] | {"sensitivity_tags": ["legal"]}
    with pytest.raises(ValidationError, match="escalation_trigger"):
        Policy.model_validate(item)


def test_blank_phrases_are_rejected() -> None:
    item = load_items("policy")[0] | {"phrases_ja": ["返金いたします", "  "]}
    with pytest.raises(ValidationError, match="空短语"):
        Policy.model_validate(item)


# --- Notice ---


def test_event_time_is_separate_from_validity() -> None:
    """事件发生在 3 月 2 日 11:00–14:00；公告到 3 月底都有效，3 月 5 日来信的玩家仍能匹配。"""
    notice = load_models(Notice, "notice")[0]
    assert notice.event_covers(datetime(2024, 3, 2, 12, 30, tzinfo=JST))
    assert not notice.event_covers(datetime(2024, 3, 2, 14, 0, tzinfo=JST))
    assert not notice.event_covers(datetime(2024, 3, 2, 10, 59, tzinfo=JST))
    assert notice.is_valid_at(datetime(2024, 3, 5, tzinfo=JST))
    assert not notice.is_valid_at(datetime(2024, 4, 1, tzinfo=JST))


def test_ongoing_event_has_no_end() -> None:
    notice = Notice.model_validate(load_items("notice")[0] | {"event_end": None})
    assert notice.event_covers(datetime(2099, 1, 1, tzinfo=JST))


def test_event_must_be_ordered() -> None:
    item = load_items("notice")[0]
    item["event_start"], item["event_end"] = item["event_end"], item["event_start"]
    with pytest.raises(ValidationError, match="event_start"):
        Notice.model_validate(item)


# --- 版本选择对所有类型都适用 ---


def test_entries_of_different_types_may_share_an_id() -> None:
    fact = load_models(Fact, "fact")[0]
    notice = Notice.model_validate(load_items("notice")[0] | {"id": fact.id})
    selected = select_valid([fact, notice], datetime(2024, 3, 2, 12, tzinfo=JST))
    assert {type(entry) for entry in selected} == {Fact, Notice}
