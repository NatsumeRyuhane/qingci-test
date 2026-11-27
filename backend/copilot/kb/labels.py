"""标准答案标注（`copilot kb label`，#5 → #7，提案 0003 §6、§8）。

为**所有切分**（train、dev、test）的历史线程生成 #7 评测用的标注。模板只来自 train（D-012），
所以 dev 和 test 的回复对应到的是“当时已有的知识”；对应不上的标为“无匹配”。这些标注是评测的
标准答案，不会进入知识库。

1. **每封客服回复 → 模板**（`ReplyLabel`）：先精确匹配模板来源回复的各种正文，再按近似度匹配
   （`ClusterIndex`），记录模板的（`id`, `version`）和对应方式；对应不上时为空。另记：
   - 角色证据的强度（导入时的 `Message.role_strength`）：#7 的主指标只用 `strong`，`auxiliary`
     和 `weak` 作为单独的切片（D-012）；
   - **解决信号**：之后有玩家邮件 → 未解决；之后没有邮件，或只有客服回复 → 解决；之后没有玩家
     邮件、但有系统或无法判断的邮件 → 未知。这是近似的：玩家的追问可能只是道谢；
   - 它回复的玩家邮件（之前最近的一封玩家邮件）、是否是线程中的第一封客服回复。
2. **“不回复”**（`NoReplyLabel`）：只来自可解释的原因（提案 0003 §8）：
   - **重复提交**：与同组中某个有强证据客服回复的线程是同一个咨询，自己没有客服回复；
   - **系统邮件**：线程中只有系统邮件。
   原因不明的、没有回复的线程不作为“不回复”的标准答案。
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from copilot.contracts import (
    Case,
    CaseId,
    MessageId,
    MessageRole,
    NoReplyReason,
    RoleEvidenceStrength,
)
from copilot.contracts.base import Document
from copilot.contracts.knowledge import EntryId
from copilot.eval import Split, SplitManifest
from copilot.ingest.duplicates import duplicate_groups
from copilot.ingest.evidence import text_key
from copilot.kb.annotate import AnnotatedTemplate
from copilot.kb.cluster import ClusterIndex, MatchKind

LABELS_DIR = "labels"
REPLIES_FILE = "replies.jsonl"
NO_REPLY_FILE = "no_reply.jsonl"


class Resolution(StrEnum):
    """一封客服回复是否解决了问题（近似，见模块说明）。"""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    UNKNOWN = "unknown"


class ReplyLabel(Document):
    """一封历史客服回复的标准答案。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    message_id: MessageId
    split: Split
    evidence: RoleEvidenceStrength | None
    """导入时的角色证据强度。#7 的主指标只用 `strong`（D-012）。"""
    template_id: EntryId | None = None
    """对应的模板；无匹配时为空。"""
    template_version: int | None = Field(default=None, ge=1)
    match: MatchKind | None = None
    resolution: Resolution
    inquiry_message_id: MessageId | None = None
    """它回复的玩家邮件：之前最近的一封玩家邮件。没有时为空。"""
    first_agent_reply: bool
    """是否是线程中的第一封客服回复（自动发送的门槛只适用于第一封，提案 0003 §5）。"""

    @model_validator(mode="after")
    def _check_match(self) -> Self:
        given = [self.template_id is not None, self.template_version is not None]
        if any(given) != all(given) or any(given) != (self.match is not None):
            raise ValueError("template_id、template_version 和 match 必须同时有或同时没有")
        return self


