"""把案件切分为 train / dev / test（#5、#7，决策 D-012）。

- **train**：知识库只用它构建（#5）。人工编写 `knowledge/`、审核模板时也只看它。
- **dev**：调参（阈值、LLM 评审校准、prompt 迭代）。
- **test**：评测集，只在报告基线时运行，保持它能反映系统的真实效果。

默认方法是**在时间上均匀分布的分层抽样**（`stratified`）：按线程顺序（线程 ID 即提交顺序，
`thread_order`）把时间线切成每层 `STRATUM_SIZE` 个线程，每层内按固定种子的哈希顺序，以
`RATIOS` 的比例分配。这样 train 覆盖所有时期，知识库能学到“某个事件已经结束”这类只在
后期出现的信号；dev 和 test 的时间分布与整体相同。

另有**按时间先后切分**（`chronological`）作对比：最早的 70% 为 train，之后依次是 dev、test。
它衡量系统面对从未见过的时期（例如新的事故）时的表现。

两种方法中，**重复提交的线程**（`duplicate_groups`）整组放在同一侧：分层抽样时按组内最早的
线程所在的层分配，按时间切分时按组内最早的线程的位置分配。

均匀抽样意味着 test 案件旁边有时间更晚的 train 线程。由下游控制：知识库只读 train 线程；
#7 历史重放时，每个案件只看 `known_from` 不晚于它的“现在”的知识库版本。

结果只取决于案件 ID、重复提交的分组和参数，与输入顺序无关，可以复现。
"""

import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from copilot.contracts import Case, CaseId, MessageRole
from copilot.contracts.base import Document
from copilot.ingest.duplicates import duplicate_groups
from copilot.ingest.pipeline import thread_order


class Split(StrEnum):
    """切分的一侧。"""

    TRAIN = "train"
    """构建知识库。"""
    DEV = "dev"
    """调参。"""
    TEST = "test"
    """评测集，只在报告基线时运行。"""


class SplitMethod(StrEnum):
    """切分方法。"""

    STRATIFIED = "stratified"
    """在时间上均匀分布的分层抽样（默认）。"""
    CHRONOLOGICAL = "chronological"
    """按时间先后切分，作对比。"""


RATIOS: Mapping[Split, float] = {Split.TRAIN: 0.70, Split.DEV: 0.15, Split.TEST: 0.15}
"""各侧的线程比例。"""
STRATUM_SIZE = 100
"""分层抽样时每层的线程数。样本中约 100 个线程相当于一天（docs/data.md）。"""
SEED = "d012-v1"
"""分层抽样的种子。改了它，切分就变了：评测数字不再可以与之前比较。"""
DEFAULT_MANIFEST = Path("data/processed/split.json")
"""切分结果的默认位置（被 git 忽略：含有线程 ID，属于派生数据，见 data/README.md）。"""


class StaleSplitError(ValueError):
    """切分结果与案件不一致，例如重新导入后没有重新切分。"""


