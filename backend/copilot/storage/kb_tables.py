"""知识库条目的表（#5，D-011：行以（类型, id, version）为键，只插入、不改写）。

条目的内容是契约模型（`KnowledgeEntry` 的子类）序列化后的 JSON，校验和含义都以契约为准；
这里只放查询需要的键。迁移中为 SQLite 加了禁止 UPDATE 和 DELETE 的触发器。
"""

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from copilot.storage.db import Base


class KnowledgeEntryRow(Base):
    __tablename__ = "kb_entries"

    entry_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    """条目类型（`EntryType` 的值），例如 `template`、`intent`。"""
    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    """契约模型的 JSON（`model_dump_json`）。"""
