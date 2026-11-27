"""模板的人工审核：覆盖层、YAML 导出导入、发布（#5，提案 0003 §4、§12）。

**覆盖层**（`data/processed/kb/overlay.json`，被 git 忽略，应在仓库之外备份）以稳定的模板 ID
为键，保存所有者审核时看到并认可的内容快照（`TemplateReview`）。重新构建、重新标注不会改变
它：**审核过的内容以快照为准**，机器生成的新草稿只用于还没有审核过的模板。

**发布**（`merge`）把机器草稿、覆盖层和存储中已有的版本合并成要插入的新版本：

- 没有审核：机器草稿，`draft`。
- 有审核：快照的内容和审核状态。已批准的模板如果在重新构建后**正文变了**（簇的代表正文变化），
  改为 `needs_rereview`：所有者批准的是旧正文，不能默认新正文也被批准。正文**只在空白（换行）上
  不同**时（例如 `kb build` 接回了历史邮件的硬换行，`copilot.kb.cleanup`），内容就是批准过的内容：
  采用新的排版，审核状态不变。
- 类别取意图的类别；风险不低于意图的默认风险；只有已批准、风险为 `low` 的模板才能允许自动发送。
- 与存储中最新的版本相比内容没有变化时不新建版本，重复发布不会产生新行。变化时版本号加 1，
  `known_from` 沿用上一版本（审核只改措辞和状态，提案 0003 §3）。
"""

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AwareDatetime, Field, ValidationError

from copilot.contracts import ReplyTemplate, ReviewStatus, RiskLevel, TemplateSlot
from copilot.contracts.base import Contract, Document
from copilot.contracts.knowledge import EntryId
from copilot.kb.annotate import AnnotatedTemplate
from copilot.kb.knowledge import Knowledge

OVERLAY_FILE = "overlay.json"
REVIEW_DIR = "review"
_RISK_ORDER = [RiskLevel.LOW, RiskLevel.NORMAL, RiskLevel.HIGH]
_SPACE = re.compile(r"\s+")


class ReviewError(ValueError):
    """审核文件或覆盖层不合规。消息中写明模板 ID 或文件。"""


class TemplateContent(Contract):
    """模板中由审核决定的内容。字段含义见 `ReplyTemplate`。"""

    intent: EntryId
    title_zh: str = Field(min_length=1)
    description_zh: str
    use_when_zh: str
    avoid_when_zh: str
    body_ja: str = Field(min_length=1)
    slots: list[TemplateSlot] = Field(default_factory=list)
    risk: RiskLevel
    auto_send_allowed: bool = False
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None

    @classmethod
    def of(cls, template: ReplyTemplate) -> "TemplateContent":
        return cls.model_validate(template.model_dump(include=set(cls.model_fields)))


class TemplateReview(Contract):
    """所有者对一个模板的审核。"""

    review_status: Literal[ReviewStatus.APPROVED, ReviewStatus.RETIRED, ReviewStatus.DRAFT]
    """审核结果。`draft` 表示看过、改过措辞但还没有批准。"""
    content: TemplateContent
    """审核时认可的内容快照。"""
    reviewed_at: AwareDatetime
    note_zh: str | None = None
    """审核备注（中文）。"""


class Overlay(Document):
    """覆盖层：模板 ID → 审核。"""

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    reviews: dict[EntryId, TemplateReview] = Field(default_factory=dict)


def read_overlay(path: Path) -> Overlay:
    if not path.is_file():
        return Overlay()
    return Overlay.model_validate_json(path.read_text(encoding="utf-8"))