class SplitManifest(Document):
    """一次切分的结果和参数。写在 `data/processed/split.json`，不提交。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    method: SplitMethod
    """切分方法。"""
    seed: str
    """分层抽样的种子。按时间切分时不使用，仍然记录。"""
    stratum_size: int = Field(ge=1)
    """分层抽样时每层的线程数。"""
    ratios: dict[Split, float]
    """各侧的线程比例。"""
    assignments: dict[CaseId, Split]
    """每个案件分到哪一侧。"""
    inputs_fingerprint: str
    """影响切分的输入（案件 ID 和重复提交的分组）的指纹，见 `split_fingerprint`。
    `select` 用它发现过期的切分。"""

    @model_validator(mode="after")
    def _check_ratios(self) -> Self:
        _check_ratios(self.ratios)
        return self

    def ids(self, split: Split) -> set[str]:
        """分到 `split` 的案件 ID。"""
        return {case_id for case_id, side in self.assignments.items() if side is split}

    def select(self, cases: Iterable[Case], split: Split) -> list[Case]:
        """`cases` 中分到 `split` 的案件，顺序不变。

        `cases` 必须正好是切分时的那些案件，并且重复提交的分组没有变，否则报
        `StaleSplitError`：用过期的切分构建知识库，评测集的案件可能会混进训练部分（例如重新
        导入后两个线程成了重复提交，却分别在 train 和 test 中）。只改变了不影响切分的内容
        （例如清洗规则改动了正文）时，切分仍然有效。
        """
        cases = list(cases)
        given = {case.id for case in cases}
        if given != self.assignments.keys():
            missing = len(given - self.assignments.keys())
            extra = len(self.assignments.keys() - given)
            raise StaleSplitError(
                f"切分与案件不一致：{missing} 个案件不在切分中，切分中有 {extra} 个案件不存在。"
                "请重新运行 copilot split"
            )
        if split_fingerprint(cases) != self.inputs_fingerprint:
            raise StaleSplitError(
                "切分与案件不一致：重复提交的分组与切分时不同。请重新运行 copilot split"
            )
        return [case for case in cases if self.assignments[case.id] is split]


def _check_ratios(ratios: Mapping[Split, float]) -> None:
    if set(ratios) != set(Split):
        raise ValueError(f"ratios 必须正好包含 {[s.value for s in Split]}")
    if any(value <= 0 for value in ratios.values()):
        raise ValueError("每一侧的比例都必须大于 0")
    if abs(sum(ratios.values()) - 1) > 1e-9:
        raise ValueError("比例之和必须为 1")


def split_fingerprint(cases: Iterable[Case]) -> str:
    """影响切分的输入的指纹：案件 ID 和重复提交的分组（`duplicate_groups`）。

    两者都没有变时，切分结果不变（参数相同的前提下），所以只看它们，不看正文的其他部分。
    """
    cases = list(cases)
    return _fingerprint(cases, duplicate_groups(cases))


def _fingerprint(cases: list[Case], groups: list[list[str]]) -> str:
    ids = sorted((case.id for case in cases), key=thread_order)
    payload = json.dumps({"ids": ids, "duplicate_groups": groups}, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _units(cases: list[Case], groups: list[list[str]]) -> list[list[str]]:
    """切分的单位：一组重复提交，或一个单独的线程。按组内最早线程的顺序排列。"""
    ids = sorted({case.id for case in cases}, key=thread_order)
    grouped: dict[str, list[str]] = {}
    for group in groups:
        for case_id in group:
            grouped[case_id] = group
    units = []
    seen: set[str] = set()
    for case_id in ids:
        if case_id in seen:
            continue
        unit = grouped.get(case_id, [case_id])
        seen.update(unit)
        units.append(unit)
    return units


def _allocate(
    units: list[list[str]], ratios: Mapping[Split, float], assignments: dict[str, Split]
) -> None:
    """按给定的顺序把单位逐个分给当前最欠缺的一侧（按线程数计），使各侧接近 `ratios`。"""
    counts = dict.fromkeys(Split, 0)
    for unit in units:
        total = sum(counts.values()) + len(unit)
        # 平局时按 train、dev、test 的顺序（max 取第一个最大值）。
        side = max(Split, key=lambda s: ratios[s] * total - counts[s])
        counts[side] += len(unit)
        for case_id in unit:
            assignments[case_id] = side


def _hash_order(seed: str, unit: list[str]) -> str:
    return hashlib.sha256(f"{seed}\0{unit[0]}".encode()).hexdigest()


def split_cases(
    cases: Iterable[Case],
    *,
    method: SplitMethod = SplitMethod.STRATIFIED,
    seed: str = SEED,
    stratum_size: int = STRATUM_SIZE,
    ratios: Mapping[Split, float] = RATIOS,
) -> SplitManifest:
    """切分案件。参数的含义见模块说明。"""
    cases = list(cases)
    _check_ratios(ratios)
    groups = duplicate_groups(cases)
    units = _units(cases, groups)
    position = {
        case_id: i for i, case_id in enumerate(sorted((c.id for c in cases), key=thread_order))
    }
    assignments: dict[str, Split] = {}
    if method is SplitMethod.STRATIFIED:
        strata: dict[int, list[list[str]]] = {}
        for unit in units:
            strata.setdefault(position[unit[0]] // stratum_size, []).append(unit)
        for stratum in strata.values():
            _allocate(sorted(stratum, key=lambda u: _hash_order(seed, u)), ratios, assignments)
    else:
        # 按组内最早的线程在时间线上的原始位置决定一侧，整组跟随它。不能按已分配的线程数
        # 累计：一组重复提交会把它之后的所有线程往后推，边界附近的线程会被分错。
        total = len(position)
        bounds = [ratios[Split.TRAIN] * total, (ratios[Split.TRAIN] + ratios[Split.DEV]) * total]
        for unit in units:
            first = position[unit[0]]
            side = (
                Split.TRAIN if first < bounds[0] else Split.DEV if first < bounds[1] else Split.TEST
            )
            for case_id in unit:
                assignments[case_id] = side
    return SplitManifest(
        method=method,
        seed=seed,
        stratum_size=stratum_size,
        ratios=dict(ratios),
        assignments=dict(sorted(assignments.items(), key=lambda kv: thread_order(kv[0]))),
        inputs_fingerprint=_fingerprint(cases, groups),
    )


def write_manifest(manifest: SplitManifest, path: Path) -> None:
    """写成 JSON。先写临时文件再替换，中途失败不会留下半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(manifest.model_dump_json(indent=1))
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_manifest(path: Path) -> SplitManifest:
    """读回 `write_manifest` 写出的文件。"""
    return SplitManifest.model_validate_json(path.read_text(encoding="utf-8"))


