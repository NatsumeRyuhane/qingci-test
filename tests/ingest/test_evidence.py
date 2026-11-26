"""角色证据与案件状态（提案 0003 §11）。数据全部是合成的。"""

from copilot.contracts import (
    CaseStatus,
    Message,
    MessageRole,
    RoleEvidenceKind,
    RoleEvidenceStrength,
)
from copilot.ingest import SheetRow, build_cases
from tests.ingest.test_pipeline import AGENT, FOLLOW_UP, FORM


def kinds(message: Message) -> list[RoleEvidenceKind]:
    return [e.kind for e in message.role_evidence]


def test_quote_corroboration_is_strong() -> None:
    """玩家的追问用标明客服发件人的引导行引用了这封回复。"""
    (case,), _ = build_cases(
        [SheetRow("1", i + 1, b) for i, b in enumerate([FORM, AGENT, FOLLOW_UP])]
    )
    reply = case.messages[1]
    assert RoleEvidenceKind.QUOTE_CORROBORATION in kinds(reply)
    assert reply.role_evidence[-1].source_message_id == "1:3"
    assert reply.role_strength is RoleEvidenceStrength.STRONG
    assert case.status is CaseStatus.OPEN


def test_reply_matching_a_corroborated_reply_counts_as_replied() -> None:
    """线程 2 的回复与线程 1 中有引用佐证的回复正文相同，线程 2 可以判为已回复。"""
    rows = [
        *[SheetRow("1", i + 1, b) for i, b in enumerate([FORM, AGENT, FOLLOW_UP])],
        SheetRow("2", 1, FORM),
        SheetRow("2", 2, AGENT),
    ]
    (_, second), stats = build_cases(rows)
    reply = second.messages[1]
    assert RoleEvidenceKind.CORROBORATED_TEXT in kinds(reply)
    assert reply.role_evidence[-1].source_message_id == "1:2"
    assert second.status is CaseStatus.REPLIED
    assert stats.agent_evidence == {"quote_corroboration": 1, "corroborated_text": 1}


def test_keywords_alone_are_weak() -> None:
    (case,), stats = build_cases([SheetRow("1", 1, FORM), SheetRow("1", 2, AGENT)])
    reply = case.messages[1]
    assert kinds(reply) == [RoleEvidenceKind.KEYWORD, RoleEvidenceKind.KEYWORD]
    assert reply.role_strength is RoleEvidenceStrength.WEAK
    assert case.status is CaseStatus.UNDETERMINED
    assert stats.replied_without_strong_evidence == 1


def test_repetition_threshold_is_configurable() -> None:
    rows = [SheetRow(str(n), s, b) for n in (1, 2) for s, b in ((1, FORM), (2, AGENT))]
    default, _ = build_cases(rows)
    assert all(RoleEvidenceKind.REPEATED_TEXT not in kinds(c.messages[1]) for c in default)
    lowered, _ = build_cases(rows, repeated_text_min_threads=2)
    for case in lowered:
        assert case.messages[1].role_evidence[-1].thread_count == 2
        # 重复只是辅助证据，状态仍然是 undetermined
        assert case.messages[1].role_strength is RoleEvidenceStrength.AUXILIARY
        assert case.status is CaseStatus.UNDETERMINED


def test_system_messages_record_their_pattern() -> None:
    bounce = "This is the mail system at host mx.example.test.\n\n【合成データ】The mail system\n"
    (case,), _ = build_cases([SheetRow("1", 1, bounce)])
    assert case.messages[0].role is MessageRole.SYSTEM
    assert kinds(case.messages[0]) == [RoleEvidenceKind.SYSTEM_PATTERN]
    assert case.status is CaseStatus.UNDETERMINED


def test_players_have_no_evidence() -> None:
    (case,), _ = build_cases([SheetRow("1", 1, FORM)])
    assert case.messages[0].role_evidence == []


def test_stats_report_evidence() -> None:
    _, stats = build_cases([SheetRow("1", 1, FORM), SheetRow("1", 2, AGENT)])
    text = "\n".join(stats.lines())
    assert "客服回复的角色证据（按最强的一条）：keyword: 1" in text
    assert "以没有强证据的客服回复结束：1" in text
