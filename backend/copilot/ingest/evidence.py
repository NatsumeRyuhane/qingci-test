"""角色证据（`Message.role_evidence`）：判断一封客服回复或系统邮件可信程度的依据（提案 0003 §11）。

前提：输入中没有发件人元数据，角色只能从文本判断。全部是确定性的规则，不调用 LLM：

1. 线程内：固定的问候语、结语（弱）；同一线程中之后的邮件用标明客服发件人的引导行引用了
   这封回复（强，引用佐证）；系统邮件的固定结构。
2. 跨线程：正文与另一封有引用佐证的客服回复相同（强，与同一线程中之前一封客服回复相同时
   不算）；同一正文在至少 `REPEATED_TEXT_MIN_THREADS` 个线程中出现（辅助）。

案件状态只取决于这里的证据，不随知识库的审核状态变化。
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable

from copilot.contracts import (
    Case,
    Message,
    MessageRole,
    RoleEvidence,
    RoleEvidenceKind,
    RoleEvidenceStrength,
)
from copilot.ingest.timestamps import QuoteMatch

REPEATED_TEXT_MIN_THREADS = 3
"""同一正文至少出现在这么多个不同线程中，才记为“重复出现”（辅助证据）。"""

_SPACE = re.compile(r"\s+")


def text_key(core: str) -> str:
    """比较回复正文时用的键：NFKC 规范化并去掉所有空白。"""
    return _SPACE.sub("", unicodedata.normalize("NFKC", core))


def local_evidence(message: Message, matches: Iterable[QuoteMatch]) -> list[RoleEvidence]:
    """只看线程内部就能得到的依据。`matches` 是这个线程中确认的引用（`quote_matches`）。"""
    if message.role is MessageRole.SYSTEM:
        return [RoleEvidence(kind=RoleEvidenceKind.SYSTEM_PATTERN, detail="系统邮件的固定结构")]
    if message.role is not MessageRole.AGENT:
        return []
    evidence = []
    if message.greeting:
        evidence.append(RoleEvidence(kind=RoleEvidenceKind.KEYWORD, detail="问候语"))
    if message.closing:
        evidence.append(RoleEvidence(kind=RoleEvidenceKind.KEYWORD, detail="结语"))
    quoting = sorted(
        m.quoting_seq
        for m in matches
        if m.target_seq == message.seq and m.sender_role is MessageRole.AGENT
    )
    if quoting:
        evidence.append(
            RoleEvidence(
                kind=RoleEvidenceKind.QUOTE_CORROBORATION,
                source_message_id=f"{message.thread_id}:{quoting[0]}",
            )
        )
    return evidence


def _agent_replies(case: Case) -> list[Message]:
    return [m for m in case.messages if m.role is MessageRole.AGENT and text_key(m.core)]


def add_cross_thread_evidence(
    cases: list[Case], *, min_threads: int = REPEATED_TEXT_MIN_THREADS
) -> list[Case]:
    """补充需要看所有线程才能得到的依据，返回新的案件列表（顺序不变）。"""
    threads: dict[str, set[str]] = defaultdict(set)
    anchors: dict[str, list[str]] = defaultdict(list)
    """正文 → 有引用佐证的客服回复（按案件顺序）。"""
    for case in cases:
        for message in _agent_replies(case):
            key = text_key(message.core)
            threads[key].add(case.id)
            if any(e.kind is RoleEvidenceKind.QUOTE_CORROBORATION for e in message.role_evidence):
                anchors[key].append(message.id)

    updated = []
    for case in cases:
        earlier: set[str] = set()
        messages = []
        for message in case.messages:
            key = text_key(message.core) if message.role is MessageRole.AGENT else ""
            if not key:
                messages.append(message)
                continue
            extra = []
            corroborated = message.role_strength is RoleEvidenceStrength.STRONG
            # 与同一线程中之前一封客服回复正文相同：玩家可能把收到的回复粘贴了回来，不算。
            if not corroborated and key not in earlier:
                source = next((a for a in anchors[key] if a != message.id), None)
                if source is not None:
                    extra.append(
                        RoleEvidence(
                            kind=RoleEvidenceKind.CORROBORATED_TEXT, source_message_id=source
                        )
                    )
            if len(threads[key]) >= min_threads:
                extra.append(
                    RoleEvidence(
                        kind=RoleEvidenceKind.REPEATED_TEXT, thread_count=len(threads[key])
                    )
                )
            earlier.add(key)
            messages.append(
                message.model_copy(update={"role_evidence": [*message.role_evidence, *extra]})
                if extra
                else message
            )
        updated.append(case.model_copy(update={"messages": messages}))
    return updated
