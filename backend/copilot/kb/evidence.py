"""只在 train 线程内重新计算跨线程的角色证据（D-012）。

导入步骤（#4）在所有线程上计算跨线程的依据（与有佐证的回复正文相同、重复出现），所以一封
train 中的回复可能只因为 dev 或 test 线程中的引用佐证而得到强证据。知识库只读 train，信任
信号也不能来自评测集，因此构建前去掉这两种依据，只用 train 线程重新计算。线程内部的依据
（引用佐证、问候语和结语、系统邮件的结构）只依赖本线程，保持不变。

案件本身（`Case.status` 等）不变：这里的结果只供知识库学习使用。
"""

from collections.abc import Iterable

from copilot.contracts import Case, RoleEvidenceKind
from copilot.ingest.evidence import REPEATED_TEXT_MIN_THREADS, add_cross_thread_evidence

CROSS_THREAD_KINDS = frozenset({RoleEvidenceKind.CORROBORATED_TEXT, RoleEvidenceKind.REPEATED_TEXT})
"""需要看其他线程才能得到的依据。"""


def recompute_cross_thread_evidence(
    cases: Iterable[Case], *, min_threads: int = REPEATED_TEXT_MIN_THREADS
) -> list[Case]:
    """去掉跨线程的依据，只在 `cases` 内重新计算。`cases` 应当只含 train 线程。"""
    stripped = [
        case.model_copy(
            update={
                "messages": [
                    message.model_copy(
                        update={
                            "role_evidence": [
                                e for e in message.role_evidence if e.kind not in CROSS_THREAD_KINDS
                            ]
                        }
                    )
                    for message in case.messages
                ]
            }
        )
        for case in cases
    ]
    return add_cross_thread_evidence(stripped, min_threads=min_threads)
