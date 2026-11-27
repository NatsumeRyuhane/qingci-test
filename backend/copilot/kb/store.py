"""知识库条目的存储：只插入、不改写（D-011，提案 0003 §3、§12）。

- 行以（类型, `id`, `version`）为键。同一个键再次写入相同的内容时什么都不做（重复发布是幂等的），
  写入不同的内容时报 `ImmutableEntryError`：修改要新建版本。数据库中另有禁止 UPDATE 和
  DELETE 的触发器。
- 读取时用契约模型校验每一行；版本选择用契约中的规则（`current_version`、`select_valid`）。
"""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from copilot.contracts import (
    EntryType,
    Fact,
    GlossaryEntry,
    Intent,
    KnowledgeEntry,
    Notice,
    Policy,
    ReplyTemplate,
)
from copilot.contracts.knowledge import current_version, select_valid
from copilot.storage.kb_tables import KnowledgeEntryRow

ENTRY_MODELS: dict[EntryType, type[KnowledgeEntry]] = {
    EntryType.TEMPLATE: ReplyTemplate,
    EntryType.INTENT: Intent,
    EntryType.FACT: Fact,
    EntryType.POLICY: Policy,
    EntryType.GLOSSARY: GlossaryEntry,
    EntryType.NOTICE: Notice,
}


class ImmutableEntryError(ValueError):
    """同一个（类型, id, version）已经有不同的内容。修改条目要新建版本。"""


def entry_type_of(entry: KnowledgeEntry) -> EntryType:
    return next(t for t, model in ENTRY_MODELS.items() if type(entry) is model)


class KnowledgeStore:
    """知识库条目的仓库。业务代码只依赖它，不直接接触 SQLAlchemy（D-010）。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_all(self, entries: Iterable[KnowledgeEntry]) -> tuple[int, int]:
        """在一个事务中写入，返回（新插入的数量, 已存在且相同的数量）。有冲突时整体回滚。"""
        inserted = unchanged = 0
        with Session(self._engine) as session, session.begin():
            for entry in entries:
                kind = entry_type_of(entry)
                key = (kind.value, entry.id, entry.version)
                row = session.get(KnowledgeEntryRow, key)
                if row is None:
                    session.add(
                        KnowledgeEntryRow(
                            entry_type=kind.value,
                            entry_id=entry.id,
                            version=entry.version,
                            payload=entry.model_dump_json(),
                        )
                    )
                    session.flush()
                    inserted += 1
                elif ENTRY_MODELS[kind].model_validate_json(row.payload) == entry:
                    unchanged += 1
                else:
                    raise ImmutableEntryError(
                        f"{kind.value} {entry.id} 的版本 {entry.version} 已经存在且内容不同："
                        "修改条目要新建版本（version 加 1）"
                    )
        return inserted, unchanged

    def entries[E: KnowledgeEntry](self, model: type[E]) -> list[E]:
        """某个类型的全部版本，按（id, version）排序。"""
        kind = next(t for t, m in ENTRY_MODELS.items() if m is model)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(KnowledgeEntryRow)
                .where(KnowledgeEntryRow.entry_type == kind.value)
                .order_by(KnowledgeEntryRow.entry_id, KnowledgeEntryRow.version)
            ).all()
            return [model.model_validate_json(row.payload) for row in rows]

    def latest[E: KnowledgeEntry](self, model: type[E]) -> dict[str, E]:
        """每个条目版本号最大的版本（包括已下线的），按 id 索引。用于决定下一个版本号。"""
        latest: dict[str, E] = {}
        for entry in self.entries(model):
            latest[entry.id] = entry  # entries 按版本升序
        return latest

    def current[E: KnowledgeEntry](self, model: type[E]) -> list[E]:
        """每个条目当前生效的版本（最新版本；已下线的不返回，`current_version`）。"""
        groups: dict[str, list[E]] = {}
        for entry in self.entries(model):
            groups.setdefault(entry.id, []).append(entry)
        return [v for v in (current_version(g) for g in groups.values()) if v is not None]

    def valid_at[E: KnowledgeEntry](
        self, model: type[E], at: datetime, *, known_at: datetime | None = None
    ) -> list[E]:
        """在时间 `at` 适用的条目（`select_valid`）。历史重放时 `known_at` 是模拟的“现在”。"""
        return select_valid(self.entries(model), at, known_at=known_at)
