"""离线评测记录、指标汇总与 Markdown 报告（#7，D-002、D-012）。

规则：
- `CaseEvalRecord` 只保存案件 ID、指标、分流、检查结果、评审布尔值和耗时/token，不保存
  玩家或客服原文。
- `render_markdown` 输出的 Markdown 报告**只含汇总数字**，不含任何样本数据片段（D-002），
  可以直接贴进 PR 描述或整理进 `docs/eval.md`。
"""

import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from copilot.contracts import (
    DraftCheckKind,
    DraftMode,
    InquiryCategory,
    NoReplyReason,
    RiskLevel,
    RoleEvidenceStrength,
    Route,
    TimeStatus,
)
from copilot.contracts.base import Document
from copilot.draft.route import AutoSendGate
from copilot.eval.judge import JudgeScores, JudgeVerdict
from copilot.eval.split import Split


class CaseEvalRecord(Document):
    """单个评测样本的运行结果（不含客户原文）。"""

    schema_version: Literal[1] = 1
    kind: Literal["reply", "no_reply"]
    case_id: str
    message_id: str | None = None
    split: Split
    evidence: RoleEvidenceStrength | None = None
    first_agent_reply: bool | None = None
    resolution: str | None = None
    now_status: TimeStatus
    problem_time_status: TimeStatus
    mode: DraftMode = DraftMode.PIPELINE

    expected_category: InquiryCategory | None = None
    predicted_category: InquiryCategory | None = None
    expected_intent: str | None = None
    predicted_intent: str | None = None
    expected_template_id: str | None = None
    expected_template_visible: bool | None = None
    """标准答案模板在当前重放时刻（`known_from` 过滤后）是否可见（docs/claims.md A-12）。"""
    expected_template_risk: RiskLevel | None = None
    chosen_template_id: str | None = None
    top3_template_ids: list[str] = Field(default_factory=list)

    route: Route | None = None
    no_reply_auto: bool = False
    expected_no_reply_reason: NoReplyReason | None = None
    predicted_no_reply_reason: NoReplyReason | None = None

    intent_confidence: float | None = None
    draft_confidence: float | None = None
    failed_checks: list[DraftCheckKind] = Field(default_factory=list)
    failed_gates: list[AutoSendGate] = Field(default_factory=list)
    high_risk: bool = False
    fallback_reason: str | None = None
    tool_calls: int = 0
    judge: JudgeScores | None = None
    judge_error: str | None = None
    """LLM 评审调用失败时的错误摘要（只含异常类型，不静默吞掉异常）。"""
    config_fingerprint: str | None = None
    """本次评测运行配置的指纹，用于缓存续跑时防止混入旧配置的结果。"""
    item_fingerprint: str | None = None
    """单条评测样本重放输入与标注的指纹，防止输入或标准答案变化后命中旧缓存。"""

    wall_seconds: float = 0.0
    step_seconds: dict[str, float] = Field(default_factory=dict)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None

    @field_validator("judge", mode="before")
    @classmethod
    def _strip_judge_notes(cls, value: object) -> object:
        if isinstance(value, JudgeVerdict):
            return value.scores()
        return value


