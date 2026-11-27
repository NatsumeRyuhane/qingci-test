"""导入与清洗样本邮件（#4）：表格 → 清洗后的 `Case`（JSONL，写在 data/interim/）。

规则与依据见各模块的说明和 docs/data.md。入口是 `copilot ingest`。
"""

from copilot.ingest.pipeline import (
    DEFAULT_OUTPUT,
    IngestStats,
    build_cases,
    read_cases,
    thread_order,
    write_cases,
)
from copilot.ingest.sheet import SheetFormatError, SheetRow, read_sheet

__all__ = [
    "DEFAULT_OUTPUT",
    "IngestStats",
    "SheetFormatError",
    "SheetRow",
    "build_cases",
    "read_cases",
    "read_sheet",
    "thread_order",
    "write_cases",
]