def write_overlay(overlay: Overlay, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(overlay.model_dump_json(indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


# --- 导入审核工具的决定 ---------------------------------------------------------


@dataclass
class ImportReport:
    changed: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)
    """无法导入的条目（模板不存在、版本不符等），附原因。"""

    def lines(self) -> list[str]:
        lines = [
            f"覆盖层：更新 {self.changed} · 没有变化 {self.unchanged} · 跳过 {len(self.skipped)}"
        ]
        lines += [f"  跳过：{reason}" for reason in self.skipped]
        return lines


def _record(
    overlay: Overlay, template_id: str, review: TemplateReview, report: ImportReport
) -> None:
    old = overlay.reviews.get(template_id)
    if old is not None and (old.review_status, old.content, old.note_zh) == (
        review.review_status,
        review.content,
        review.note_zh,
    ):
        report.unchanged += 1
        return
    overlay.reviews[template_id] = review
    report.changed += 1


def import_decisions(
    overlay: Overlay, decisions_path: Path, templates: Iterable[AnnotatedTemplate]
) -> ImportReport:
    """导入审核工具写出的决定（JSONL：template_id、template_version、decision、decided_at）。

    每个模板取最后一个决定（`undo` 撤销之前的决定）。批准 → `approved`，拒绝 → `retired`；
    快照取所审版本的机器草稿，版本号必须与当前的草稿一致，否则跳过并报告。
    """
    drafts = {t.template.id: t.template for t in templates}
    latest: dict[str, dict[str, Any]] = {}
    for n, line in enumerate(decisions_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("decision") == "undo":
            latest.pop(record["template_id"], None)
        elif record.get("decision") in {"approved", "rejected"}:
            latest[record["template_id"]] = record
        else:
            raise ReviewError(
                f"{decisions_path} 第 {n} 行：不认识的决定 {record.get('decision')!r}"
            )
    report = ImportReport()
    for template_id, record in sorted(latest.items()):
        draft = drafts.get(template_id)
        if draft is None:
            report.skipped.append(f"{template_id}：当前草稿中没有这个模板")
            continue
        if record["template_version"] != draft.version:
            report.skipped.append(
                f"{template_id}：审核的是版本 {record['template_version']}，"
                f"当前草稿是 {draft.version}"
            )
            continue
        status = ReviewStatus.APPROVED if record["decision"] == "approved" else ReviewStatus.RETIRED
        review = TemplateReview.model_validate(
            {
                "review_status": status,
                "content": TemplateContent.of(draft),
                "reviewed_at": datetime.fromisoformat(record["decided_at"]),
            }
        )
        _record(overlay, template_id, review, report)
    return report


# --- YAML 导出导入 --------------------------------------------------------------


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)

_YAML_FIELDS = ("review_status", "note_zh", *TemplateContent.model_fields)


def review_yaml(template: ReplyTemplate, info: Mapping[str, str]) -> str:
    """一个模板的审核文件：开头的注释是只读信息，之后是可以修改的字段。"""
    data = template.model_dump(mode="json", include=set(TemplateContent.model_fields))
    data = {"review_status": template.review_status.value, "note_zh": None, **data}
    header = [f"# 模板 {template.id} · 当前版本 {template.version}"]
    header += [f"# {key}：{value}" for key, value in info.items()]
    header += [
        "# 只修改下面的字段。review_status：draft | approved | retired。",
        "# 保存后运行 copilot kb review import，再运行 copilot kb publish。",
    ]
    body = yaml.dump(
        {key: data.get(key) for key in _YAML_FIELDS},
        Dumper=_Dumper,
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    )
    return "\n".join(header) + "\n" + body


def read_review_yaml(path: Path) -> tuple[ReviewStatus, TemplateContent, str | None]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ReviewError(f"{path}：YAML 格式错误：{exc}") from exc
    if not isinstance(data, dict) or set(data) - set(_YAML_FIELDS):
        raise ReviewError(f"{path}：只能有这些字段：{', '.join(_YAML_FIELDS)}")
    status = data.pop("review_status", None)
    note = data.pop("note_zh", None)
    if status not in {"draft", "approved", "retired"}:
        raise ReviewError(f"{path}：review_status 只能是 draft、approved 或 retired")
    try:
        content = TemplateContent.model_validate(data)
    except ValidationError as exc:
        raise ReviewError(f"{path}：{exc.errors()[0]['loc']}：{exc.errors()[0]['msg']}") from exc
    return ReviewStatus(status), content, note


def import_review_files(
    overlay: Overlay, directory: Path, knowledge: Knowledge, now: datetime
) -> ImportReport:
    """导入 `directory` 中的审核文件（`<模板 ID>.yaml`）。内容或状态变了的写进覆盖层。"""
    intents = {intent.id for intent in knowledge.intents}
    report = ImportReport()
    for path in sorted(directory.glob("*.yaml")):
        status, content, note = read_review_yaml(path)
        if content.intent not in intents:
            raise ReviewError(f"{path}：意图 {content.intent!r} 不在意图目录中")
        review = TemplateReview.model_validate(
            {"review_status": status, "content": content, "reviewed_at": now, "note_zh": note}
        )
        _record(overlay, path.stem, review, report)
    return report


# --- 发布 -----------------------------------------------------------------------


def _norm(text: str) -> str:
    return _SPACE.sub("", unicodedata.normalize("NFKC", text))


@dataclass
class PublishReport:
    new_versions: Counter[str] = field(default_factory=Counter)
    """按审核状态统计的新版本数。"""
    unchanged: int = 0
    body_changed: list[str] = field(default_factory=list)
    """已批准、但重新构建后正文变了的模板（改为 needs_rereview）。"""
    layout_updated: list[str] = field(default_factory=list)
    """审核过、重新构建后正文只在空白（换行）上不同的模板：采用新的排版，审核状态不变。"""
    risk_raised: list[str] = field(default_factory=list)
    auto_send_dropped: list[str] = field(default_factory=list)
    """审核要求允许自动发送，但不满足条件（未批准或风险不是 low）。"""
    reviewed_without_draft: list[str] = field(default_factory=list)
    """覆盖层中有审核、但当前草稿中没有的模板（簇消失了）。按快照继续发布。"""
    knowledge: tuple[int, int] = (0, 0)
    statuses: Counter[str] = field(default_factory=Counter)
    """发布后每个模板的当前状态。"""

    def lines(self) -> list[str]:
        def ids(items: list[str]) -> str:
            return f"（{'、'.join(items)}）" if items else ""

        return [
            f"模板：新版本 {sum(self.new_versions.values())} {dict(self.new_versions) or ''}"
            f" · 没有变化 {self.unchanged}",
            f"发布后的状态：{dict(sorted(self.statuses.items()))}",
            f"已批准但正文变了、改为 needs_rereview：{len(self.body_changed)}"
            f"{ids(self.body_changed)}",
            f"正文只有排版（换行）变化、沿用审核状态：{len(self.layout_updated)}"
            f"{ids(self.layout_updated)}",
            f"风险调到意图默认风险：{len(self.risk_raised)}{ids(self.risk_raised)}"
            f" · 不满足条件而取消自动发送：{len(self.auto_send_dropped)}"
            f"{ids(self.auto_send_dropped)}",
            f"有审核但当前草稿中没有：{len(self.reviewed_without_draft)}"
            f"{ids(self.reviewed_without_draft)}",
            f"knowledge/：新插入 {self.knowledge[0]} · 已存在 {self.knowledge[1]}",
        ]


_COMPARED = set(TemplateContent.model_fields) | {"review_status", "category"}


def merge(
    drafts: Iterable[AnnotatedTemplate],
    overlay: Overlay,
    knowledge: Knowledge,
    latest: Mapping[str, ReplyTemplate],
) -> tuple[list[ReplyTemplate], PublishReport]:
    """合并出要插入的新版本。`latest` 是存储中每个模板的最新版本。见模块说明。"""
    intents = {intent.id: intent for intent in knowledge.intents}
    drafts_by_id = {d.template.id: d.template for d in drafts}
    report = PublishReport()
    new: list[ReplyTemplate] = []
    ids = sorted(set(drafts_by_id) | set(overlay.reviews))
    for template_id in ids:
        draft = drafts_by_id.get(template_id)
        review = overlay.reviews.get(template_id)
        previous = latest.get(template_id)
        base = draft or previous
        if base is None:
            report.reviewed_without_draft.append(template_id)
            continue  # 从未发布过、草稿也没有了：没有出处，无法发布
        if draft is None:
            report.reviewed_without_draft.append(template_id)
        if review is None:
            content, status = TemplateContent.of(base), base.review_status
            recorded_at = base.recorded_at
        else:
            content, status, recorded_at = review.content, review.review_status, review.reviewed_at
            if draft is not None and _norm(draft.body_ja) != _norm(content.body_ja):
                if status is ReviewStatus.APPROVED:
                    status = ReviewStatus.NEEDS_REREVIEW
                    report.body_changed.append(template_id)
            elif draft is not None and draft.body_ja != content.body_ja:
                # 只有空白（换行）不同：内容就是审核过的内容，采用新的排版，审核状态不变
                content = content.model_copy(update={"body_ja": draft.body_ja})
                report.layout_updated.append(template_id)
        intent = intents.get(content.intent)
        if intent is None:
            raise ReviewError(f"{template_id}：意图 {content.intent!r} 不在意图目录中")
        risk = max(content.risk, intent.default_risk, key=_RISK_ORDER.index)
        if risk is not content.risk:
            report.risk_raised.append(template_id)
        auto_send = content.auto_send_allowed
        if auto_send and (status is not ReviewStatus.APPROVED or risk is not RiskLevel.LOW):
            auto_send = False
            report.auto_send_dropped.append(template_id)
        known_from = previous.known_from if previous else base.known_from
        candidate = ReplyTemplate.model_validate(
            {
                **base.model_dump(),
                **content.model_dump(),
                "category": intent.category,
                "risk": risk,
                "auto_send_allowed": auto_send,
                "review_status": status,
                "known_from": known_from,
                "recorded_at": max(recorded_at, known_from),
                "version": previous.version + 1 if previous else 1,
            }
        )
        report.statuses[status.value] += 1
        if previous is not None and previous.model_dump(include=_COMPARED) == candidate.model_dump(
            include=_COMPARED
        ):
            report.unchanged += 1
            continue
        new.append(candidate)
        report.new_versions[status.value] += 1
    return new, report
