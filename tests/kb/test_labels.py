"""标准答案标注（#5 → #7）。SYNTHETIC：全部是合成数据。"""

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from copilot.cli import app
from copilot.contracts import (
    Case,
    InquiryCategory,
    Message,
    MessageRole,
    NoReplyReason,
    ReplyTemplate,
    RoleEvidenceKind,
    RoleEvidenceStrength,
)
from copilot.eval import Split, SplitManifest, SplitMethod, write_manifest
from copilot.eval.split import RATIOS, split_fingerprint
from copilot.ingest import write_cases
from copilot.ingest.timestamps import JST
from copilot.kb.annotate import (
    TEMPLATES_FILE,
    AnnotatedTemplate,
    KnownFromBasis,
    write_templates,
)
from copilot.kb.cluster import ClusterEvidence, MatchKind
from copilot.kb.labels import (
    NoReplyLabel,
    ReplyLabel,
    Resolution,
    label_no_reply,
    label_replies,
    read_no_reply_labels,
    read_reply_labels,
)
from tests.cases import agent, case, player

K = RoleEvidenceKind
T = datetime(2099, 1, 1, tzinfo=JST)
REINSTALL = "【合成データ】お手数ですが、アプリを再インストールしてから再度お試しください。"
WINDOW = "【合成データ】3月2日11:00から14:00まで、ストアで購入できない不具合が発生しておりました。"
OTHER = "【合成データ】テンプレートにない個別のご案内です。"
DUPLICATE = "【合成データ】ログインボーナスが受け取れません。何度試しても同じです。"


def _strong(thread_id: str, seq: int, core: str) -> Message:
    return agent(thread_id, seq, core, K.KEYWORD, K.CORROBORATED_TEXT, source="999:2")


def _system(thread_id: str, seq: int) -> Message:
    body = "【合成データ】The mail system\nReason: 5.1.1"
    return Message(thread_id=thread_id, seq=seq, role=MessageRole.SYSTEM, raw_body=body, body=body)


def _cases() -> list[Case]:
    return [
        # train
        case(
            "1", player("1", 1), _strong("1", 2, REINSTALL), player("1", 3), _strong("1", 4, WINDOW)
        ),
        case("2", player("2", 1), _strong("2", 2, WINDOW)),
        case("3", _system("3", 1)),  # 只有系统邮件
        case("20", player("20", 1), _strong("20", 2, REINSTALL), content=DUPLICATE),
        case("21", content=DUPLICATE),  # 与 20 重复，20 有强证据的回复
        case("22", content=DUPLICATE.replace("ログイン", "ガチャ")),
        case("23", content=DUPLICATE.replace("ログイン", "ガチャ")),  # 彼此重复，都没有回复
        # test
        case(
            "9",
            player("9", 1),
            agent("9", 2, WINDOW.replace("11:00", "10:00"), K.KEYWORD),  # 近似、弱证据
            _system("9", 3),
        ),
        case("10", player("10", 1), _strong("10", 2, OTHER)),
    ]


def _manifest(cases: list[Case], test_ids: set[str] = frozenset({"9", "10"})) -> SplitManifest:
    return SplitManifest(
        method=SplitMethod.STRATIFIED,
        seed="syn",
        stratum_size=100,
        ratios=dict(RATIOS),
        assignments={c.id: Split.TEST if c.id in test_ids else Split.TRAIN for c in cases},
        inputs_fingerprint=split_fingerprint(cases),
    )


def _template(template_id: str, body: str, sources: list[str], rank: int) -> AnnotatedTemplate:
    template = ReplyTemplate(
        id=template_id,
        version=1,
        known_from=T,
        recorded_at=T,
        origin="reconstructed",
        source_message_ids=sources,
        intent="other",
        category=InquiryCategory.OTHER,
        title_zh="合成",
        description_zh="合成",
        use_when_zh="合成",
        avoid_when_zh="合成",
        body_ja=body,
    )
    return AnnotatedTemplate(
        template=template,
        rank=rank,
        evidence=ClusterEvidence.STRONG,
        coverage=0.5,
        generic=False,
        time_bound=False,
        known_from_basis=KnownFromBasis.SENT_AT,
    )


TEMPLATES = [
    _template("tpl-reinstall", REINSTALL, ["1:2", "20:2"], 1),
    _template("tpl-window", WINDOW, ["1:4", "2:2"], 2),
]


def _by_id(labels: list[ReplyLabel]) -> dict[str, ReplyLabel]:
    return {label.message_id: label for label in labels}


