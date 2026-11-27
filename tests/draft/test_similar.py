"""相似案件的混合检索（#6）。SYNTHETIC：全部是合成数据。"""

from pathlib import Path

import pytest

from copilot.draft.similar import (
    BM25Index,
    EmbeddingCache,
    SimilarCaseRetriever,
    bigrams,
    case_text,
    corpus_before,
    embed_cached,
)
from copilot.llm import FakeEmbeddingClient
from tests.cases import case, player

pytestmark = pytest.mark.anyio

GACHA = "【合成】ガチャを引いたのにキャラが表示されません"
PURCHASE = "【合成】ジェムを購入したのに反映されていません"
TRANSFER = "【合成】機種変更でデータ引き継ぎができません"


def corpus() -> list:
    return [
        case("101", content=GACHA),
        case("102", content=PURCHASE),
        case("103", content=TRANSFER),
        case("104", player("104", 1, "【合成】ジェム　購入　反映されない？")),
    ]


def test_bigrams_normalize_width_space_and_lost_chars() -> None:
    assert bigrams("ＡＢ c?d") == bigrams("abcd") == ["ab", "bc", "cd"]
    assert bigrams("あ") == ["あ"]
    assert bigrams(" 。") == []


def test_case_text_prefers_form_and_strips_invisible() -> None:
    assert case_text(case("1", content="【合成】本文\u200b")) == "【合成】本文"
    assert case_text(case("2", player("2", 1, "【合成】メール"))) == "【合成】メール"


def test_bm25_ranks_overlap_and_excludes() -> None:
    index = BM25Index({c.id: case_text(c) for c in corpus()})
    hits = index.search("ジェムを購入しても反映されない", limit=3)
    assert {doc for doc, _ in hits[:2]} == {"102", "104"}
    assert all(score > 0 for _, score in hits)
    assert "102" not in [d for d, _ in index.search("ジェム購入", limit=3, exclude={"102"})]
    assert index.search("ｚｚｚ", limit=3) == []


def test_corpus_before_uses_thread_order_and_excludes_current() -> None:
    cases = [*corpus(), case("99", content=GACHA)]
    assert [c.id for c in corpus_before(cases, cases[2])] == ["101", "102", "99"]


async def test_bm25_only_without_embedder() -> None:
    refs = await SimilarCaseRetriever(corpus()).similar_to(case("200", content=PURCHASE), limit=2)
    assert {r.case_id for r in refs} == {"102", "104"}
    assert refs[0].reason_zh == "BM25 第 1 名"
    assert refs[0].score > refs[1].score


async def test_hybrid_fuses_embedding_rank() -> None:
    # 预设向量：查询在语义上只与 103 接近，BM25 上与 103 完全没有重合。
    query = "【合成】スマホを替えたら遊べない"
    vectors = {query: [1.0, 0.0], case_text(corpus()[2]): [1.0, 0.0]}
    for c in corpus():
        vectors.setdefault(case_text(c), [0.0, 1.0])
    retriever = SimilarCaseRetriever(corpus(), embedder=FakeEmbeddingClient(vectors))
    refs = await retriever.search(query, limit=4)
    assert refs[0].case_id == "103"
    assert "embedding 第 1 名" in (refs[0].reason_zh or "")


async def test_current_case_is_excluded() -> None:
    cases = corpus()
    refs = await SimilarCaseRetriever(cases).similar_to(cases[1])
    assert "102" not in [r.case_id for r in refs]


async def test_embedding_cache_avoids_repeat_calls(tmp_path: Path) -> None:
    path = tmp_path / "emb.jsonl"
    fake = FakeEmbeddingClient()
    cache = EmbeddingCache(path)
    first = await embed_cached(fake, cache, ["a", "b", "a"])
    cache.save()
    assert fake.calls == [["a", "b"]]
    reloaded = EmbeddingCache(path)
    again = await embed_cached(fake, reloaded, ["b", "a"])
    assert fake.calls == [["a", "b"]]
    assert again == [first[1], first[0]]


async def test_empty_query_returns_nothing() -> None:
    assert await SimilarCaseRetriever(corpus()).search("\u200b ") == []
    assert await SimilarCaseRetriever([]).search("ジェム") == []


async def test_cache_separates_endpoints_with_same_model(tmp_path: Path) -> None:
    """评审意见（PR #28）：同名模型、不同端点的向量不能混用。"""
    cache = EmbeddingCache(tmp_path / "emb.jsonl")
    first = FakeEmbeddingClient(model_name="same")
    other = FakeEmbeddingClient(model_name="same", namespace="https://other.example/v1|same")
    await embed_cached(first, cache, ["a"])
    await embed_cached(other, cache, ["a"])
    await embed_cached(first, cache, ["a"])
    assert first.calls == [["a"]] and other.calls == [["a"]]
