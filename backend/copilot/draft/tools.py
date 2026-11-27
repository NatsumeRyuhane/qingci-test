"""起草时模型可以调用的只读工具（#6，提案 0003 §10、§13，docs/security.md L6）。

- `search_facts`：检索游戏事实。只返回已批准、“现在”有效的事实。
- `search_similar_cases`：检索相似历史案件。只限“现在”之前的案件，排除当前案件；**只返回
  编号和当时用过的模板**，不返回别的玩家的正文（docs/security.md H-04）。
- `read_thread`：读取线程中的某封邮件。**只限当前案件**：参数只有 `seq`，没有案件 ID。
- `lookup_events`：查询另一个时间点的公告与事件（例如玩家说“昨天”）。只限“现在”之前的
  时间；结果只作参考，**不改变 `template_window` 检查使用的时间**。
- `glossary`：查询术语对照。只返回已批准的术语。

**假设模型已被攻破：** 每个工具有自己的参数模型（`extra="forbid"`、长度上限），执行前由代码
校验；不合规或越权的调用被拒绝，拒绝的原因作为结果返回给模型并记在轨迹中。工具没有副作用。
工具结果与邮件正文一样是不可信数据，由调用方放进分隔标记。
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import Field, ValidationError

from copilot.contracts import Case, EntryType, TimePoint, TimeStatus
from copilot.contracts.base import Contract
from copilot.draft.retrieval import KnowledgeView
from copilot.draft.similar import BM25Index, SimilarCaseRetriever
from copilot.draft.timectx import WindowMatch, match_window
from copilot.ingest.timestamps import JST

TOOL_NAMES: tuple[str, ...] = (
    "search_facts",
    "search_similar_cases",
    "read_thread",
    "lookup_events",
    "glossary",
)
MAX_RESULTS = 5
MAX_MESSAGE_CHARS = 2000


class SearchArgs(Contract):
    query: str = Field(min_length=1, max_length=200)
    """检索词（日语或中文）。"""


class ReadThreadArgs(Contract):
    seq: int = Field(ge=1, le=10_000)
    """当前案件中邮件的序号。没有案件 ID 参数：只能读当前案件。"""


class LookupEventsArgs(Contract):
    at: datetime
    """要查询的时间（ISO 8601，不带时区时按 JST）。不能晚于“现在”。"""


class GlossaryArgs(Contract):
    term: str = Field(min_length=1, max_length=50)
    """日语或中文术语。"""


ARG_MODELS: dict[str, type[Contract]] = {
    "search_facts": SearchArgs,
    "search_similar_cases": SearchArgs,
    "read_thread": ReadThreadArgs,
    "lookup_events": LookupEventsArgs,
    "glossary": GlossaryArgs,
}


class ToolRejected(ValueError):
    """调用不合规或越权：参数校验失败、超出范围。原因作为结果返回给模型。"""


@dataclass(frozen=True)
class ToolResult:
    """一次调用的结果。`data` 交给模型（不可信数据），`summary_zh` 记在轨迹中。"""

    data: Any
    summary_zh: str
    entries: frozenset[tuple[EntryType, str, int]] = frozenset()
    """结果中出现的知识库条目，可以被草稿引用。"""


@dataclass
class ToolBox:
    """一个案件的工具：范围由构造时传入的当前案件、可见知识和相似案件检索固定。"""

    case: Case
    view: KnowledgeView
    retriever: SimilarCaseRetriever | None = None
    templates_used: Callable[[Case], list[str]] = lambda _case: []
    """相似案件当时用过的模板（`service.TemplateMatcher.templates_used`）。"""
    _facts: BM25Index = field(init=False)

    def __post_init__(self) -> None:
        self._facts = BM25Index(
            {f.id: f"{f.statement_zh}\n{f.statement_ja or ''}" for f in self.view.facts}
        )

    def _now_latest(self) -> datetime:
        bounds = self.view.time_context.now.bounds()
        assert bounds is not None  # “现在”不能是 unavailable（契约保证）
        return bounds[1]

    def validate(self, tool: str, arguments: Mapping[str, str]) -> Contract:
        """校验工具名和参数。不合规时抛出 `ToolRejected`。"""
        model = ARG_MODELS.get(tool)
        if model is None:
            raise ToolRejected(f"没有这个工具：{tool[:40]!r}")
        try:
            args = model.model_validate(dict(arguments))
        except ValidationError as error:
            fields = sorted({".".join(map(str, e["loc"])) or "（整体）" for e in error.errors()})
            raise ToolRejected(f"参数不合法：{', '.join(fields)[:200]}") from None
        if isinstance(args, LookupEventsArgs):
            at = args.at if args.at.tzinfo is not None else args.at.replace(tzinfo=JST)
            if at > self._now_latest():
                raise ToolRejected("超出范围：不能查询“现在”之后的时间")
            args = LookupEventsArgs(at=at)
        return args

    async def run(self, tool: str, arguments: Mapping[str, str]) -> ToolResult:
        """校验并执行。不合规时抛出 `ToolRejected`；其他异常是工具出错，由调用方回退。"""
        args = self.validate(tool, arguments)
        if isinstance(args, ReadThreadArgs):
            return self._read_thread(args)
        if isinstance(args, LookupEventsArgs):
            return self._lookup_events(args)
        if isinstance(args, GlossaryArgs):
            return self._glossary(args)
        assert isinstance(args, SearchArgs)
        if tool == "search_facts":
            return self._search_facts(args)
        return await self._search_similar(args)

    def _search_facts(self, args: SearchArgs) -> ToolResult:
        facts = {f.id: f for f in self.view.facts}
        hits = [facts[i] for i, _ in self._facts.search(args.query, limit=MAX_RESULTS)]
        return ToolResult(
            data=[
                {"type": "fact", "id": f.id, "version": f.version, "statement_zh": f.statement_zh}
                for f in hits
            ],
            summary_zh=f"找到 {len(hits)} 条事实"
            + (f"：{', '.join(f.id for f in hits)}" if hits else ""),
            entries=frozenset((EntryType.FACT, f.id, f.version) for f in hits),
        )

    async def _search_similar(self, args: SearchArgs) -> ToolResult:
        if self.retriever is None:
            return ToolResult(data=[], summary_zh="没有可检索的历史案件")
        corpus = {c.id: c for c in self.retriever.corpus}
        refs = await self.retriever.search(args.query, limit=MAX_RESULTS, exclude={self.case.id})
        data = [
            {"case_id": r.case_id, "templates_used": self.templates_used(corpus[r.case_id])}
            for r in refs
        ]
        return ToolResult(
            data=data,
            summary_zh=f"找到 {len(refs)} 个相似案件"
            + (f"：{', '.join(r.case_id for r in refs)}" if refs else ""),
        )

    def _read_thread(self, args: ReadThreadArgs) -> ToolResult:
        message = next((m for m in self.case.messages if m.seq == args.seq), None)
        if message is None:
            raise ToolRejected(f"超出范围：当前案件中没有 seq {args.seq}")
        return ToolResult(
            data={
                "seq": message.seq,
                "role": message.role.value,
                "evidence": message.role_strength.value if message.role_strength else "none",
                "body": message.body[:MAX_MESSAGE_CHARS],
            },
            summary_zh=f"读取了当前案件的 seq {message.seq}（{message.role.value}）",
        )

    def _lookup_events(self, args: LookupEventsArgs) -> ToolResult:
        at = TimePoint(status=TimeStatus.KNOWN, value=args.at)
        found = [
            n
            for n in self.view.valid_notices
            if match_window(at, n.event_start, n.event_end) is WindowMatch.INSIDE
        ]
        return ToolResult(
            data=[
                {
                    "type": "notice",
                    "id": n.id,
                    "version": n.version,
                    "title_zh": n.title_zh,
                    "window": [
                        n.event_start.isoformat(),
                        n.event_end.isoformat() if n.event_end else None,
                    ],
                    "note": "只作参考，不改变草稿检查使用的问题发生时间",
                }
                for n in found
            ],
            summary_zh=f"{args.at.isoformat()} 时的事件：{len(found)} 条"
            + (f"（{', '.join(n.id for n in found)}）" if found else ""),
            entries=frozenset((EntryType.NOTICE, n.id, n.version) for n in found),
        )

    def _glossary(self, args: GlossaryArgs) -> ToolResult:
        term = args.term.strip()
        hits = [g for g in self.view.glossary if term in g.term_ja or term in g.term_zh][
            :MAX_RESULTS
        ]
        return ToolResult(
            data=[
                {
                    "type": "glossary",
                    "id": g.id,
                    "version": g.version,
                    "ja": g.term_ja,
                    "zh": g.term_zh,
                    "note": g.note_zh,
                }
                for g in hits
            ],
            summary_zh=f"找到 {len(hits)} 个术语",
            entries=frozenset((EntryType.GLOSSARY, g.id, g.version) for g in hits),
        )


def render(data: Any) -> str:
    """把工具结果写成给模型看的 JSON 文本（调用方再放进分隔标记）。"""
    return json.dumps(data, ensure_ascii=False, indent=1)
