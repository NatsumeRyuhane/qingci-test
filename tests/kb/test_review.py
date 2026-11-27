"""模板的人工审核：覆盖层、导入、发布（#5）。SYNTHETIC：全部是合成数据。"""

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine

from copilot.contracts import ReplyTemplate, ReviewStatus, RiskLevel
from copilot.ingest.timestamps import JST
from copilot.kb.annotate import AnnotatedTemplate, KnownFromBasis
from copilot.kb.cluster import ClusterEvidence
from copilot.kb.knowledge import Knowledge, load_knowledge
from copilot.kb.review import (
    Overlay,
    ReviewError,
    TemplateContent,
    import_decisions,
    import_review_files,
    merge,
    read_overlay,
    review_yaml,
    write_overlay,
)
from copilot.kb.store import KnowledgeStore
from copilot.storage import create_engine_for, upgrade_database
from tests.kb.test_knowledge import REPO_KNOWLEDGE

T0 = datetime(2099, 1, 1, tzinfo=JST)
T1 = datetime(2099, 2, 1, tzinfo=JST)
T2 = datetime(2099, 3, 1, tzinfo=JST)
THANKS = "【合成データ】ご丁寧にお礼のご連絡をくださりありがとうございます。"
REFUND = "【合成データ】決済はストアで行っておりますので、ストアへお問い合わせください。"


def _draft(
    template_id: str, body: str, intent: str, risk: str, **extra: object
) -> AnnotatedTemplate:
    template = ReplyTemplate.model_validate(
        {
            "id": template_id,
            "version": 1,
            "known_from": T0,
            "recorded_at": T0,
            "origin": "reconstructed",
            "source_message_ids": [f"{len(template_id)}:2"],
            "intent": intent,
            "category": "other",
            "title_zh": "机器生成的标题",
            "description_zh": "机器生成的说明",
            "use_when_zh": "机器：什么时候用",
            "avoid_when_zh": "机器：什么时候不用",
            "body_ja": body,
            "risk": risk,
            **extra,
        }
    )
    return AnnotatedTemplate(
        template=template,
        rank=1,
        evidence=ClusterEvidence.STRONG,
        coverage=0.1,
        generic=True,
        time_bound=False,
        known_from_basis=KnownFromBasis.SENT_AT,
    )


DRAFTS = [
    _draft("tpl-thanks", THANKS, "thanks_reply", "low"),
    _draft("tpl-refund", REFUND, "refund_request", "high"),
]


@pytest.fixture
def knowledge() -> Knowledge:
    return load_knowledge(REPO_KNOWLEDGE)


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    url = f"sqlite:///{tmp_path / 'kb.db'}"
    upgrade_database(url)
    engine: Engine = create_engine_for(url)
    return KnowledgeStore(engine)


def _publish(
    store: KnowledgeStore, drafts: list[AnnotatedTemplate], overlay: Overlay, knowledge: Knowledge
) -> list[ReplyTemplate]:
    new, _ = merge(drafts, overlay, knowledge, store.latest(ReplyTemplate))
    store.add_all(new)
    return new


