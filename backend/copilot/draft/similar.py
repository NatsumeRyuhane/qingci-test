"""相似历史案件检索：BM25 + embedding 的混合检索（#6，D-010、D-015）。

- **BM25 用字符 bigram**（D-015），不引入日语分词依赖：对全角半角混用、`?` 字符丢失
  （docs/data.md）的文本也稳健。语义上的相似由 embedding 补充。
- **融合用 RRF**（Reciprocal Rank Fusion）：两路各自排名，`score = Σ 1 / (k + rank)`。
  不需要把 BM25 分数和余弦相似度换算到同一个尺度。没有配置 embedding 时只用 BM25。
- **语料只限“现在”之前的案件，并排除当前案件**（提案 0003 §10）：由 `corpus_before` 按
  线程顺序（线程 ID 即提交顺序）筛选。历史重放（#7）可以传入自己的语料。
- 案件的检索文本是玩家的首次咨询（表单内容优先），去掉不可见字符，截断到
  `MAX_DOC_CHARS`。检索结果只是辅助上下文，正文进入 prompt 时仍然是不可信数据。

Embedding 按（模型, 文本）的哈希缓存在 JSONL 文件中，重复运行不重复调用。
"""

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from copilot.contracts import Case, MessageRole, SimilarCaseRef
from copilot.ingest.pipeline import thread_order
from copilot.llm.embedding import EmbeddingClient
from copilot.llm.prompt import clean_untrusted

MAX_DOC_CHARS = 1000
"""每个案件参与检索的最多字符数。"""
RRF_K = 60
"""RRF 的常数 k（常用值）。越大，排名靠后的结果权重下降越慢。"""
DEFAULT_LIMIT = 5

_DROP = re.compile(r"[\s?？・、。,.!！「」『』（）()【】\[\]:：]+")


def case_text(case: Case) -> str:
    """案件用于检索的文本：表单的咨询内容，没有时取第一封玩家邮件。"""
    if case.form is not None and case.form.content:
        text = case.form.content
    else:
        first = next((m for m in case.messages if m.role is MessageRole.PLAYER), None)
        text = first.body if first is not None else ""
    return clean_untrusted(text).strip()[:MAX_DOC_CHARS]


def bigrams(text: str) -> list[str]:
    """NFKC、小写、去掉空白和常见标点（以及样本中代替丢失字符的 `?`）后的字符 bigram。

    只有一个字符时返回它本身，空文本返回空列表。
    """
    norm = _DROP.sub("", unicodedata.normalize("NFKC", text).lower())
    if len(norm) < 2:
        return [norm] if norm else []
    return [norm[i : i + 2] for i in range(len(norm) - 1)]


