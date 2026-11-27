"""评测（#7）。目前只有数据切分（`copilot split`），知识库构建（#5）也依赖它。"""

from copilot.eval.split import (
    DEFAULT_MANIFEST,
    Split,
    SplitManifest,
    SplitMethod,
    StaleSplitError,
    read_manifest,
    split_cases,
    split_stats,
    write_manifest,
)

__all__ = [
    "DEFAULT_MANIFEST",
    "Split",
    "SplitManifest",
    "SplitMethod",
    "StaleSplitError",
    "read_manifest",
    "split_cases",
    "split_stats",
    "write_manifest",
]
