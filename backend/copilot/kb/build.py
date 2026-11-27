"""构建模板候选（`copilot kb build`，#5）。

输入：导入结果（`Case`）和切分（`SplitManifest`）。只读 train 线程（D-012）：

1. 只在 train 线程内重新计算跨线程的角色证据（`recompute_cross_thread_evidence`）；
2. 聚类（`cluster_replies`）；
3. 识别槽位与特定内容（`detect_slots`）；
4. 分配稳定的模板 ID（`assign_ids`），与上一次的候选对应。

输出 `data/processed/kb/candidates.jsonl`（被 git 忽略：含有客服回复原文，D-002），按覆盖率
从高到低排列，即所有者的审核顺序。候选还不是 `ReplyTemplate`：意图、中文说明和风险由之后的
标注步骤补上，所有候选都要经过审核。
"""

import os
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field

from copilot.contracts import (
    PLACEHOLDER_PATTERN,
    Case,
    MessageId,
    MessageRole,
    RoleEvidenceStrength,
    TemplateSlot,
)
from copilot.contracts.base import Contract, Document
from copilot.contracts.knowledge import EntryId
from copilot.eval import Split, SplitManifest
from copilot.ingest.evidence import text_key
from copilot.kb.cleanup import drop_trailing_closing, unwrap_lines
from copilot.kb.cluster import ClusterEvidence, ClusterIndex, ReplyCluster, cluster_replies
from copilot.kb.evidence import recompute_cross_thread_evidence
from copilot.kb.ids import IdentityInput, PreviousIdentity, assign_ids
from copilot.kb.slots import SpanKind, detect_slots

DEFAULT_KB_DIR = Path("data/processed/kb")
"""知识库构建结果的默认目录（被 git 忽略）。"""
CANDIDATES_FILE = "candidates.jsonl"


class SpecificSpan(Contract):
    """代表正文中没有变化的日期、时刻或时间窗口，通常是某次事故的固定时间。"""

    kind: SpanKind
    text: str


