"""只在 train 线程内重新计算角色证据（D-012）。SYNTHETIC：全部是合成数据。"""

from copilot.contracts import RoleEvidenceKind, RoleEvidenceStrength
from copilot.kb.evidence import recompute_cross_thread_evidence
from tests.cases import agent, case, player

K = RoleEvidenceKind
CORE = "【合成データ】再インストールをお試しください。"


def test_trust_from_an_evaluation_thread_is_dropped() -> None:
    """train 中的回复只因为 test 线程中的引用佐证而有强证据：只看 train 时它不再是强证据。"""
    train_reply = agent("1", 2, CORE, K.KEYWORD, K.CORROBORATED_TEXT, source="9:2")
    (recomputed,) = recompute_cross_thread_evidence([case("1", player("1", 1), train_reply)])
    reply = recomputed.messages[1]
    assert [e.kind for e in reply.role_evidence] == [K.KEYWORD]
    assert reply.role_strength is RoleEvidenceStrength.WEAK


def test_anchor_inside_train_still_counts() -> None:
    anchor = agent("1", 2, CORE, K.KEYWORD, K.QUOTE_CORROBORATION, source="1:3")
    other = agent("2", 2, CORE, K.KEYWORD)
    cases = [
        case("1", player("1", 1), anchor, player("1", 3)),
        case("2", player("2", 1), other),
    ]
    _, second = recompute_cross_thread_evidence(cases)
    reply = second.messages[1]
    assert K.CORROBORATED_TEXT in [e.kind for e in reply.role_evidence]
    assert reply.role_evidence[-1].source_message_id == "1:2"
    assert reply.role_strength is RoleEvidenceStrength.STRONG


def test_repeated_text_counts_only_train_threads() -> None:
    # ingest 时这段正文在 3 个线程中出现（含评测集），train 中只有 2 个：不再是“重复出现”。
    replies = [agent(str(i), 2, CORE, K.KEYWORD, K.REPEATED_TEXT) for i in (1, 2)]
    cases = [case(r.thread_id, player(r.thread_id, 1), r) for r in replies]
    for recomputed in recompute_cross_thread_evidence(cases):
        assert K.REPEATED_TEXT not in [e.kind for e in recomputed.messages[1].role_evidence]