def write_records(records: Iterable[CaseEvalRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for record in records:
                f.write(record.model_dump_json() + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _repair_incomplete_tail(path: Path) -> None:
    """在字节边界检查并修复尾部：仅保留完整有效尾行并补换行，否则截断至最后一个换行符。"""
    if not path.is_file():
        return
    size = path.stat().st_size
    if size == 0:
        return
    with path.open("r+b") as f:
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return
        chunk_size = 8192
        pos = size
        tail_start = 0
        while pos > 0:
            read_from = max(0, pos - chunk_size)
            f.seek(read_from)
            chunk = f.read(pos - read_from)
            nl = chunk.rfind(b"\n")
            if nl >= 0:
                tail_start = read_from + nl + 1
                break
            pos = read_from
        f.seek(tail_start)
        tail = f.read()
        if not tail.strip():
            f.truncate(tail_start)
            return
        try:
            CaseEvalRecord.model_validate_json(tail.decode("utf-8"))
        except Exception:
            f.truncate(tail_start)
        else:
            f.seek(0, os.SEEK_END)
            f.write(b"\n")


def append_record(record: CaseEvalRecord, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _repair_incomplete_tail(path)
    with path.open("ab") as f:
        f.write((record.model_dump_json() + "\n").encode("utf-8"))


def read_records(path: Path) -> list[CaseEvalRecord]:
    if not path.is_file():
        return []
    records: list[CaseEvalRecord] = []
    with path.open("rb") as f:
        for raw_line in f:
            if not raw_line.strip():
                continue
            try:
                records.append(CaseEvalRecord.model_validate_json(raw_line.decode("utf-8")))
            except Exception:
                # 容忍中断写入留下的残缺 UTF-8 或半行 JSON，避免续跑直接崩溃
                continue
    return records


def percentile(values: Sequence[float], q: float) -> float:
    """线性插值分位数（`q` 在 0 到 100 之间）。空序列返回 0.0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * (q / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _rate(hits: int, total: int) -> str:
    return f"{hits}/{total} ({hits / total:.1%})" if total else "0/0 (—)"


@dataclass
class SliceMetrics:
    """一组回复样本的汇总指标。"""

    total: int = 0
    errors: int = 0
    category_hits: int = 0
    category_total: int = 0
    intent_hits: int = 0
    intent_total: int = 0
    template_top1_hits: int = 0
    template_top3_hits: int = 0
    template_total: int = 0
    visible_template_top1_hits: int = 0
    visible_template_top3_hits: int = 0
    visible_template_total: int = 0
    judge_total: int = 0
    judge_errors: int = 0
    solution_agrees: int = 0
    register_appropriate: int = 0
    no_fabricated_promise: int = 0
    back_translation_faithful: int = 0
    citations_supported: int = 0
    no_other_case_leak: int = 0
    # P-05：代码启发式检查 vs LLM 评审交叉统计
    register_fp: int = 0
    register_fn: int = 0
    promise_fp: int = 0
    promise_fn: int = 0
    combined_success_hits: int = 0
    combined_success_total: int = 0
    combined_success_unknown: int = 0
    routes: Counter[str] = field(default_factory=Counter)
    high_risk_total: int = 0
    high_risk_auto_send: int = 0
    false_no_reply: int = 0
    false_no_reply_by_reason: Counter[str] = field(default_factory=Counter)
    check_failures: Counter[str] = field(default_factory=Counter)
    gate_failures: Counter[str] = field(default_factory=Counter)
    fallbacks: int = 0
    tool_calls_total: int = 0


def compute_slice(records: Sequence[CaseEvalRecord]) -> SliceMetrics:
    m = SliceMetrics(total=len(records))
    for r in records:
        if r.error is not None:
            m.errors += 1
            continue
        if r.no_reply_auto:
            m.routes["no_reply_auto"] += 1
            m.false_no_reply += 1
            reason = r.predicted_no_reply_reason.value if r.predicted_no_reply_reason else "unknown"
            m.false_no_reply_by_reason[reason] += 1
        elif r.route is not None:
            m.routes[r.route.value] += 1
        if r.high_risk:
            m.high_risk_total += 1
            if r.route is Route.AUTO_SEND:
                m.high_risk_auto_send += 1
        if r.expected_category is not None and r.predicted_category is not None:
            m.category_total += 1
            if r.predicted_category is r.expected_category:
                m.category_hits += 1
        if r.expected_intent is not None and r.predicted_intent is not None:
            m.intent_total += 1
            if r.predicted_intent == r.expected_intent:
                m.intent_hits += 1
        if r.expected_template_id is not None:
            m.template_total += 1
            in_top1 = (not r.no_reply_auto) and r.chosen_template_id == r.expected_template_id
            in_top3 = (not r.no_reply_auto) and r.expected_template_id in r.top3_template_ids
            if in_top1:
                m.template_top1_hits += 1
            if in_top3:
                m.template_top3_hits += 1
            if r.expected_template_visible:
                m.visible_template_total += 1
                if in_top1:
                    m.visible_template_top1_hits += 1
                if in_top3:
                    m.visible_template_top3_hits += 1
            if r.no_reply_auto or (not in_top3):
                # 误判为自动“不回复”或标准答案不在 Top-3 时，成功指标已确定失败。
                m.combined_success_total += 1
            elif r.judge is not None:
                m.combined_success_total += 1
                if r.judge.solution_agrees:
                    m.combined_success_hits += 1
            else:
                m.combined_success_unknown += 1
        if r.judge_error is not None:
            m.judge_errors += 1
        if r.judge is not None:
            m.judge_total += 1
            m.solution_agrees += int(r.judge.solution_agrees)
            m.register_appropriate += int(r.judge.register_appropriate)
            m.no_fabricated_promise += int(r.judge.no_fabricated_promise)
            m.back_translation_faithful += int(r.judge.back_translation_faithful)
            m.citations_supported += int(r.judge.citations_supported)
            m.no_other_case_leak += int(r.judge.no_other_case_leak)
            reg_failed = DraftCheckKind.REGISTER in r.failed_checks
            if reg_failed and r.judge.register_appropriate:
                m.register_fp += 1
            elif (not reg_failed) and (not r.judge.register_appropriate):
                m.register_fn += 1
            prom_failed = DraftCheckKind.FORBIDDEN_PROMISE in r.failed_checks
            if prom_failed and r.judge.no_fabricated_promise:
                m.promise_fp += 1
            elif (not prom_failed) and (not r.judge.no_fabricated_promise):
                m.promise_fn += 1
        for check in r.failed_checks:
            m.check_failures[check.value] += 1
        for gate in r.failed_gates:
            m.gate_failures[gate.value] += 1
        if r.fallback_reason:
            m.fallbacks += 1
        m.tool_calls_total += r.tool_calls
    return m


@dataclass
class NoReplyMetrics:
    total: int = 0
    errors: int = 0
    auto_hits: int = 0
    proposal_hits: int = 0
    by_reason: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    """reason -> (auto_hits, proposal_hits, total)"""


def compute_no_reply(records: Sequence[CaseEvalRecord]) -> NoReplyMetrics:
    m = NoReplyMetrics(total=len(records))
    counts: dict[str, list[int]] = {}
    for r in records:
        if r.error is not None:
            m.errors += 1
            continue
        reason = r.expected_no_reply_reason.value if r.expected_no_reply_reason else "unknown"
        bucket = counts.setdefault(reason, [0, 0, 0])
        bucket[2] += 1
        reason_matches = r.predicted_no_reply_reason is r.expected_no_reply_reason
        if r.no_reply_auto and reason_matches:
            m.auto_hits += 1
            bucket[0] += 1
        if (r.no_reply_auto or r.predicted_no_reply_reason is not None) and reason_matches:
            m.proposal_hits += 1
            bucket[1] += 1
    m.by_reason = {k: (v[0], v[1], v[2]) for k, v in sorted(counts.items())}
    return m


@dataclass
class LatencySummary:
    count: int
    wall_p50: float
    wall_p95: float
    wall_mean: float
    step_p50: dict[str, float]
    step_p95: dict[str, float]
    prompt_tokens_avg: float | None
    completion_tokens_avg: float | None
    prompt_tokens_total: int | None
    completion_tokens_total: int | None


def compute_latency(records: Sequence[CaseEvalRecord]) -> LatencySummary:
    ok = [r for r in records if r.error is None]
    walls = [r.wall_seconds for r in ok]
    step_keys = sorted({k for r in ok for k in r.step_seconds})
    step_p50 = {
        k: percentile([r.step_seconds[k] for r in ok if k in r.step_seconds], 50) for k in step_keys
    }
    step_p95 = {
        k: percentile([r.step_seconds[k] for r in ok if k in r.step_seconds], 95) for k in step_keys
    }
    prompts = [r.prompt_tokens for r in ok if r.prompt_tokens is not None]
    completions = [r.completion_tokens for r in ok if r.completion_tokens is not None]
    all_prompt_known = bool(ok) and len(prompts) == len(ok)
    all_comp_known = bool(ok) and len(completions) == len(ok)
    return LatencySummary(
        count=len(ok),
        wall_p50=percentile(walls, 50),
        wall_p95=percentile(walls, 95),
        wall_mean=(sum(walls) / len(walls)) if walls else 0.0,
        step_p50=step_p50,
        step_p95=step_p95,
        prompt_tokens_avg=(sum(prompts) / len(prompts)) if all_prompt_known else None,
        completion_tokens_avg=(sum(completions) / len(completions)) if all_comp_known else None,
        prompt_tokens_total=sum(prompts) if all_prompt_known else None,
        completion_tokens_total=sum(completions) if all_comp_known else None,
    )


def _calibration_rows(records: Sequence[CaseEvalRecord]) -> list[str]:
    buckets = [
        ("< 0.70", 0.0, 0.70),
        ("0.70–0.85", 0.70, 0.85),
        ("0.85–0.92", 0.85, 0.92),
        (">= 0.92", 0.92, 1.01),
    ]
    rows = []
    for label, lo, hi in buckets:
        sub = [
            r
            for r in records
            if r.error is None and r.draft_confidence is not None and lo <= r.draft_confidence < hi
        ]
        if not sub:
            continue
        m = compute_slice(sub)
        rows.append(
            f"| {label} | {len(sub)} | {_rate(m.template_top1_hits, m.template_total)} "
            f"| {_rate(m.template_top3_hits, m.template_total)} "
            f"| {_rate(m.solution_agrees, m.judge_total)} |"
        )
    return rows


def render_markdown(
    records: Sequence[CaseEvalRecord],
    *,
    title: str = "离线评测报告",
    meta: dict[str, str] | None = None,
) -> str:
    """生成只含汇总数字的 Markdown 报告（D-002）。"""
    replies = [r for r in records if r.kind == "reply"]
    no_replies = [r for r in records if r.kind == "no_reply"]
    strong = [r for r in replies if r.evidence is RoleEvidenceStrength.STRONG]
    non_strong = [r for r in replies if r.evidence is not RoleEvidenceStrength.STRONG]

    lines = [f"# {title}", ""]
    if meta:
        for k, v in meta.items():
            lines.append(f"- **{k}**：{v}")
        lines.append("")

    def _row(label: str, hits: int, total: int) -> str:
        return f"| {label} | {_rate(hits, total)} |"

    def _section(heading: str, subset: Sequence[CaseEvalRecord]) -> None:
        m = compute_slice(subset)
        ok = m.total - m.errors
        combined_val = _rate(m.combined_success_hits, m.combined_success_total)
        if m.combined_success_unknown > 0:
            combined_val += f"（另有 {m.combined_success_unknown} 条无评审结果）"
        lines.extend(
            [
                f"## {heading}（样本 {m.total}，成功运行 {ok}，错误 {m.errors}）",
                "",
                "| 指标 | 结果 |",
                "|---|---|",
                f"| **成功指标（模板 Top-3 且草稿意思一致）** | {combined_val} |",
                _row("模板 Top-1 准确率（全部有标准答案）", m.template_top1_hits, m.template_total),
                _row("模板 Top-3 准确率（全部有标准答案）", m.template_top3_hits, m.template_total),
                _row(
                    "模板 Top-1 准确率（标准答案当时可见，A-12）",
                    m.visible_template_top1_hits,
                    m.visible_template_total,
                ),
                _row(
                    "模板 Top-3 准确率（标准答案当时可见，A-12）",
                    m.visible_template_top3_hits,
                    m.visible_template_total,
                ),
                _row("类别准确率", m.category_hits, m.category_total),
                _row("意图准确率", m.intent_hits, m.intent_total),
                _row("高风险案件误分流为 `auto_send`", m.high_risk_auto_send, m.high_risk_total),
                _row("应回复案件被误判为自动“不回复”", m.false_no_reply, ok),
                "",
                "**分流分布**："
                + (
                    " · ".join(f"`{k}` {v} ({v / ok:.1%})" for k, v in sorted(m.routes.items()))
                    if ok
                    else "无"
                ),
                "",
            ]
        )
        if m.judge_total:
            lines.extend(
                [
                    "**LLM 评审质量（对照真实回复）**：",
                    "",
                    "| 维度 | 通过率 |",
                    "|---|---|",
                    f"| 解决方案一致 | {_rate(m.solution_agrees, m.judge_total)} |",
                    f"| 语体得体（H-05） | {_rate(m.register_appropriate, m.judge_total)} |",
                    f"| 无编造承诺（H-01） | {_rate(m.no_fabricated_promise, m.judge_total)} |",
                    f"| 回译忠实（H-14） | {_rate(m.back_translation_faithful, m.judge_total)} |",
                    f"| 引用成立（H-03） | {_rate(m.citations_supported, m.judge_total)} |",
                    f"| 无跨案件泄露（H-04） | {_rate(m.no_other_case_leak, m.judge_total)} |",
                    "",
                    f"- **启发式检查误报/漏报（P-05，对照 LLM 评审）**："
                    f"`register` 误报 {m.register_fp} · 漏报 {m.register_fn}；"
                    f"`forbidden_promise` 误报 {m.promise_fp} · 漏报 {m.promise_fn}",
                    "",
                ]
            )
        if m.judge_errors:
            lines.append(f"**LLM 评审失败次数**：{m.judge_errors}")
            lines.append("")
        if m.check_failures:
            chk = " · ".join(f"`{k}` {v}" for k, v in m.check_failures.most_common())
            lines.append(f"**代码自检未通过次数**：{chk}")
            lines.append("")
        if m.gate_failures:
            gates = " · ".join(f"`{k}` {v}" for k, v in m.gate_failures.most_common(6))
            lines.append(f"**自动发送关卡未通过（前 6）**：{gates}")
            lines.append("")

    _section("主指标（强角色证据 strong）", strong)
    if non_strong:
        _section("辅助与弱证据切片（auxiliary / weak，D-012）", non_strong)

    # 按时间状态分别报告
    if strong:
        lines.extend(
            [
                "## 按时间状态分切片（强证据）",
                "",
                "| `now` 状态 | `problem_time` 状态 | 样本数 | 模板 Top-3"
                " | 解决方案一致 | `review` / `escalate` | `draft_case` p50"
                " | 平均 Token（输入/输出） |",
                "|---|---|---:|---|---|---|---:|---|",
            ]
        )
        groups: dict[tuple[str, str], list[CaseEvalRecord]] = {}
        for r in strong:
            groups.setdefault((r.now_status.value, r.problem_time_status.value), []).append(r)
        for (ns, ps), grp in sorted(groups.items()):
            gm = compute_slice(grp)
            glat = compute_latency(grp)
            tok_col = (
                f"{glat.prompt_tokens_avg:.0f} / {glat.completion_tokens_avg:.0f}"
                if glat.prompt_tokens_avg is not None and glat.completion_tokens_avg is not None
                else "未知"
            )
            lines.append(
                f"| `{ns}` | `{ps}` | {gm.total} "
                f"| {_rate(gm.template_top3_hits, gm.template_total)} "
                f"| {_rate(gm.solution_agrees, gm.judge_total)} "
                f"| {gm.routes.get('review', 0)} / {gm.routes.get('escalate', 0)} "
                f"| {glat.wall_p50:.1f}s | {tok_col} |"
            )
        lines.append("")

    # 置信度校准（P-06）
    cal_rows = _calibration_rows(strong)
    if cal_rows:
        lines.extend(
            [
                "## 信心值校准（`draft_confidence`，P-06）",
                "",
                "| 区间 | 样本数 | 模板 Top-1 | 模板 Top-3 | 解决方案一致 |",
                "|---|---:|---|---|---|",
                *cal_rows,
                "",
            ]
        )

    # “不回复”指标
    if no_replies:
        nr = compute_no_reply(no_replies)
        ok_nr = nr.total - nr.errors
        lines.extend(
            [
                f"## “不回复”准确率（样本 {nr.total}，成功运行 {ok_nr}）",
                "",
                "| 类别 | 自动生效 | 含人工确认提议 |",
                "|---|---|---|",
                f"| 合计 | {_rate(nr.auto_hits, ok_nr)} | {_rate(nr.proposal_hits, ok_nr)} |",
            ]
        )
        for reason, (auto_h, prop_h, tot) in nr.by_reason.items():
            lines.append(f"| `{reason}` | {_rate(auto_h, tot)} | {_rate(prop_h, tot)} |")
        lines.append("")

    # 延迟与成本（按模式分开，自动“不回复”决策与生成草稿分列）
    drafted_ok = [r for r in records if r.error is None and not r.no_reply_auto]
    auto_nr_ok = [r for r in records if r.error is None and r.no_reply_auto]
    modes = sorted({r.mode.value for r in drafted_ok})
    if modes or auto_nr_ok:
        lines.extend(["## 延迟与成本（`draft_case` 耗时，不含重放与检索初始化）", ""])
        for mode_name in modes:
            sub = [r for r in drafted_ok if r.mode.value == mode_name]
            lat = compute_latency(sub)
            tok_in = (
                f"{lat.prompt_tokens_avg:.0f}（总 {lat.prompt_tokens_total}）"
                if lat.prompt_tokens_avg is not None
                else "未知"
            )
            tok_out = (
                f"{lat.completion_tokens_avg:.0f}（总 {lat.completion_tokens_total}）"
                if lat.completion_tokens_avg is not None
                else "未知"
            )
            lines.extend(
                [
                    f"### 模式：`{mode_name}`（{lat.count} 个起草案件）",
                    "",
                    f"- `draft_case` 延迟（草稿可用）：p50 **{lat.wall_p50:.1f}s** · "
                    f"p95 **{lat.wall_p95:.1f}s** · 均值 {lat.wall_mean:.1f}s",
                    f"- 单案件平均 token：输入 {tok_in} · 输出 {tok_out}",
                    "",
                ]
            )
            if lat.step_p50:
                lines.extend(
                    [
                        "| 步骤 | p50 (s) | p95 (s) |",
                        "|---|---:|---:|",
                    ]
                )
                for step in sorted(lat.step_p50):
                    lines.append(
                        f"| `{step}` | {lat.step_p50[step]:.1f} | {lat.step_p95[step]:.1f} |"
                    )
                lines.append("")
        if auto_nr_ok:
            nr_lat = compute_latency(auto_nr_ok)
            lines.append(
                f"- 自动“不回复”决策延迟（`no_reply_auto`，{nr_lat.count} 个案件）："
                f"p50 **{nr_lat.wall_p50:.1f}s** · p95 **{nr_lat.wall_p95:.1f}s** · "
                f"均值 {nr_lat.wall_mean:.1f}s"
            )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
