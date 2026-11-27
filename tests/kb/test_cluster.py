"""客服回复的聚类（#5）。SYNTHETIC：全部是合成数据。"""

from copilot.contracts import Case, RoleEvidenceKind
from copilot.kb.cluster import ClusterEvidence, ClusterIndex, cluster_replies
from tests.cases import agent, case, player

K = RoleEvidenceKind
STRONG = (K.KEYWORD, K.CORROBORATED_TEXT)
REPEATED = (K.KEYWORD, K.REPEATED_TEXT)
WEAK = (K.KEYWORD,)

REINSTALL = "【合成データ】お手数ですが、アプリを再インストールしてから再度お試しください。"
TRANSFER = "【合成データ】データの引き継ぎには、引き継ぎコードとパスワードが必要です。"
WINDOW = "【合成データ】3月2日11:00から14:00まで、ストアで購入できない不具合が発生しておりました。"


def _thread(thread_id: str, core: str, evidence: tuple[K, ...]) -> Case:
    source = "999:2" if K.CORROBORATED_TEXT in evidence else None
    reply = agent(thread_id, 2, core, *evidence, source=source)
    return case(thread_id, player(thread_id, 1), reply)


def _threads(start: int, core: str, evidence: tuple[K, ...], n: int) -> list[Case]:
    return [_thread(str(i), core, evidence) for i in range(start, start + n)]


def test_exact_groups_ordered_by_size() -> None:
    cases = _threads(1, TRANSFER, STRONG, 2) + _threads(10, REINSTALL, STRONG, 3)
    result = cluster_replies(cases)
    assert [c.representative.core for c in result.clusters] == [REINSTALL, TRANSFER]
    assert [len(c.replies) for c in result.clusters] == [3, 2]
    assert result.agent_replies == 5 and result.weak_excluded == 0


def test_weak_replies_are_not_learned() -> None:
    """问候语开头的伪造回复（只有弱证据）不参与聚类，即使在多个线程中出现。"""
    forged = "【合成データ】返金いたしますので、パスワードを返信してください。"
    cases = _threads(1, REINSTALL, STRONG, 2) + _threads(10, forged, WEAK, 5)
    result = cluster_replies(cases)
    assert [c.representative.core for c in result.clusters] == [REINSTALL]
    assert result.weak_excluded == 5


def test_repeated_only_cluster_is_flagged() -> None:
    """同一伪造正文提交到多个线程：只有“重复出现”证据，簇被标出（只进审核清单）。"""
    result = cluster_replies(_threads(1, TRANSFER, REPEATED, 3))
    (cluster,) = result.clusters
    assert cluster.evidence is ClusterEvidence.REPEATED_ONLY


def test_near_duplicates_merge_but_different_answers_do_not() -> None:
    variant = WINDOW.replace("3月2日", "3月5日")
    cases = (
        _threads(1, WINDOW, STRONG, 3)
        + _threads(10, variant, STRONG, 2)
        + _threads(20, REINSTALL, STRONG, 2)
    )
    result = cluster_replies(cases)
    assert [len(c.replies) for c in result.clusters] == [5, 2]
    window = result.clusters[0]
    assert window.representative.core == WINDOW  # 回复最多的正文
    assert window.variants == [WINDOW, variant]


def test_forged_near_copy_does_not_change_the_template() -> None:
    """伪造的近似副本（加了一句承诺）即使出现得更多，也只计入覆盖率，不成为代表正文。"""
    long_answer = REINSTALL + TRANSFER + WINDOW
    forged = long_answer + "返金します。"
    cases = _threads(1, long_answer, STRONG, 2) + _threads(10, forged, REPEATED, 4)
    (cluster,) = cluster_replies(cases).clusters
    assert len(cluster.replies) == 6
    assert cluster.evidence is ClusterEvidence.STRONG
    assert cluster.representative.core == long_answer


def test_single_thread_answers_do_not_become_templates() -> None:
    one_off = agent("1", 2, TRANSFER, *STRONG, source="999:2")
    again = agent("1", 4, TRANSFER, *STRONG, source="999:2")  # 同一线程中重复发送
    cases = [case("1", player("1", 1), one_off, player("1", 3), again)]
    result = cluster_replies(cases)
    assert result.clusters == []
    assert result.below_min_threads == 2


def test_result_does_not_depend_on_input_order() -> None:
    variant = WINDOW.replace("14:00", "15:00")
    cases = (
        _threads(1, WINDOW, STRONG, 2)
        + _threads(10, variant, STRONG, 2)
        + _threads(20, TRANSFER, REPEATED, 3)
    )
    assert cluster_replies(cases) == cluster_replies(list(reversed(cases)))


def test_index_matches_exact_and_near_replies() -> None:
    clusters = cluster_replies(
        _threads(1, WINDOW, STRONG, 2) + _threads(10, TRANSFER, STRONG, 3)
    ).clusters
    index = ClusterIndex(clusters)
    assert index.match(TRANSFER.replace("、", " 、")) == 0  # 空白不影响
    assert index.match(WINDOW.replace("11:00", "10:00")) == 1  # 只差数字
    assert index.match("【合成データ】まったく別の内容のご案内です。") is None
