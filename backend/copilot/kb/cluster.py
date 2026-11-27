"""把客服回复（`Message.core`）聚成模板的候选（提案 0003 §5、§11；#5）。

1. **能学习的回复**：只用角色证据为强或辅助的客服回复。只有弱证据（问候语、结语）的回复
   不参与，玩家可以在正文开头写出客服的问候语（#18 第 5 项）。
2. **精确匹配**：按规范化后的正文（`text_key`：NFKC、去掉空白）分组。
3. **近似去重**：正文中的数字换成 `#` 后取字符 3-gram，Jaccard 相似度不低于
   `NEAR_DUP_THRESHOLD` 的组合并成一个簇（并查集，结果与输入顺序无关）。这样合并的是只差
   日期、时刻、个别字词的变体。
4. **代表正文**：簇内证据最强的那一级中，回复最多的正文；相同时取最早的。只有“重复出现”证据
   的簇标为 `repeated_only`，代表正文从这些回复中选。
5. 出现在少于 `MIN_TEMPLATE_THREADS` 个线程中的簇不成为模板（只出现一次的回复）。

强证据的簇中可以有只有辅助证据的变体，它们只计入覆盖率，模板的内容（代表正文）只来自强证据
的回复，所以伪造的近似副本不能改变模板内容。
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from copilot.contracts import Case, MessageRole, RoleEvidenceStrength
from copilot.ingest.evidence import text_key
from copilot.ingest.pipeline import thread_order

NEAR_DUP_THRESHOLD = 0.9
"""近似去重的 Jaccard 阈值（数字换成 `#` 后的字符 3-gram）。"""
MIN_TEMPLATE_THREADS = 2
"""一个簇至少出现在这么多个不同线程中才成为模板。只出现一次的回复不单独成为模板。"""
NGRAM = 3

_SPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")


class ClusterEvidence(StrEnum):
    """簇的信任来源（提案 0003 §11）。"""

    STRONG = "strong"
    """至少有一封强证据的回复，代表正文来自强证据的回复。"""
    REPEATED_ONLY = "repeated_only"
    """只有“重复出现”的证据。所有者批准前只出现在审核清单中，不提供给模型，也不展示给客服。"""


@dataclass(frozen=True)
class Reply:
    """一封参与聚类的客服回复。"""

    message_id: str
    thread_id: str
    seq: int
    core: str
    strength: RoleEvidenceStrength

    @property
    def key(self) -> str:
        return text_key(self.core)

    def order(self) -> tuple[tuple[int, int, str, str], int]:
        return thread_order(self.thread_id), self.seq


@dataclass(frozen=True)
class ReplyCluster:
    """一个簇：一组正文相同或近似的客服回复。"""

    evidence: ClusterEvidence
    representative: Reply
    replies: tuple[Reply, ...]
    """簇内全部回复，按线程顺序。"""

    @property
    def thread_count(self) -> int:
        return len({reply.thread_id for reply in self.replies})

    @property
    def variants(self) -> list[str]:
        """簇内不同的正文（按 `text_key` 去重，取每种正文最早的原文），代表正文在最前。"""
        seen = {self.representative.key}
        result = [self.representative.core]
        for reply in self.replies:
            if reply.key not in seen:
                seen.add(reply.key)
                result.append(reply.core)
        return result


@dataclass
class ClusterResult:
    clusters: list[ReplyCluster]
    """成为模板的簇，按回复数从多到少排列，相同时按代表正文的先后。"""
    agent_replies: int = 0
    """输入中有正文的客服回复数。"""
    weak_excluded: int = 0
    """只有弱证据（或没有证据）、不参与聚类的回复数。"""
    below_min_threads: int = 0
    """参与聚类、但所在的簇线程数不足 `MIN_TEMPLATE_THREADS` 的回复数。"""


def masked_key(core: str) -> str:
    """近似比较用的键：NFKC、去掉空白、数字换成 `#`。"""
    return _DIGITS.sub("#", _SPACE.sub("", unicodedata.normalize("NFKC", core)))


def ngrams(core: str) -> frozenset[str]:
    text = masked_key(core)
    if len(text) <= NGRAM:
        return frozenset({text})
    return frozenset(text[i : i + NGRAM] for i in range(len(text) - NGRAM + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def learnable_replies(cases: Iterable[Case]) -> tuple[list[Reply], int, int]:
    """返回（能学习的回复, 有正文的客服回复总数, 因证据弱而排除的回复数）。"""
    replies = []
    total = weak = 0
    for case in cases:
        for message in case.messages:
            if message.role is not MessageRole.AGENT or not text_key(message.core):
                continue
            total += 1
            strength = message.role_strength
            if strength not in {RoleEvidenceStrength.STRONG, RoleEvidenceStrength.AUXILIARY}:
                weak += 1
                continue
            replies.append(
                Reply(
                    message_id=message.id,
                    thread_id=message.thread_id,
                    seq=message.seq,
                    core=message.core,
                    strength=strength,
                )
            )
    replies.sort(key=Reply.order)
    return replies, total, weak


def _union_find(keys: list[str], grams: dict[str, frozenset[str]], threshold: float) -> list[int]:
    parent = list(range(len(keys)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if jaccard(grams[keys[i]], grams[keys[j]]) >= threshold:
                a, b = find(i), find(j)
                if a != b:
                    parent[max(a, b)] = min(a, b)
    return [find(i) for i in range(len(keys))]


def _representative(replies: list[Reply]) -> tuple[ClusterEvidence, Reply]:
    strong = [r for r in replies if r.strength is RoleEvidenceStrength.STRONG]
    evidence = ClusterEvidence.STRONG if strong else ClusterEvidence.REPEATED_ONLY
    pool = strong or replies
    by_key: dict[str, list[Reply]] = defaultdict(list)
    for reply in pool:
        by_key[reply.key].append(reply)
    # 回复最多的正文；相同时取最早出现的（每组已按线程顺序排列）。
    best = min(by_key.values(), key=lambda group: (-len(group), group[0].order()))
    return evidence, best[0]


def cluster_replies(
    cases: Iterable[Case],
    *,
    threshold: float = NEAR_DUP_THRESHOLD,
    min_threads: int = MIN_TEMPLATE_THREADS,
) -> ClusterResult:
    """把 `cases` 中能学习的客服回复聚成簇。`cases` 应当只含 train 线程（D-012）。"""
    replies, total, weak = learnable_replies(cases)
    by_key: dict[str, list[Reply]] = defaultdict(list)
    for reply in replies:
        by_key[reply.key].append(reply)
    # 按每种正文最早出现的位置排列，并查集的根因此是最早的正文，结果与输入顺序无关。
    keys = sorted(by_key, key=lambda k: by_key[k][0].order())
    grams = {key: ngrams(by_key[key][0].core) for key in keys}
    roots = _union_find(keys, grams, threshold)
    merged: dict[int, list[Reply]] = defaultdict(list)
    for key, root in zip(keys, roots, strict=True):
        merged[root].extend(by_key[key])

    clusters = []
    below = 0
    for members in merged.values():
        members.sort(key=Reply.order)
        evidence, representative = _representative(members)
        cluster = ReplyCluster(
            evidence=evidence, representative=representative, replies=tuple(members)
        )
        if cluster.thread_count < min_threads:
            below += len(members)
            continue
        clusters.append(cluster)
    clusters.sort(key=lambda c: (-len(c.replies), c.representative.order()))
    return ClusterResult(
        clusters=clusters, agent_replies=total, weak_excluded=weak, below_min_threads=below
    )


class MatchKind(StrEnum):
    """一段回复正文是怎样对应到簇的。"""

    EXACT = "exact"
    """规范化后（`text_key`）与簇内的某种正文相同。"""
    NEAR = "near"
    """与簇内的某种正文近似（3-gram Jaccard 不低于阈值）。"""


class ClusterIndex:
    """把任意一段回复正文对应到簇：先精确匹配簇内的某种正文，再按近似度匹配。

    用于覆盖率和标准答案标注（#5）。只读，不改变簇。
    """

    def __init__(self, clusters: list[ReplyCluster], threshold: float = NEAR_DUP_THRESHOLD):
        self._init([cluster.variants for cluster in clusters], threshold)

    @classmethod
    def from_variants(
        cls, variants: Sequence[Sequence[str]], threshold: float = NEAR_DUP_THRESHOLD
    ) -> "ClusterIndex":
        """直接用每个簇的各种正文建立索引，例如从模板的来源回复重建。"""
        index = cls.__new__(cls)
        index._init(variants, threshold)
        return index

    def _init(self, variants: Sequence[Sequence[str]], threshold: float) -> None:
        self._threshold = threshold
        self._exact: dict[str, int] = {}
        self._grams: list[tuple[int, frozenset[str]]] = []
        for i, texts in enumerate(variants):
            for variant in texts:
                self._exact.setdefault(text_key(variant), i)
                self._grams.append((i, ngrams(variant)))

    def lookup(self, core: str) -> tuple[int, MatchKind] | None:
        """`core` 所属的簇的下标和对应方式，没有时为 None。

        近似度相同时取下标小的（覆盖率高的）簇。
        """
        exact = self._exact.get(text_key(core))
        if exact is not None:
            return exact, MatchKind.EXACT
        grams = ngrams(core)
        best: tuple[float, int] | None = None
        for i, other in self._grams:
            score = jaccard(grams, other)
            if score >= self._threshold and (best is None or (-score, i) < (-best[0], best[1])):
                best = (score, i)
        return None if best is None else (best[1], MatchKind.NEAR)

    def match(self, core: str) -> int | None:
        """`core` 所属的簇的下标，没有时为 None。"""
        found = self.lookup(core)
        return None if found is None else found[0]