TIME_BINS = 10
"""统计时间分布时，把线程顺序等分成这么多段。"""


def split_stats(cases: Iterable[Case], manifest: SplitManifest) -> list[str]:
    """可读的切分统计，每项一行，顺序固定。只含汇总数字，可以写进 PR（AGENTS.md 第 7 节）。"""
    cases = sorted(cases, key=lambda c: thread_order(c.id))
    manifest.select(cases, Split.TRAIN)  # 检查切分与案件一致
    side = manifest.assignments
    threads: Counter[Split] = Counter(side[c.id] for c in cases)
    replies: dict[Split, Counter[str]] = {s: Counter() for s in Split}
    statuses: dict[Split, Counter[str]] = {s: Counter() for s in Split}
    categories: dict[Split, Counter[str]] = {s: Counter() for s in Split}
    bins: dict[Split, Counter[int]] = {s: Counter() for s in Split}
    for i, case in enumerate(cases):
        s = side[case.id]
        statuses[s][case.status.value] += 1
        categories[s][case.category.value if case.category else "（无）"] += 1
        bins[s][i * TIME_BINS // len(cases)] += 1
        for message in case.messages:
            if message.role is MessageRole.AGENT:
                strength = message.role_strength
                replies[s][strength.value if strength else "none"] += 1
    grouped = sum(len(g) for g in duplicate_groups(cases))
    lines = [
        f"方法：{manifest.method.value} · 种子：{manifest.seed}"
        f" · 每层线程数：{manifest.stratum_size} · 重复提交整组分配的线程：{grouped}",
    ]
    for s in Split:
        share = threads[s] / len(cases) if cases else 0
        lines += [
            f"{s.value}：{threads[s]} 个线程（{share:.1%}）",
            f"  客服回复（按角色证据强度）：{_join(replies[s], ['strong', 'auxiliary', 'weak'])}",
            f"  案件状态：{_join(statuses[s], sorted(statuses[s]))}",
            f"  类别：{_join(categories[s], sorted(categories[s]))}",
            f"  时间分布（线程顺序 {TIME_BINS} 等分）：{_bins(bins[s])}",
        ]
    return lines


def _join(counter: Counter[str], order: Iterable[str]) -> str:
    return " · ".join(f"{key}: {counter[key]}" for key in order if counter.get(key))


def _bins(counter: Counter[int]) -> str:
    return " ".join(str(counter[b]) for b in range(TIME_BINS))
