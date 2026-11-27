"""读取并校验 `knowledge/` 中人工编写的知识（提案 0003 §2、§12）。

`knowledge/` 中的内容不含客户原文，可以提交：意图目录、政策、公告与事件、术语表（各自是契约
类型）和风格指南（固定内容，不做版本和有效期）。每个文件的格式：

```yaml
defaults:          # 可选：合并到每个条目中，条目中的字段优先
  version: 1
  origin: manual
entries:
  - id: ...
```

校验：每个条目符合契约；同一类型中（`id`, `version`）不重复；政策和公告引用的意图存在于意图
目录中；风格指南的问候语和结语与导入步骤识别的固定措辞一致。

`find_leaks` 检查提交的日语文本中是否有一段与玩家写的内容相同（提案 0003 “风险”：提交的人工
内容意外混入客户原文）。
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from copilot.contracts import (
    Case,
    GlossaryEntry,
    Intent,
    KnowledgeEntry,
    MessageRole,
    Notice,
    Policy,
)
from copilot.contracts.base import Contract, Document
from copilot.ingest.text import CLOSING_MARK, GREETING_MARK, NOTICE_MARK

KNOWLEDGE_DIR = Path("knowledge")
"""人工编写的知识所在的目录（仓库根目录，提交到 git）。"""

ENTRY_FILES: dict[str, type[KnowledgeEntry]] = {
    "intents.yaml": Intent,
    "policies.yaml": Policy,
    "notices.yaml": Notice,
    "glossary.yaml": GlossaryEntry,
}
STYLE_GUIDE_FILE = "style_guide.yaml"
LEAK_MIN_CHARS = 20
"""提交的日语文本中，与玩家写的内容相同的一段至少这么长才报告。"""


class KnowledgeError(ValueError):
    """`knowledge/` 中的内容不合规。消息中写明文件和条目。"""


class StylePhrase(Contract):
    """风格指南中的一句固定措辞。"""

    ja: str = Field(min_length=1)
    """日语措辞，原样使用。"""
    zh: str = Field(min_length=1)
    """中文说明：这句话的作用、什么时候用。"""


class StyleGuide(Document):
    """回复的固定结构：问候 →（致歉）→ 答复 →（请再次联系）→ 结尾 → 禁止转载声明
    （docs/data.md 关键发现 3）。样本中这些措辞是固定的，所以不做版本和有效期（D-011）。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    greeting: StylePhrase
    """开头的固定问候语。"""
    apologies: list[StylePhrase] = Field(min_length=1)
    """各种致歉，按使用场景区分。"""
    contact_again: list[StylePhrase] = Field(min_length=1)
    """“如果问题仍未解决，请再次联系我们”一类的句子。"""
    closing: StylePhrase
    """结尾的固定结语。"""
    notice: StylePhrase
    """末尾的禁止转载声明。"""


@dataclass(frozen=True)
class Knowledge:
    intents: list[Intent]
    policies: list[Policy]
    notices: list[Notice]
    glossary: list[GlossaryEntry]
    style_guide: StyleGuide

    def entries(self) -> list[KnowledgeEntry]:
        return [*self.intents, *self.policies, *self.notices, *self.glossary]


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KnowledgeError(f"{path}：文件不存在") from exc
    except yaml.YAMLError as exc:
        raise KnowledgeError(f"{path}：YAML 格式错误：{exc}") from exc


def _validate[M: BaseModel](model: type[M], data: Any, where: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or '（整体）'}：{e['msg']}" for e in exc.errors()
        )
        raise KnowledgeError(f"{where}：{errors}") from exc


def _load_entries[E: KnowledgeEntry](path: Path, model: type[E]) -> list[E]:
    data = _read_yaml(path)
    if not isinstance(data, dict) or not set(data) <= {"defaults", "entries"}:
        raise KnowledgeError(f"{path}：顶层只能有 defaults 和 entries")
    defaults = data.get("defaults") or {}
    raw = data.get("entries") or []
    if not isinstance(defaults, dict) or not isinstance(raw, list):
        raise KnowledgeError(f"{path}：defaults 必须是映射，entries 必须是列表")
    entries = []
    seen: set[tuple[str, int]] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise KnowledgeError(f"{path} 第 {i + 1} 个条目：必须是映射")
        where = f"{path} 中的 {item.get('id', f'第 {i + 1} 个条目')}"
        entry = _validate(model, {**defaults, **item}, where)
        key = (entry.id, entry.version)
        if key in seen:
            raise KnowledgeError(f"{where}：（id, version）重复：{key}")
        seen.add(key)
        entries.append(entry)
    return entries


def _check_intent_refs(knowledge: Knowledge, directory: Path) -> None:
    intent_ids = {intent.id for intent in knowledge.intents}
    for name, entries in (
        ("policies.yaml", knowledge.policies),
        ("notices.yaml", knowledge.notices),
    ):
        for entry in entries:
            missing = sorted(set(entry.intents) - intent_ids)
            if missing:
                raise KnowledgeError(
                    f"{directory / name} 中的 {entry.id}：引用了不存在的意图 {missing}"
                )


