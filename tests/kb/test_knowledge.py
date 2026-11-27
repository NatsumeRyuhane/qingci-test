"""knowledge/ 中人工编写的知识（#5）。校验仓库中提交的内容，以及各种不合规的情况。

SYNTHETIC：测试中构造的案件和改动全部是合成数据。
"""

import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from copilot.cli import app
from copilot.contracts import PolicyKind, RiskLevel
from copilot.ingest import write_cases
from copilot.kb.knowledge import (
    KnowledgeError,
    LeakKind,
    find_leaks,
    load_knowledge,
)
from tests.cases import agent, case, player

REPO_KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"


def test_committed_knowledge_is_valid() -> None:
    knowledge = load_knowledge(REPO_KNOWLEDGE)
    intents = {intent.id: intent for intent in knowledge.intents}
    assert "other" in intents  # 都不合适时的去处
    # 自动发送允许清单只有本身就是完整答复、答错代价很小的意图（D-001）。
    assert sorted(i for i, intent in intents.items() if intent.auto_send_eligible) == [
        "feedback_acknowledgement",
        "thanks_reply",
    ]
    assert intents["refund_request"].default_risk is RiskLevel.HIGH
    assert all(entry.model_visible for entry in knowledge.entries())  # 评审即审核


def test_committed_notices_know_no_earlier_than_they_start() -> None:
    """known_from 必须是上界：运营方不可能在事件开始之前就知道它。"""
    for notice in load_knowledge(REPO_KNOWLEDGE).notices:
        assert notice.known_from >= notice.event_start, notice.id


def test_committed_policies_are_checked_by_code() -> None:
    policies = {p.id: p for p in load_knowledge(REPO_KNOWLEDGE).policies}
    draft = "【合成データ】ご迷惑をおかけしたため、全額を返金いたします。"
    assert policies["no-refund-promise"].matching_phrases(draft) == ["返金いたします"]
    assert policies["no-refund-promise"].kind is PolicyKind.FORBIDDEN_PROMISE
    complaint = "【合成データ】対応してくれないなら消費者センターに相談します"  # 全角半角不影响
    assert policies["escalate-legal"].matching_phrases(complaint) == ["消費者センター"]


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    target = tmp_path / "knowledge"
    shutil.copytree(REPO_KNOWLEDGE, target)
    return target


def _edit(path: Path, change: object) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)  # type: ignore[operator]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_unknown_top_level_key(knowledge_dir: Path) -> None:
    _edit(knowledge_dir / "glossary.yaml", lambda d: d.update(extra=1))
    with pytest.raises(KnowledgeError, match="defaults 和 entries"):
        load_knowledge(knowledge_dir)


def test_invalid_entry_names_file_and_id(knowledge_dir: Path) -> None:
    def make_unsafe(data: dict) -> None:  # type: ignore[type-arg]
        data["entries"][0]["auto_send_eligible"] = True  # default_risk 不是 low

    _edit(knowledge_dir / "intents.yaml", make_unsafe)
    with pytest.raises(KnowledgeError, match=r"intents\.yaml 中的 bug_investigating"):
        load_knowledge(knowledge_dir)


def test_duplicate_entry(knowledge_dir: Path) -> None:
    _edit(knowledge_dir / "glossary.yaml", lambda d: d["entries"].append(d["entries"][0]))
    with pytest.raises(KnowledgeError, match="重复"):
        load_knowledge(knowledge_dir)


def test_dangling_intent_reference(knowledge_dir: Path) -> None:
    def dangle(data: dict) -> None:  # type: ignore[type-arg]
        data["entries"][0]["intents"] = ["no_such_intent"]

    _edit(knowledge_dir / "policies.yaml", dangle)
    with pytest.raises(KnowledgeError, match="no_such_intent"):
        load_knowledge(knowledge_dir)


def test_style_guide_must_match_ingest(knowledge_dir: Path) -> None:
    _edit(knowledge_dir / "style_guide.yaml", lambda d: d["closing"].update(ja="【合成】結び"))
    with pytest.raises(KnowledgeError, match="closing"):
        load_knowledge(knowledge_dir)


def test_malformed_yaml(knowledge_dir: Path) -> None:
    (knowledge_dir / "notices.yaml").write_text("entries: [\n", encoding="utf-8")
    with pytest.raises(KnowledgeError, match="YAML"):
        load_knowledge(knowledge_dir)


# --- 与样本相同的文本 -----------------------------------------------------------

LEAKED = "【合成データ】これは玩家が書いた二十文字以上の独自の文章です"


def _with_glossary_note(knowledge_dir: Path, note: str) -> None:
    _edit(knowledge_dir / "glossary.yaml", lambda d: d["entries"][0].update(note_zh=note))


def test_player_text_in_knowledge_is_a_leak(knowledge_dir: Path) -> None:
    _with_glossary_note(knowledge_dir, "说明：" + LEAKED)
    cases = [case("1", player("1", 1, LEAKED + "。よろしくお願いします"))]
    leaks = find_leaks(load_knowledge(knowledge_dir), cases)
    assert [(leak.source, leak.kind, leak.threads) for leak in leaks] == [
        ("glossary.yaml:game-title", LeakKind.PLAYER_TEXT, 1)
    ]


def test_template_text_in_knowledge_is_a_leak(knowledge_dir: Path) -> None:
    _with_glossary_note(knowledge_dir, LEAKED)
    cases = [case("1", player("1", 1), agent("1", 2, LEAKED))]
    (leak,) = find_leaks(load_knowledge(knowledge_dir), cases)
    assert leak.kind is LeakKind.AGENT_REPLY


def test_style_phrases_pasted_back_are_not_leaks() -> None:
    knowledge = load_knowledge(REPO_KNOWLEDGE)
    phrase = knowledge.style_guide.contact_again[0].ja
    pasted = "【合成データ】前回の返信です：" + phrase
    cases = [case("1", player("1", 1), agent("1", 2, phrase), player("1", 3, pasted))]
    assert find_leaks(knowledge, cases) == []


def test_cli(knowledge_dir: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    ok = runner.invoke(app, ["kb", "knowledge", "--dir", str(knowledge_dir)])
    assert ok.exit_code == 0 and "校验通过" in ok.output

    _with_glossary_note(knowledge_dir, LEAKED)
    cases_path = tmp_path / "cases.jsonl"
    write_cases([case("1", player("1", 1, LEAKED))], cases_path)
    args = ["kb", "knowledge", "--dir", str(knowledge_dir), "--check-leaks"]
    leaked = runner.invoke(app, [*args, "--cases", str(cases_path)])
    assert leaked.exit_code == 1
    assert "glossary.yaml:game-title" in leaked.output and LEAKED not in leaked.output

    _edit(knowledge_dir / "glossary.yaml", lambda d: d.update(extra=1))
    assert runner.invoke(app, ["kb", "knowledge", "--dir", str(knowledge_dir)]).exit_code == 1