def _decisions(tmp_path: Path, *rows: tuple[str, str]) -> Path:
    path = tmp_path / "decisions.jsonl"
    lines = [
        json.dumps(
            {"template_id": tid, "template_version": 1, "decision": d, "decided_at": T1.isoformat()}
        )
        for tid, d in rows
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_unreviewed_drafts_are_published_once(store: KnowledgeStore, knowledge: Knowledge) -> None:
    first = _publish(store, DRAFTS, Overlay(), knowledge)
    assert [(t.id, t.version, t.review_status) for t in first] == [
        ("tpl-refund", 1, ReviewStatus.DRAFT),
        ("tpl-thanks", 1, ReviewStatus.DRAFT),
    ]
    assert first[0].category.value == "purchase"  # 类别来自意图
    assert _publish(store, DRAFTS, Overlay(), knowledge) == []  # 重复发布不产生新版本


def test_approval_creates_a_version_and_keeps_known_from(
    store: KnowledgeStore, knowledge: Knowledge, tmp_path: Path
) -> None:
    _publish(store, DRAFTS, Overlay(), knowledge)
    overlay = Overlay()
    path = _decisions(tmp_path, ("tpl-thanks", "approved"), ("tpl-refund", "rejected"))
    report = import_decisions(overlay, path, DRAFTS)
    assert report.changed == 2
    new = {t.id: t for t in _publish(store, DRAFTS, overlay, knowledge)}
    assert new["tpl-thanks"].version == 2
    assert new["tpl-thanks"].review_status is ReviewStatus.APPROVED
    assert new["tpl-thanks"].known_from == T0 and new["tpl-thanks"].recorded_at == T1
    assert new["tpl-refund"].review_status is ReviewStatus.RETIRED
    assert [t.id for t in store.current(ReplyTemplate)] == ["tpl-thanks"]  # 下线的不再被选中
    assert [t.model_visible for t in store.current(ReplyTemplate)] == [True]


def test_reviews_survive_a_rebuild(
    store: KnowledgeStore, knowledge: Knowledge, tmp_path: Path
) -> None:
    """重新标注改了机器生成的说明：审核过的快照不变，不产生新版本。"""
    overlay = Overlay()
    import_decisions(overlay, _decisions(tmp_path, ("tpl-thanks", "approved")), DRAFTS)
    _publish(store, DRAFTS, overlay, knowledge)
    rebuilt = [
        _draft("tpl-thanks", THANKS, "thanks_reply", "low", title_zh="重新标注的标题"),
        DRAFTS[1],
    ]
    assert _publish(store, rebuilt, overlay, knowledge) == []
    current = {t.id: t for t in store.current(ReplyTemplate)}
    assert current["tpl-thanks"].title_zh == "机器生成的标题"


def test_changed_body_needs_rereview(
    store: KnowledgeStore, knowledge: Knowledge, tmp_path: Path
) -> None:
    """重新构建后代表正文变了：所有者批准的是旧正文，改为 needs_rereview，不再提供给模型。"""
    overlay = Overlay()
    import_decisions(overlay, _decisions(tmp_path, ("tpl-thanks", "approved")), DRAFTS)
    _publish(store, DRAFTS, overlay, knowledge)
    rebuilt = [
        _draft("tpl-thanks", THANKS + "またのご連絡をお待ちしております。", "thanks_reply", "low")
    ]
    new, report = merge(rebuilt, overlay, knowledge, store.latest(ReplyTemplate))
    (template,) = new
    assert template.review_status is ReviewStatus.NEEDS_REREVIEW
    assert template.body_ja == THANKS  # 仍是批准时的正文
    assert not template.model_visible and report.body_changed == ["tpl-thanks"]


def test_yaml_round_trip_and_code_guards(
    store: KnowledgeStore, knowledge: Knowledge, tmp_path: Path
) -> None:
    _publish(store, DRAFTS, Overlay(), knowledge)
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    for template in store.current(ReplyTemplate):
        (review_dir / f"{template.id}.yaml").write_text(
            review_yaml(template, {"覆盖率": "10%"}), encoding="utf-8"
        )
    path = review_dir / "tpl-refund.yaml"
    assert path.read_text(encoding="utf-8").startswith("# 模板 tpl-refund · 当前版本 1")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(
        review_status="approved", title_zh="人工修改的标题", risk="low", auto_send_allowed=True
    )
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    overlay = Overlay()
    report = import_review_files(overlay, review_dir, knowledge, T2)
    assert report.changed == 2  # 两个文件都是第一次导入
    new, publish = merge(DRAFTS, overlay, knowledge, store.latest(ReplyTemplate))
    refund = next(t for t in new if t.id == "tpl-refund")
    assert refund.title_zh == "人工修改的标题" and refund.version == 2
    assert refund.risk is RiskLevel.HIGH  # 不能低于意图的默认风险
    assert not refund.auto_send_allowed  # 风险不是 low，不能自动发送
    assert publish.risk_raised == ["tpl-refund"] and publish.auto_send_dropped == ["tpl-refund"]


def test_import_decisions_handles_undo_and_versions(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    rows = [
        {"template_id": "tpl-thanks", "template_version": 1, "decision": "approved"},
        {"template_id": "tpl-thanks", "template_version": 1, "decision": "undo"},
        {"template_id": "tpl-refund", "template_version": 9, "decision": "approved"},
        {"template_id": "tpl-gone", "template_version": 1, "decision": "approved"},
    ]
    path.write_text(
        "\n".join(json.dumps({**r, "decided_at": T1.isoformat()}) for r in rows), encoding="utf-8"
    )
    overlay = Overlay()
    report = import_decisions(overlay, path, DRAFTS)
    assert overlay.reviews == {}
    assert len(report.skipped) == 2  # 版本不符、模板不存在


def test_bad_review_files_are_rejected(knowledge: Knowledge, tmp_path: Path) -> None:
    content = TemplateContent.of(DRAFTS[0].template).model_dump(mode="json")
    cases = {
        "extra.yaml": {"review_status": "approved", "unknown": 1, **content},
        "status.yaml": {"review_status": "needs_rereview", **content},
        "intent.yaml": {"review_status": "approved", **content, "intent": "no_such_intent"},
    }
    for name, data in cases.items():
        directory = tmp_path / name.removesuffix(".yaml")
        directory.mkdir()
        (directory / name).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ReviewError):
            import_review_files(Overlay(), directory, knowledge, T2)


def test_overlay_round_trip(tmp_path: Path) -> None:
    overlay = Overlay()
    import_decisions(overlay, _decisions(tmp_path, ("tpl-thanks", "approved")), DRAFTS)
    path = tmp_path / "overlay.json"
    write_overlay(overlay, path)
    assert read_overlay(path) == overlay
    assert read_overlay(tmp_path / "missing.json") == Overlay()