def _check_style_guide(guide: StyleGuide, path: Path) -> None:
    """风格指南必须与导入步骤识别客服回复时用的固定措辞一致（`copilot.ingest.text`）。"""
    for field_name, mark in (
        ("greeting", GREETING_MARK),
        ("closing", CLOSING_MARK),
        ("notice", NOTICE_MARK),
    ):
        phrase: StylePhrase = getattr(guide, field_name)
        if mark not in phrase.ja:
            raise KnowledgeError(f"{path} 的 {field_name}：必须包含导入步骤使用的固定措辞 {mark!r}")


def load_knowledge(directory: Path = KNOWLEDGE_DIR) -> Knowledge:
    """读取并校验 `directory` 中的全部知识。有任何问题时报 `KnowledgeError`。"""
    loaded: dict[str, list[Any]] = {
        name: _load_entries(directory / name, model) for name, model in ENTRY_FILES.items()
    }
    style_path = directory / STYLE_GUIDE_FILE
    style_guide = _validate(StyleGuide, _read_yaml(style_path), str(style_path))
    _check_style_guide(style_guide, style_path)
    knowledge = Knowledge(
        intents=loaded["intents.yaml"],
        policies=loaded["policies.yaml"],
        notices=loaded["notices.yaml"],
        glossary=loaded["glossary.yaml"],
        style_guide=style_guide,
    )
    _check_intent_refs(knowledge, directory)
    return knowledge


_SPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _SPACE.sub("", unicodedata.normalize("NFKC", text))


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


class LeakKind(StrEnum):
    PLAYER_TEXT = "player_text"
    """与玩家写的内容相同，并且不出现在任何客服回复中：客户原文。"""
    AGENT_REPLY = "agent_reply"
    """与客服回复的内容相同：模板原文属于派生数据，不能提交（风格指南除外）。"""


@dataclass(frozen=True)
class Leak:
    """提交的知识中与样本相同的一段。只记录位置，不记录内容（内容可能是客户原文）。"""

    source: str
    """所在的条目，例如 `intents.yaml:purchase_not_reflected` 或 `style_guide.yaml`。"""
    kind: LeakKind
    threads: int
    """有多少个线程中含有它。"""


def find_leaks(
    knowledge: Knowledge, cases: Iterable[Case], *, min_chars: int = LEAK_MIN_CHARS
) -> list[Leak]:
    """`knowledge` 的文本中，有连续 `min_chars` 个字符（NFKC、去空白后）与样本相同的条目：

    - 与玩家邮件或表单内容相同、并且不出现在任何客服回复中 → 客户原文（`player_text`）；
    - 与客服回复相同 → 模板原文（`agent_reply`）。风格指南本来就是客服的固定措辞，不报告这一种。

    玩家把收到的回复不加引用标记地粘贴回来时，客服的措辞会出现在玩家邮件中；因为它也出现在
    客服回复中，所以不算客户原文。
    """
    segments: dict[str, set[str]] = {}
    named = [
        *((f"{_file_of(entry)}:{entry.id}", entry.model_dump()) for entry in knowledge.entries()),
        (STYLE_GUIDE_FILE, knowledge.style_guide.model_dump()),
    ]
    for source, dumped in named:
        for text in _strings(dumped):
            norm = _norm(text)
            for i in range(len(norm) - min_chars + 1):
                segments.setdefault(norm[i : i + min_chars], set()).add(source)

    in_player: dict[str, set[str]] = {}
    in_agent: dict[str, set[str]] = {}
    for case in cases:
        for message in case.messages:
            if message.role is MessageRole.PLAYER:
                _scan(message.body, case.id, segments, in_player, min_chars)
            elif message.role is MessageRole.AGENT:
                _scan(message.body, case.id, segments, in_agent, min_chars)
        if case.form and case.form.content:
            _scan(case.form.content, case.id, segments, in_player, min_chars)

    hits: dict[tuple[str, LeakKind], set[str]] = {}
    for segment, sources in segments.items():
        for source in sources:
            if segment in in_player and segment not in in_agent:
                hits.setdefault((source, LeakKind.PLAYER_TEXT), set()).update(in_player[segment])
            if segment in in_agent and source != STYLE_GUIDE_FILE:
                hits.setdefault((source, LeakKind.AGENT_REPLY), set()).update(in_agent[segment])
    return [
        Leak(source=source, kind=kind, threads=len(threads))
        for (source, kind), threads in sorted(hits.items())
    ]


def _scan(
    text: str,
    case_id: str,
    segments: dict[str, set[str]],
    found: dict[str, set[str]],
    min_chars: int,
) -> None:
    norm = _norm(text)
    for i in range(len(norm) - min_chars + 1):
        segment = norm[i : i + min_chars]
        if segment in segments:
            found.setdefault(segment, set()).add(case_id)


def _file_of(entry: KnowledgeEntry) -> str:
    return next(name for name, model in ENTRY_FILES.items() if isinstance(entry, model))