class TemplateCandidate(Document):
    """一个模板候选：一个簇的代表正文、槽位和出处。供标注和审核使用，不直接提供给模型。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    id: EntryId
    """稳定的模板 ID，见 `copilot.kb.ids`。"""
    rank: int = Field(ge=1)
    """按覆盖率排列的名次，即审核顺序。"""
    evidence: ClusterEvidence
    """`repeated_only` 的候选在所有者批准前只出现在审核清单中，不提供给模型，也不展示给客服。"""
    representative_message_id: MessageId
    """代表正文取自的那封客服回复。"""
    representative_key: str
    """代表正文的比较键（`text_key`），用于下一次构建时对应 ID。"""
    body_ja: str
    """代表正文，槽位替换为 `{{name}}`。"""
    slots: list[TemplateSlot] = Field(default_factory=list)
    """自动识别的槽位。"""
    specific_spans: list[SpecificSpan] = Field(default_factory=list)
    """没有变化的日期、时刻和时间窗口，供判断是否限定事故期间、是否是通用模板。"""
    source_message_ids: list[MessageId]
    """簇内的全部客服回复（train），按线程顺序。"""
    thread_count: int = Field(ge=1)
    """来源回复所在的不同线程数。"""
    variant_count: int = Field(ge=1)
    """簇内不同的正文数（含代表正文）。"""
    categories: dict[str, int] = Field(default_factory=dict)
    """来源线程的咨询类别（表单）及线程数，供标注意图时参考。"""
    coverage: float
    """来源回复占 train 中全部客服回复的比例。"""
    cumulative_coverage: float
    """按名次累计的覆盖率。"""


@dataclass
class BuildReport:
    """构建统计，只含汇总数字，可以写进 PR（AGENTS.md 第 7 节）。"""

    train_threads: int = 0
    agent_replies: int = 0
    learnable: Counter[str] = field(default_factory=Counter)
    weak_excluded: int = 0
    below_min_threads: int = 0
    unsafe_bodies: int = 0
    candidates: Counter[str] = field(default_factory=Counter)
    member_replies: int = 0
    matched_replies: int = 0
    top_coverage: dict[int, float] = field(default_factory=dict)
    candidates_for_80: int | None = None
    slots: Counter[str] = field(default_factory=Counter)
    with_specific_spans: int = 0
    ids_reused_by_text: int = 0
    ids_reused_by_members: int = 0
    ids_new: int = 0
    ids_ambiguous: int = 0
    ids_disappeared: int = 0
    unwrapped_bodies: int = 0
    closings_dropped: int = 0

    def lines(self) -> list[str]:
        share = (lambda n: f"{n / self.agent_replies:.1%}") if self.agent_replies else str
        tops = " · ".join(f"前 {n}：{v:.1%}" for n, v in self.top_coverage.items())
        return [
            f"train 线程：{self.train_threads} · 有正文的客服回复：{self.agent_replies}",
            f"参与聚类（只在 train 内重新计算证据）：强 {self.learnable['strong']}"
            f" · 只有重复出现 {self.learnable['auxiliary']}"
            f" · 证据弱而排除 {self.weak_excluded}",
            f"不成为模板：线程数不足 {self.below_min_threads} 封"
            f" · 正文含占位符写法 {self.unsafe_bodies} 个簇",
            f"候选：{sum(self.candidates.values())}（strong {self.candidates['strong']}"
            f" · repeated_only {self.candidates['repeated_only']}）",
            f"覆盖率（占 train 全部客服回复）：来源回复 {share(self.member_replies)}"
            f" · 加上近似匹配的弱证据回复 {share(self.matched_replies)}",
            f"按名次累计：{tops} · 达到 80% 需要的候选数：{self.candidates_for_80 or '达不到'}",
            f"槽位：{dict(self.slots) or '无'}"
            f" · 有特定日期或时间的候选：{self.with_specific_spans}",
            f"ID：沿用（正文相同）{self.ids_reused_by_text}"
            f" · 沿用（来源重合）{self.ids_reused_by_members} · 新 {self.ids_new}"
            f" · 有歧义 {self.ids_ambiguous} · 消失 {self.ids_disappeared}",
            f"正文清理：接回硬换行 {self.unwrapped_bodies}"
            f" · 去掉末尾自带的结语 {self.closings_dropped}",
        ]


TOP_NS = (10, 25, 50, 100)


def _cleaned(body: str, report: "BuildReport") -> str:
    """去掉历史邮件的硬换行和末尾自带的结语（`copilot.kb.cleanup`），并计数。"""
    unwrapped = unwrap_lines(body)
    cleaned = drop_trailing_closing(unwrapped)
    report.unwrapped_bodies += unwrapped != body
    report.closings_dropped += cleaned != unwrapped.rstrip()
    return cleaned


def _categories(cluster: ReplyCluster, cases: dict[str, Case]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for thread_id in sorted({r.thread_id for r in cluster.replies}):
        category = cases[thread_id].category
        counts[category.value if category else "（无）"] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def build_candidates(
    cases: Iterable[Case],
    manifest: SplitManifest,
    previous: Iterable[TemplateCandidate] = (),
) -> tuple[list[TemplateCandidate], BuildReport]:
    """构建模板候选。`previous` 是上一次构建的候选，用来保持 ID 稳定。"""
    train = recompute_cross_thread_evidence(manifest.select(cases, Split.TRAIN))
    by_id = {case.id: case for case in train}
    result = cluster_replies(train)
    report = BuildReport(
        train_threads=len(train),
        agent_replies=result.agent_replies,
        weak_excluded=result.weak_excluded,
        below_min_threads=result.below_min_threads,
    )
    for case in train:
        for message in case.messages:
            if message.role is MessageRole.AGENT and message.role_strength in {
                RoleEvidenceStrength.STRONG,
                RoleEvidenceStrength.AUXILIARY,
            }:
                report.learnable[message.role_strength.value] += bool(text_key(message.core))

    # 代表正文中已有 `{{name}}` 写法的簇不能成为模板：会与槽位混淆（可能是构造的输入）。
    clusters = []
    for cluster in result.clusters:
        if PLACEHOLDER_PATTERN.search(cluster.representative.core):
            report.unsafe_bodies += 1
        else:
            clusters.append(cluster)

    assignment = assign_ids(
        [
            IdentityInput(
                key=c.representative.key, members=frozenset(r.message_id for r in c.replies)
            )
            for c in clusters
        ],
        [
            PreviousIdentity(
                id=p.id, key=p.representative_key, members=frozenset(p.source_message_ids)
            )
            for p in previous
        ],
    )
    report.ids_reused_by_text = assignment.reused_by_text
    report.ids_reused_by_members = assignment.reused_by_members
    report.ids_new = assignment.new
    report.ids_ambiguous = len(assignment.ambiguous)
    report.ids_disappeared = len(assignment.disappeared)

    candidates = []
    cumulative = 0
    for rank, (cluster, template_id) in enumerate(
        zip(clusters, assignment.ids, strict=True), start=1
    ):
        variants = cluster.variants
        detection = detect_slots(variants[0], variants[1:])
        cumulative += len(cluster.replies)
        total = result.agent_replies or 1
        candidates.append(
            TemplateCandidate(
                id=template_id,
                rank=rank,
                evidence=cluster.evidence,
                representative_message_id=cluster.representative.message_id,
                representative_key=cluster.representative.key,
                body_ja=_cleaned(detection.body_ja, report),
                slots=detection.slots,
                specific_spans=[SpecificSpan(kind=s.kind, text=s.text) for s in detection.specific],
                source_message_ids=[r.message_id for r in cluster.replies],
                thread_count=cluster.thread_count,
                variant_count=len(variants),
                categories=_categories(cluster, by_id),
                coverage=round(len(cluster.replies) / total, 6),
                cumulative_coverage=round(cumulative / total, 6),
            )
        )
        report.candidates[cluster.evidence.value] += 1
        report.slots.update(slot.name.rsplit("_", 1)[0] for slot in detection.slots)
        report.with_specific_spans += bool(detection.specific)
        if report.candidates_for_80 is None and cumulative / total >= 0.8:
            report.candidates_for_80 = rank

    report.member_replies = cumulative
    index = ClusterIndex(clusters)
    report.matched_replies = sum(
        1
        for case in train
        for m in case.messages
        if m.role is MessageRole.AGENT and text_key(m.core) and index.match(m.core) is not None
    )
    for n in TOP_NS:
        if candidates and n <= len(candidates):
            report.top_coverage[n] = candidates[n - 1].cumulative_coverage
    return candidates, report


def write_candidates(candidates: Iterable[TemplateCandidate], path: Path) -> None:
    """写成 JSONL（每行一个候选）。先写临时文件再替换，中途失败不会留下半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for candidate in candidates:
                f.write(candidate.model_dump_json())
                f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_candidates(path: Path) -> list[TemplateCandidate]:
    """读回 `write_candidates` 写出的文件；文件不存在时返回空列表（第一次构建）。"""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        return [TemplateCandidate.model_validate_json(line) for line in f if line.strip()]
