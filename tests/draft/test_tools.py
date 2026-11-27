"""起草时的只读工具（#6，docs/security.md L6、ATK-50 至 ATK-53）。

**假设模型已被攻破**：下面的调用模拟被注入的模型构造的越权或畸形参数，断言代码在执行前
拒绝它们。SYNTHETIC：全部是合成数据。
"""

import pytest

from copilot.contracts import EntryType, RoleEvidenceKind
from copilot.draft.retrieval import KnowledgeSnapshot, build_view
from copilot.draft.similar import SimilarCaseRetriever
from copilot.draft.tools import ToolBox, ToolRejected
from tests.cases import agent, case, player
from tests.draft.factories import context, dt, fact, notice, term

pytestmark = pytest.mark.anyio

NOW = dt(3, 5, 12)
CURRENT = case(
    "500",
    player("500", 1, "【合成】昨日からジェムが消えました"),
    agent("500", 2, "【合成】回答", RoleEvidenceKind.KEYWORD),
    player("500", 3, "【合成】まだ直りません"),
)
OTHER = case("400", content="【合成】別のお客様のジェムが消えた件の詳しい内容")
VIEW = build_view(
    KnowledgeSnapshot(
        facts=[fact("syn-transfer", "【合成】数据迁移需要先绑定账号。")],
        glossary=[term("syn-gem", "ジェム", "宝石")],
        notices=[notice("syn-outage", start=dt(3, 4, 10), end=dt(3, 4, 12))],
    ),
    context(NOW, dt(3, 5, 9)),
)


def toolbox() -> ToolBox:
    return ToolBox(
        case=CURRENT,
        view=VIEW,
        retriever=SimilarCaseRetriever([OTHER]),
        templates_used=lambda c: ["tpl-syn"],
    )


async def test_read_thread_reads_only_the_current_case() -> None:
    result = await toolbox().run("read_thread", {"seq": "3"})
    assert result.data["body"] == "【合成】まだ直りません"
    assert result.data["evidence"] == "none"


@pytest.mark.parametrize(
    ("tool", "arguments", "reason"),
    [
        # ATK-50：读取其他案件。没有案件 ID 参数，多余的参数被拒绝。
        ("read_thread", {"case_id": "400", "seq": "1"}, "参数不合法"),
        ("read_thread", {"seq": "99"}, "超出范围"),
        # ATK-51：查询“现在”之后。
        ("lookup_events", {"at": "2099-12-31T00:00:00+09:00"}, "超出范围"),
        # ATK-52：超长或畸形参数。
        ("search_facts", {"query": "あ" * 500}, "参数不合法"),
        ("read_thread", {"seq": "1; DROP TABLE"}, "参数不合法"),
        ("glossary", {}, "参数不合法"),
        ("delete_case", {"case_id": "500"}, "没有这个工具"),
    ],
)
async def test_out_of_scope_or_malformed_calls_are_rejected(
    tool: str, arguments: dict[str, str], reason: str
) -> None:
    with pytest.raises(ToolRejected, match=reason):
        await toolbox().run(tool, arguments)


async def test_similar_cases_return_ids_and_templates_not_text() -> None:
    """H-04：相似案件只返回编号和当时用过的模板。"""
    result = await toolbox().run("search_similar_cases", {"query": "ジェムが消えた"})
    assert result.data == [{"case_id": "400", "templates_used": ["tpl-syn"]}]
    assert "別のお客様" not in str(result.data)


async def test_similar_cases_never_include_the_current_case() -> None:
    box = ToolBox(case=CURRENT, view=VIEW, retriever=SimilarCaseRetriever([CURRENT, OTHER]))
    result = await box.run("search_similar_cases", {"query": "ジェムが消えました"})
    assert "500" not in [item["case_id"] for item in result.data]


async def test_facts_events_and_terms_are_citable() -> None:
    facts = await toolbox().run("search_facts", {"query": "数据迁移 绑定"})
    assert facts.entries == {(EntryType.FACT, "syn-transfer", 1)}
    events = await toolbox().run("lookup_events", {"at": "2099-03-04T11:00"})
    assert [e["id"] for e in events.data] == ["syn-outage"]
    assert (EntryType.NOTICE, "syn-outage", 1) in events.entries
    terms = await toolbox().run("glossary", {"term": "ジェム"})
    assert terms.data[0]["zh"] == "宝石"


async def test_lookup_events_does_not_change_the_problem_time() -> None:
    """lookup_events 的结果只作参考：知识视图（以及 template_window 检查）不受影响。"""
    before = VIEW.time_context
    await toolbox().run("lookup_events", {"at": "2099-03-04T11:00"})
    assert VIEW.time_context == before
    assert VIEW.notices == []  # 问题发生时间（3 月 5 日 9:00）不在事件窗口内


async def test_no_retriever_means_no_history() -> None:
    box = ToolBox(case=CURRENT, view=VIEW)
    assert (await box.run("search_similar_cases", {"query": "ジェム"})).data == []
