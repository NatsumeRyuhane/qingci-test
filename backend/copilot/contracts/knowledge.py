"""知识库条目的公共字段与版本选择规则（提案 0003 §3、§4）。

- （`id`, `version`）是**编辑历史**：每次修改都新建一个版本，行不可改写。
- 有效期 `[valid_from, valid_to)` 是**现实中的适用范围**。
- 每个条目以**最新的版本**为准，新版本完整取代旧版本（包括有效期）。最新版本为 `retired`
  时整个条目下线。旧版本不会因为新版本的有效期结束或下线而重新被选中。
- 现实中的变化（例如政策变化）用一个新条目表示，记录 `supersedes`；旧条目新建一个关闭
  有效期的版本。
- `known_from` 是运营方从何时起知道这条知识；历史重放只看当时已经知道的版本。
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from copilot.contracts.base import Document
from copilot.contracts.case import MessageId
from copilot.contracts.enums import EntryOrigin, ReviewStatus

EntryId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
"""知识库条目的 ID：小写字母或数字开头，只含小写字母、数字、`_` 和 `-`，最长 64 个字符。
跨版本不变。"""


class KnowledgeEntry(Document):
    """知识库条目（模板、事实、政策、术语表条目、意图、公告与事件）的公共字段。

    子类声明自己的 `schema_version`。
    """

    id: EntryId
    """条目 ID，跨版本不变。"""
    version: int = Field(ge=1)
    """版本，从 1 开始。每次修改都新建一个版本（编辑历史）。"""
    valid_from: AwareDatetime | None = None
    """有效期的开始（包含）。为空表示没有下限。"""
    valid_to: AwareDatetime | None = None
    """有效期的结束（不包含）。为空表示在被替换前一直有效。"""
    known_from: AwareDatetime
    """运营方从何时起知道这个版本的内容。生产中等于录入时间；从历史数据归纳的条目取得出
    它所需的证据中最晚的时间（估计时取区间上界）；只改措辞的版本沿用上一版本的值。
    历史重放只看 `known_from` 不晚于模拟的“现在”的版本。"""
    recorded_at: AwareDatetime
    """这个版本实际录入的时间，供审计。不用于历史重放。"""
    origin: EntryOrigin
    """来源方式：从数据中归纳，或人工录入。"""
    review_status: ReviewStatus = ReviewStatus.DRAFT
    """审核状态。只有 `approved` 的条目会提供给模型（见 `model_visible`）。"""
    source_message_ids: list[MessageId] = Field(default_factory=list)
    """归纳出这个条目的历史邮件，格式为 `<thread_id>:<seq>`。人工录入的条目可以为空。"""
    supersedes: list[EntryId] = Field(default_factory=list)
    """这个条目取代的旧条目 ID（现实中的变化，例如政策变化）。只作记录，不会自动让旧条目
    失效：旧条目需要新建一个关闭有效期的版本。"""

    @property
    def model_visible(self) -> bool:
        """是否可以提供给模型。只有已批准的条目可以（提案 0003 §4）。不参与序列化。"""
        return self.review_status is ReviewStatus.APPROVED

    def is_valid_at(self, at: datetime) -> bool:
        """有效期是否包含时间 `at`。"""
        if self.valid_from is not None and at < self.valid_from:
            return False
        return self.valid_to is None or at < self.valid_to

    @model_validator(mode="after")
    def _check_entry(self) -> Self:
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise ValueError("valid_from 必须早于 valid_to")
        if self.known_from > self.recorded_at:
            raise ValueError("known_from 不能晚于 recorded_at：录入时一定已经知道")
        if self.origin is EntryOrigin.RECONSTRUCTED and not self.source_message_ids:
            raise ValueError("从数据中归纳的条目必须列出 source_message_ids")
        if self.id in self.supersedes:
            raise ValueError("条目不能取代自己（supersedes 中有自己的 id）")
        return self


def current_version[E: KnowledgeEntry](
    versions: Iterable[E], *, known_at: datetime | None = None
) -> E | None:
    """同一个条目的所有版本中，当前生效的那一个。

    取 `version` 最大的版本；`known_at` 不为空时（历史重放），只看 `known_from` 不晚于
    `known_at` 的版本。最新版本为 `retired` 时返回 None。不检查有效期，见 `select_valid`。
    """
    versions = list(versions)
    ids = {(type(v), v.id) for v in versions}
    if len(ids) > 1:
        raise ValueError(f"只能比较同一个条目的版本，实际有 {sorted(i for _, i in ids)}")
    numbers = [v.version for v in versions]
    if len(numbers) != len(set(numbers)):
        raise ValueError(f"版本号重复：{sorted(numbers)}")
    known = [v for v in versions if known_at is None or v.known_from <= known_at]
    if not known:
        return None
    latest = max(known, key=lambda v: v.version)
    return None if latest.review_status is ReviewStatus.RETIRED else latest


def select_valid[E: KnowledgeEntry](
    entries: Iterable[E], at: datetime, *, known_at: datetime | None = None
) -> list[E]:
    """在时间 `at` 适用的条目：每个条目取当前版本（`current_version`），再看它的有效期是否
    包含 `at`。按（类型名, `id`）排序。

    生产中 `known_at` 为空、`at` 为“现在”；历史重放时 `known_at` 是模拟的“现在”，`at` 是
    “现在”或问题发生时间。是否提供给模型另看 `model_visible`。
    """
    groups: dict[tuple[str, str], list[E]] = defaultdict(list)
    for entry in entries:
        # 不同类型的条目可以用相同的 ID，所以按（类型, ID）分组。
        groups[type(entry).__name__, entry.id].append(entry)
    selected = []
    for key in sorted(groups):
        current = current_version(groups[key], known_at=known_at)
        if current is not None and current.is_valid_at(at):
            selected.append(current)
    return selected
