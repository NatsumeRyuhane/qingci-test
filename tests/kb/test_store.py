"""知识库条目的存储（D-011：只插入、不改写）。SYNTHETIC：全部是合成数据。"""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DatabaseError

from copilot.contracts import GlossaryEntry, ReviewStatus
from copilot.ingest.timestamps import JST
from copilot.kb.store import ImmutableEntryError, KnowledgeStore
from copilot.storage import create_engine_for, upgrade_database

T1 = datetime(2099, 1, 1, tzinfo=JST)
T2 = datetime(2099, 2, 1, tzinfo=JST)
T3 = datetime(2099, 3, 1, tzinfo=JST)


def _term(version: int, zh: str = "背包", **extra: object) -> GlossaryEntry:
    fields: dict[str, object] = {
        "id": "backpack",
        "version": version,
        "known_from": T1,
        "recorded_at": T1,
        "origin": "manual",
        "term_ja": "リュック",
        "term_zh": zh,
        **extra,
    }
    return GlossaryEntry.model_validate(fields)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    url = f"sqlite:///{tmp_path / 'kb.db'}"
    upgrade_database(url)
    return create_engine_for(url)


def test_insert_is_idempotent(engine: Engine) -> None:
    store = KnowledgeStore(engine)
    assert store.add_all([_term(1)]) == (1, 0)
    assert store.add_all([_term(1)]) == (0, 1)
    assert store.entries(GlossaryEntry) == [_term(1)]


def test_changing_a_stored_version_is_rejected(engine: Engine) -> None:
    store = KnowledgeStore(engine)
    store.add_all([_term(1)])
    with pytest.raises(ImmutableEntryError, match="新建版本"):
        store.add_all([_term(2, "书包"), _term(1, "书包")])
    assert store.entries(GlossaryEntry) == [_term(1)]  # 整个事务回滚，版本 2 也没有写入


def test_database_rejects_update_and_delete(engine: Engine) -> None:
    KnowledgeStore(engine).add_all([_term(1)])
    for statement in ("UPDATE kb_entries SET payload = '{}'", "DELETE FROM kb_entries"):
        with pytest.raises(DatabaseError, match="只插入"), engine.begin() as connection:
            connection.execute(text(statement))


def test_version_selection(engine: Engine) -> None:
    """最新版本为准；下线后整个条目不再被选中；历史重放只看当时已经知道的版本。"""
    store = KnowledgeStore(engine)
    store.add_all([_term(1), _term(2, "书包", known_from=T2, recorded_at=T2)])
    assert [t.version for t in store.current(GlossaryEntry)] == [2]
    assert [t.version for t in store.valid_at(GlossaryEntry, T3, known_at=T1)] == [1]
    assert store.latest(GlossaryEntry)["backpack"].version == 2

    retired = _term(3, "书包", known_from=T2, recorded_at=T3, review_status=ReviewStatus.RETIRED)
    store.add_all([retired])
    assert store.current(GlossaryEntry) == []
    assert store.latest(GlossaryEntry)["backpack"].version == 3