def test_every_reply_in_every_split_is_labeled() -> None:
    cases = _cases()
    labels = _by_id(label_replies(cases, _manifest(cases), TEMPLATES))
    assert sorted(labels) == ["10:2", "1:2", "1:4", "20:2", "2:2", "9:2"]
    first = labels["1:2"]
    assert (first.template_id, first.template_version, first.match) == (
        "tpl-reinstall",
        1,
        MatchKind.EXACT,
    )
    assert first.inquiry_message_id == "1:1" and first.first_agent_reply


def test_resolution_signals() -> None:
    cases = _cases()
    labels = _by_id(label_replies(cases, _manifest(cases), TEMPLATES))
    assert labels["1:2"].resolution is Resolution.UNRESOLVED  # 之后有玩家追问
    assert labels["1:4"].resolution is Resolution.RESOLVED  # 线程以它结束
    assert not labels["1:4"].first_agent_reply and labels["1:4"].inquiry_message_id == "1:3"
    assert labels["9:2"].resolution is Resolution.UNKNOWN  # 之后只有退信


def test_evaluation_replies_map_to_train_templates_or_no_match() -> None:
    cases = _cases()
    labels = _by_id(label_replies(cases, _manifest(cases), TEMPLATES))
    near = labels["9:2"]
    assert near.split is Split.TEST and near.template_id == "tpl-window"
    assert near.match is MatchKind.NEAR
    assert near.evidence is RoleEvidenceStrength.WEAK  # 主指标不用它，单独作为切片（D-012）
    unmatched = labels["10:2"]
    assert unmatched.template_id is None and unmatched.match is None


def test_template_sources_must_be_in_train() -> None:
    cases = _cases()
    stale = [_template("tpl-x", OTHER, ["10:2"], 1)]  # 来源在 test 中
    with pytest.raises(ValueError, match="不在 train 中"):
        label_replies(cases, _manifest(cases), stale)


def test_no_reply_only_from_explainable_reasons() -> None:
    cases = _cases()
    labels = {label.thread_id: label for label in label_no_reply(cases, _manifest(cases))}
    assert labels["3"].reason is NoReplyReason.SYSTEM_MESSAGE
    assert labels["21"].reason is NoReplyReason.DUPLICATE and labels["21"].duplicate_of == "20"
    assert "22" not in labels and "23" not in labels  # 彼此重复但都没有回复：原因不明
    assert "20" not in labels  # 已回复的那一个不是“不回复”
    assert "9" not in labels  # 有客服回复


def test_duplicate_of_a_weak_reply_is_not_a_label() -> None:
    cases = [
        case("1", player("1", 1), agent("1", 2, REINSTALL, K.KEYWORD), content=DUPLICATE),
        case("2", content=DUPLICATE),
    ]
    assert label_no_reply(cases, _manifest(cases, set())) == []


def test_label_invariants() -> None:
    with pytest.raises(ValidationError):
        ReplyLabel(
            message_id="1:2",
            split=Split.TRAIN,
            evidence=None,
            template_id="tpl-x",
            resolution=Resolution.RESOLVED,
            first_agent_reply=True,
        )
    with pytest.raises(ValidationError):
        NoReplyLabel(thread_id="1", split=Split.TRAIN, reason=NoReplyReason.OTHER)
    with pytest.raises(ValidationError):
        NoReplyLabel(thread_id="1", split=Split.TRAIN, reason=NoReplyReason.DUPLICATE)


def test_cli(tmp_path: Path) -> None:
    cases = _cases()
    cases_path, split_path, kb_dir = tmp_path / "c.jsonl", tmp_path / "s.json", tmp_path / "kb"
    write_cases(cases, cases_path)
    write_manifest(_manifest(cases), split_path)
    write_templates(TEMPLATES, kb_dir / TEMPLATES_FILE)
    args = ["kb", "label", "--cases", str(cases_path), "--split", str(split_path)]
    result = CliRunner().invoke(app, [*args, "--kb-dir", str(kb_dir)])
    assert result.exit_code == 0, result.output
    assert "客服回复 6 · 不回复 2" in result.output
    assert "合成データ" not in result.output
    assert len(read_reply_labels(kb_dir / "labels" / "replies.jsonl")) == 6
    assert len(read_no_reply_labels(kb_dir / "labels" / "no_reply.jsonl")) == 2

    missing = CliRunner().invoke(app, [*args, "--kb-dir", str(tmp_path / "empty")])
    assert missing.exit_code == 1 and "copilot kb annotate" in missing.output
