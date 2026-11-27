"""角色证据（Message.role_evidence）的校验规则。数据全部是合成的。"""

from typing import Any

import pytest
from pydantic import ValidationError

from copilot.contracts import Message, RoleEvidence, RoleEvidenceKind, RoleEvidenceStrength
from tests.fixtures import load_items, load_models


def agent_item(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return load_items("message")[1] | {"role_evidence": evidence}


def test_strength_is_the_strongest_evidence() -> None:
    agent = load_models(Message, "message")[1]
    assert agent.role_strength is RoleEvidenceStrength.STRONG
    assert "role_strength" not in agent.model_dump()


@pytest.mark.parametrize(
    ("evidence", "strength"),
    [
        ([], None),
        ([{"kind": "keyword"}], RoleEvidenceStrength.WEAK),
        ([{"kind": "keyword"}, {"kind": "repeated_text", "thread_count": 5}], "auxiliary"),
        ([{"kind": "quote_corroboration", "source_message_id": "SYN-0001:3"}], "strong"),
        ([{"kind": "corroborated_text", "source_message_id": "SYN-0002:2"}], "strong"),
        ([{"kind": "metadata"}], "strong"),
    ],
    ids=["none", "keyword", "repeated", "quote", "corroborated-text", "metadata"],
)
def test_role_strength(evidence: list[dict[str, Any]], strength: str | None) -> None:
    message = Message.model_validate(agent_item(evidence))
    assert message.role_strength == (RoleEvidenceStrength(strength) if strength else None)


def test_repetition_alone_is_not_strong() -> None:
    """玩家可以把同一封伪造回复提交到多个线程，重复本身不能证明角色。"""
    evidence = RoleEvidence(kind=RoleEvidenceKind.REPEATED_TEXT, thread_count=100)
    assert evidence.strength is RoleEvidenceStrength.AUXILIARY


@pytest.mark.parametrize(
    "evidence",
    [
        {"kind": "quote_corroboration"},
        {"kind": "corroborated_text"},
        {"kind": "keyword", "source_message_id": "SYN-0002:2"},
        {"kind": "repeated_text"},
        {"kind": "repeated_text", "thread_count": 1},
        {"kind": "keyword", "thread_count": 3},
    ],
    ids=[
        "quote-without-source",
        "text-without-source",
        "keyword-with-source",
        "repeated-without-count",
        "repeated-once",
        "keyword-with-count",
    ],
)
def test_evidence_fields_must_match_kind(evidence: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RoleEvidence.model_validate(evidence)


def test_quote_corroboration_comes_from_a_later_message_in_the_thread() -> None:
    for source in ["SYN-0001:1", "SYN-0001:2", "SYN-0009:3"]:
        item = agent_item([{"kind": "quote_corroboration", "source_message_id": source}])
        with pytest.raises(ValidationError):
            Message.model_validate(item)


def test_evidence_cannot_point_to_itself() -> None:
    item = agent_item([{"kind": "corroborated_text", "source_message_id": "SYN-0001:2"}])
    with pytest.raises(ValidationError, match="指向自己"):
        Message.model_validate(item)


def test_players_have_no_role_evidence() -> None:
    item = load_items("message")[0] | {"role_evidence": [{"kind": "keyword"}]}
    with pytest.raises(ValidationError, match="不能有这些角色依据"):
        Message.model_validate(item)


def test_system_messages_only_have_system_evidence() -> None:
    system = load_models(Message, "message")[3]
    assert system.role_strength is RoleEvidenceStrength.STRONG
    item = load_items("message")[3] | {"role_evidence": [{"kind": "keyword"}]}
    with pytest.raises(ValidationError, match="不能有这些角色依据"):
        Message.model_validate(item)
    item = agent_item([{"kind": "system_pattern"}])
    with pytest.raises(ValidationError, match="不能有这些角色依据"):
        Message.model_validate(item)
