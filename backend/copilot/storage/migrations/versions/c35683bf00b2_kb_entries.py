"""知识库条目（#5）：行以（类型, id, version）为键，只插入、不改写（D-011）。

Revision ID: c35683bf00b2
Revises:
Create Date: 2026-11-27 10:02:42.099060
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c35683bf00b2"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TRIGGERS = {
    "kb_entries_no_update": "UPDATE",
    "kb_entries_no_delete": "DELETE",
}


def upgrade() -> None:
    op.create_table(
        "kb_entries",
        sa.Column("entry_type", sa.String(length=16), nullable=False),
        sa.Column("entry_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("entry_type", "entry_id", "version", name=op.f("pk_kb_entries")),
    )
    if op.get_bind().dialect.name == "sqlite":
        # 修改条目要新建版本，旧草稿引用的（id, version）的内容保持不变（提案 0003 §3）。
        for name, action in _IMMUTABLE_TRIGGERS.items():
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {action} ON kb_entries "
                f"BEGIN SELECT RAISE(ABORT, 'kb_entries 只插入、不改写'); END"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for name in _IMMUTABLE_TRIGGERS:
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("kb_entries")