class BM25Index:
    """字符 bigram 上的 Okapi BM25。语料规模小（约 3 千个案件），全部在内存中计算。"""

    def __init__(self, docs: dict[str, str], *, k1: float = 1.2, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._tf: dict[str, Counter[str]] = {i: Counter(bigrams(t)) for i, t in docs.items()}
        self._len = {i: sum(tf.values()) for i, tf in self._tf.items()}
        self._avg = (sum(self._len.values()) / len(self._len)) if self._len else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf.values():
            df.update(tf.keys())
        n = len(self._tf)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(
        self, query: str, *, limit: int, exclude: Iterable[str] = ()
    ) -> list[tuple[str, float]]:
        """分数大于 0 的前 `limit` 个文档，按分数降序（同分按线程顺序）。"""
        terms = Counter(bigrams(query))
        skip = set(exclude)
        scores: list[tuple[str, float]] = []
        for doc_id, tf in self._tf.items():
            if doc_id in skip:
                continue
            norm = self._k1 * (1 - self._b + self._b * self._len[doc_id] / (self._avg or 1))
            score = 0.0
            for term, qf in terms.items():
                f = tf.get(term)
                if f:
                    score += qf * self._idf[term] * f * (self._k1 + 1) / (f + norm)
            if score > 0:
                scores.append((doc_id, score))
        scores.sort(key=lambda item: (-item[1], thread_order(item[0])))
        return scores[:limit]


class EmbeddingCache:
    """（模型, 文本）→ 向量的 JSONL 缓存。文件不存在时为空，`save` 原子地写出。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._vectors: dict[str, list[float]] = {}
        self._dirty = False
        if path is not None and path.is_file():
            with path.open(encoding="utf-8") as f:
                for line in f.read().split("\n"):
                    if line.strip():
                        item = json.loads(line)
                        self._vectors[item["key"]] = item["vector"]

    @staticmethod
    def key(model: str, text: str) -> str:
        return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()

    def get(self, key: str) -> list[float] | None:
        return self._vectors.get(key)

    def put(self, key: str, vector: list[float]) -> None:
        self._vectors[key] = vector
        self._dirty = True

    def save(self) -> None:
        if self._path is None or not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".embeddings-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for key in sorted(self._vectors):
                f.write(json.dumps({"key": key, "vector": self._vectors[key]}) + "\n")
        os.replace(tmp, self._path)
        self._dirty = False


async def embed_cached(
    client: EmbeddingClient, cache: EmbeddingCache, texts: Sequence[str]
) -> list[list[float]]:
    """缓存中没有的文本才调用 `client`，结果写回缓存（不落盘，调用方决定何时 `save`）。"""
    keys = [cache.key(client.model_name, t) for t in texts]
    # 按首次出现的顺序去重，调用顺序只取决于输入。
    missing = list(
        dict.fromkeys((k, t) for k, t in zip(keys, texts, strict=True) if cache.get(k) is None)
    )
    if missing:
        vectors = await client.embed([t for _, t in missing])
        for (k, _), vector in zip(missing, vectors, strict=True):
            cache.put(k, vector)
    return [cache.get(k) or [] for k in keys]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na = math.sqrt(math.sumprod(a, a))
    nb = math.sqrt(math.sumprod(b, b))
    return math.sumprod(a, b) / (na * nb) if na and nb else 0.0


def corpus_before(cases: Iterable[Case], current: Case) -> list[Case]:
    """线程顺序在当前案件之前的案件（不含当前案件）：只用“现在”之前提交的线程。"""
    position = thread_order(current.id)
    return [c for c in cases if c.id != current.id and thread_order(c.id) < position]


@dataclass
class SimilarCaseRetriever:
    """在固定语料上做混合检索。`embedder` 为空时只用 BM25。"""

    corpus: Sequence[Case]
    embedder: EmbeddingClient | None = None
    cache: EmbeddingCache | None = None

    def __post_init__(self) -> None:
        self._texts = {c.id: case_text(c) for c in self.corpus if case_text(c)}
        self._bm25 = BM25Index(self._texts)
        self._cache = self.cache or EmbeddingCache()

    async def search(
        self, query: str, *, limit: int = DEFAULT_LIMIT, exclude: Iterable[str] = ()
    ) -> list[SimilarCaseRef]:
        """与 `query` 最相似的案件，RRF 分数降序。"""
        query = clean_untrusted(query).strip()[:MAX_DOC_CHARS]
        skip = set(exclude)
        if not query or not self._texts:
            return []
        pool = max(limit * 4, 20)
        rankings: dict[str, dict[str, int]] = {"BM25": {}, "embedding": {}}
        for rank, (doc_id, _) in enumerate(self._bm25.search(query, limit=pool, exclude=skip), 1):
            rankings["BM25"][doc_id] = rank
        if self.embedder is not None:
            ids = [i for i in self._texts if i not in skip]
            vectors = await embed_cached(
                self.embedder, self._cache, [query, *(self._texts[i] for i in ids)]
            )
            sims = sorted(
                ((i, _cosine(vectors[0], v)) for i, v in zip(ids, vectors[1:], strict=True)),
                key=lambda item: (-item[1], thread_order(item[0])),
            )
            rankings["embedding"] = {i: r for r, (i, _) in enumerate(sims[:pool], 1)}
        fused: dict[str, float] = {}
        for ranks in rankings.values():
            for doc_id, rank in ranks.items():
                fused[doc_id] = fused.get(doc_id, 0.0) + 1 / (RRF_K + rank)
        top = sorted(fused, key=lambda i: (-fused[i], thread_order(i)))[:limit]
        return [
            SimilarCaseRef(
                case_id=doc_id,
                score=round(fused[doc_id], 6),
                reason_zh=" · ".join(
                    f"{name} 第 {ranks[doc_id]} 名"
                    for name, ranks in rankings.items()
                    if doc_id in ranks
                ),
            )
            for doc_id in top
        ]

    async def similar_to(self, case: Case, *, limit: int = DEFAULT_LIMIT) -> list[SimilarCaseRef]:
        """与案件的首次咨询相似的案件，排除案件本身。"""
        return await self.search(case_text(case), limit=limit, exclude={case.id})
