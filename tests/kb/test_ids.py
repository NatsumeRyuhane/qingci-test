"""稳定的模板 ID（提案 0003 §12）。SYNTHETIC：全部是合成数据。"""

from copilot.kb.ids import IdentityInput, PreviousIdentity, assign_ids, text_id


def _c(key: str, *members: str) -> IdentityInput:
    return IdentityInput(key=key, members=frozenset(members))


def _p(id_: str, key: str, *members: str) -> PreviousIdentity:
    return PreviousIdentity(id=id_, key=key, members=frozenset(members))


def test_first_build_ids_come_from_the_text() -> None:
    result = assign_ids([_c("a", "1:2"), _c("b", "2:2")], [])
    assert result.ids == [text_id("a"), text_id("b")]
    assert result.new == 2 and result.ids[0].startswith("tpl-")
    assert assign_ids([_c("a", "1:2"), _c("b", "2:2")], []).ids == result.ids  # 可以复现


def test_same_text_keeps_its_id() -> None:
    result = assign_ids([_c("a", "9:2")], [_p("tpl-reviewed", "a", "1:2")])
    assert result.ids == ["tpl-reviewed"] and result.reused_by_text == 1


def test_changed_representative_keeps_id_by_member_overlap() -> None:
    """新回复让另一种正文成了代表正文，但来源回复大部分相同。"""
    previous = [_p("tpl-reviewed", "old", "1:2", "2:2", "3:2")]
    result = assign_ids([_c("new", "1:2", "2:2", "3:2", "4:2")], previous)
    assert result.ids == ["tpl-reviewed"] and result.reused_by_members == 1


def test_split_cluster_is_ambiguous_not_guessed() -> None:
    """上一次的一个簇这一次拆成两个：两边都与它重合，不猜测，生成新 ID 并报告。"""
    previous = [_p("tpl-reviewed", "old", "1:2", "2:2", "3:2", "4:2")]
    clusters = [_c("x", "1:2", "2:2", "3:2"), _c("y", "2:2", "3:2", "4:2")]
    result = assign_ids(clusters, previous)
    assert "tpl-reviewed" not in result.ids
    assert sorted(result.ambiguous) == sorted(result.ids)
    assert result.disappeared == ["tpl-reviewed"]


def test_disappeared_ids_are_reported_and_never_reused() -> None:
    previous = [_p(text_id("b"), "gone", "8:2")]
    result = assign_ids([_c("b", "1:2")], previous)
    assert result.ids == [text_id("b") + "-2"]  # 不会把消失的 ID 分给另一个模板
    assert result.disappeared == [text_id("b")]
