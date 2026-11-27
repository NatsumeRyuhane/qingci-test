"""知识库条目的公共字段与版本选择规则（提案 0003 §3）。数据全部是合成的。"""

from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from copilot.contracts import ReplyTemplate, ReviewStatus, current_version, select_valid
from copilot.ingest.timestamps import JST
from tests.fixtures import load_items


def at(day: int, hour: int = 0) -> datetime:
    return datetime(2024, 3, day, hour, tzinfo=JST)


def template(version: int = 1, **fields: Any) -> ReplyTemplate:
    """以 `feedback-thanks` 为底的模板。默认在 3 月 1 日已知、3 月 28 日录入、一直有效、未审核。"""
    item = load_items("reply_template")[2] | {
        "version": version,
        "known_from": at(1).isoformat(),
        "recorded_at": at(28).isoformat(),
        "review_status": "draft",
        "auto_send_allowed": False,
    }
    for key, value in fields.items():
        item[key] = value.isoformat() if isinstance(value, datetime) else value
    return ReplyTemplate.model_validate(item)


# --- current_version ---


def test_latest_version_wins() -> None:
    assert current_version([template(1), template(3), template(2)]) == template(3)


def test_retired_latest_version_removes_the_entry() -> None:
    assert current_version([template(1), template(2, review_status="retired")]) is None


def test_old_version_does_not_come_back_after_newer_expires() -> None:
    """v1 一直有效，v2 把有效期关闭在 3 月 10 日。之后不能回退到 v1。"""
    versions = [template(1), template(2, valid_to=at(10))]
    assert [t.version for t in select_valid(versions, at(5))] == [2]
    assert select_valid(versions, at(20)) == []


def test_old_version_does_not_come_back_after_newer_is_retired() -> None:
    versions = [template(1), template(2), template(3, review_status="retired")]
    assert select_valid(versions, at(5)) == []


def test_replay_sees_only_versions_known_at_the_time() -> None:
    """v2 在 3 月 10 日才知道：3 月 5 日的重放只能看到 v1，看不到当时还不存在的修正。"""
    versions = [template(1), template(2, known_from=at(10))]
    assert current_version(versions, known_at=at(5)) == template(1)
    assert current_version(versions, known_at=at(10)) == template(2, known_from=at(10))
    assert current_version(versions) == template(2, known_from=at(10))


def test_nothing_known_yet() -> None:
    versions = [template(1, known_from=at(10))]
    assert current_version(versions, known_at=at(5)) is None


def test_real_world_change_is_a_new_entry() -> None:
    """3 月 10 日政策变化：旧条目新建一个关闭有效期的版本，新内容是取代它的新条目。"""
    old = [template(1), template(2, valid_to=at(10))]
    new = template(1, id="feedback-thanks-v2", valid_from=at(10), supersedes=["feedback-thanks"])
    entries = [*old, new]
    assert [t.id for t in select_valid(entries, at(5))] == ["feedback-thanks"]
    assert [t.id for t in select_valid(entries, at(10))] == ["feedback-thanks-v2"]


def test_validity_window_is_half_open() -> None:
    entry = template(valid_from=at(2), valid_to=at(3))
    assert not entry.is_valid_at(at(1, 23))
    assert entry.is_valid_at(at(2))
    assert entry.is_valid_at(at(2, 23))
    assert not entry.is_valid_at(at(3))


def test_versions_must_belong_to_one_entry() -> None:
    with pytest.raises(ValueError, match="同一个条目"):
        current_version([template(1), template(2, id="other")])


def test_duplicate_version_numbers_are_rejected() -> None:
    with pytest.raises(ValueError, match="版本号重复"):
        current_version([template(1), template(1, title_zh="改过的标题")])


def test_select_valid_handles_many_entries() -> None:
    entries = [template(1, id="b"), template(1, id="a"), template(1, id="c", valid_to=at(2))]
    assert [t.id for t in select_valid(entries, at(5))] == ["a", "b"]


# --- 审核状态 ---


@pytest.mark.parametrize(
    ("status", "visible"),
    [("draft", False), ("approved", True), ("retired", False), ("needs_rereview", False)],
)
def test_only_approved_entries_are_visible_to_the_model(status: str, visible: bool) -> None:
    assert template(review_status=status).model_visible is visible
    assert "model_visible" not in template().model_dump()


def test_auto_send_requires_approval() -> None:
    with pytest.raises(ValidationError, match="已批准"):
        template(review_status="draft", auto_send_allowed=True)
    assert template(review_status="approved", auto_send_allowed=True).auto_send_allowed


# --- 公共字段的校验 ---


def test_known_from_cannot_be_after_recorded_at() -> None:
    with pytest.raises(ValidationError, match="known_from"):
        template(known_from=at(10), recorded_at=at(5))


def test_reconstructed_entry_needs_sources() -> None:
    with pytest.raises(ValidationError, match="source_message_ids"):
        template(origin="reconstructed", source_message_ids=[])
    assert template(origin="reconstructed", source_message_ids=["SYN-0001:2"])


def test_entry_cannot_supersede_itself() -> None:
    with pytest.raises(ValidationError, match="取代自己"):
        template(supersedes=["feedback-thanks"])


@pytest.mark.parametrize("entry_id", ["Feedback", "-x", "a/b", "a b", "a" * 65, ""])
def test_entry_id_is_restricted(entry_id: str) -> None:
    with pytest.raises(ValidationError, match="id"):
        template(id=entry_id)


def test_template_requires_intent() -> None:
    item = load_items("reply_template")[0]
    del item["intent"]
    with pytest.raises(ValidationError, match="intent"):
        ReplyTemplate.model_validate(item)


def test_review_status_defaults_to_draft() -> None:
    item = load_items("reply_template")[0]
    del item["review_status"]
    assert ReplyTemplate.model_validate(item).review_status is ReviewStatus.DRAFT