class NoReplyLabel(Document):
    """一个“不回复”的标准答案。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    thread_id: CaseId
    split: Split
    reason: NoReplyReason
    """只有 `duplicate` 和 `system_message`。"""
    duplicate_of: CaseId | None = None
    """重复提交时，对应的、有强证据客服回复的线程。"""

    @model_validator(mode="after")
    def _check_reason(self) -> Self:
        if self.reason is NoReplyReason.OTHER:
            raise ValueError("原因不明的线程不作为“不回复”的标准答案")
        if (self.reason is NoReplyReason.DUPLICATE) != (self.duplicate_of is not None):
            raise ValueError("只有重复提交需要 duplicate_of")
        return self


def resolution(case: Case, index: int) -> Resolution:
    """`case.messages[index]`（一封客服回复）的解决信号。"""
    later = case.messages[index + 1 :]
    if any(m.role is MessageRole.PLAYER for m in later):
        return Resolution.UNRESOLVED
    if any(m.role in {MessageRole.SYSTEM, MessageRole.UNKNOWN} for m in later):
        return Resolution.UNKNOWN
    return Resolution.RESOLVED


def template_index(
    templates: Sequence[AnnotatedTemplate], train_cases: Iterable[Case]
) -> ClusterIndex:
    """用每个模板的来源回复（train）的各种正文建立索引，下标与 `templates` 一致。"""
    messages = {m.id: m for case in train_cases for m in case.messages}
    variants = []
    for item in templates:
        texts: dict[str, str] = {}
        for message_id in item.template.source_message_ids:
            message = messages.get(message_id)
            if message is None:
                raise ValueError(
                    f"模板 {item.template.id} 的来源 {message_id} 不在 train 中"
                    "（先重新运行 copilot kb build 和 copilot kb annotate）"
                )
            texts.setdefault(text_key(message.core), message.core)
        variants.append(list(texts.values()))
    return ClusterIndex.from_variants(variants)


def label_replies(
    cases: Iterable[Case],
    manifest: SplitManifest,
    templates: Sequence[AnnotatedTemplate],
) -> list[ReplyLabel]:
    """所有切分中每封有正文的客服回复的标准答案，按线程顺序。"""
    cases = list(cases)
    index = template_index(templates, manifest.select(cases, Split.TRAIN))
    labels = []
    for case in cases:
        split = manifest.assignments[case.id]
        seen_agent = False
        last_player: str | None = None
        for i, message in enumerate(case.messages):
            if message.role is MessageRole.PLAYER:
                last_player = message.id
                continue
            if message.role is not MessageRole.AGENT or not text_key(message.core):
                continue
            found = index.lookup(message.core)
            template = templates[found[0]].template if found else None
            labels.append(
                ReplyLabel(
                    message_id=message.id,
                    split=split,
                    evidence=message.role_strength,
                    template_id=template.id if template else None,
                    template_version=template.version if template else None,
                    match=found[1] if found else None,
                    resolution=resolution(case, i),
                    inquiry_message_id=last_player,
                    first_agent_reply=not seen_agent,
                )
            )
            seen_agent = True
    return labels


def _has_strong_reply(case: Case) -> bool:
    return any(
        m.role is MessageRole.AGENT and m.role_strength is RoleEvidenceStrength.STRONG
        for m in case.messages
    )


def label_no_reply(cases: Iterable[Case], manifest: SplitManifest) -> list[NoReplyLabel]:
    """“不回复”的标准答案，按线程 ID 排序。"""
    cases = list(cases)
    manifest.select(cases, Split.TRAIN)  # 检查切分与案件一致
    by_id = {case.id: case for case in cases}
    labels: dict[str, NoReplyLabel] = {}
    for case in cases:
        if all(m.role is MessageRole.SYSTEM for m in case.messages):
            labels[case.id] = NoReplyLabel(
                thread_id=case.id,
                split=manifest.assignments[case.id],
                reason=NoReplyReason.SYSTEM_MESSAGE,
            )
    for group in duplicate_groups(cases):
        replied = [tid for tid in group if _has_strong_reply(by_id[tid])]
        if not replied:
            continue  # 彼此重复、但没有一个得到可信的回复：原因不明
        for thread_id in group:
            case = by_id[thread_id]
            if any(m.role is MessageRole.AGENT for m in case.messages):
                continue
            labels[thread_id] = NoReplyLabel(
                thread_id=thread_id,
                split=manifest.assignments[thread_id],
                reason=NoReplyReason.DUPLICATE,
                duplicate_of=replied[0],
            )
    position = {case.id: i for i, case in enumerate(cases)}
    return [labels[tid] for tid in sorted(labels, key=position.__getitem__)]


@dataclass
class LabelReport:
    """标注统计，只含汇总数字。"""

    replies: dict[Split, Counter[str]] = field(
        default_factory=lambda: {s: Counter() for s in Split}
    )
    no_reply: dict[Split, Counter[str]] = field(
        default_factory=lambda: {s: Counter() for s in Split}
    )

    def lines(self) -> list[str]:
        lines = []
        for split in Split:
            c = self.replies[split]
            parts = []
            for tier in ("strong", "auxiliary", "weak"):
                total = c[f"{tier}:total"]
                if total:
                    matched = c[f"{tier}:exact"] + c[f"{tier}:near"]
                    parts.append(
                        f"{tier} {total}（对应到模板 {matched / total:.1%}："
                        f"精确 {c[f'{tier}:exact']} · 近似 {c[f'{tier}:near']}）"
                    )
            resolutions = " · ".join(f"{r.value} {c[f'resolution:{r.value}']}" for r in Resolution)
            n = self.no_reply[split]
            lines += [
                f"{split.value}：客服回复 {c['total']} — {' · '.join(parts) or '无'}",
                f"  解决信号：{resolutions} · 第一封回复：{c['first']}",
                f"  不回复：重复提交 {n['duplicate']} · 系统邮件 {n['system_message']}",
            ]
        return lines


def summarize(replies: Iterable[ReplyLabel], no_reply: Iterable[NoReplyLabel]) -> LabelReport:
    report = LabelReport()
    for label in replies:
        c = report.replies[label.split]
        tier = label.evidence.value if label.evidence else "weak"
        c["total"] += 1
        c[f"{tier}:total"] += 1
        if label.match:
            c[f"{tier}:{label.match.value}"] += 1
        c[f"resolution:{label.resolution.value}"] += 1
        c["first"] += label.first_agent_reply
    for item in no_reply:
        report.no_reply[item.split][item.reason.value] += 1
    return report


def write_jsonl(items: Iterable[Document], path: Path) -> None:
    """写成 JSONL，先写临时文件再替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for item in items:
            f.write(item.model_dump_json())
            f.write("\n")
    tmp.replace(path)


def read_reply_labels(path: Path) -> list[ReplyLabel]:
    with path.open(encoding="utf-8") as f:
        return [ReplyLabel.model_validate_json(line) for line in f if line.strip()]


def read_no_reply_labels(path: Path) -> list[NoReplyLabel]:
    with path.open(encoding="utf-8") as f:
        return [NoReplyLabel.model_validate_json(line) for line in f if line.strip()]
