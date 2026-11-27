"""审核与发布的命令行（#5）。SYNTHETIC：全部是合成数据。不读取本地 .env 和数据库。"""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from copilot import cli
from copilot.config import Settings
from copilot.contracts import ReplyTemplate, ReviewStatus
from copilot.kb.annotate import TEMPLATES_FILE, write_templates
from copilot.kb.store import KnowledgeStore
from copilot.storage import create_engine_for
from tests.kb.test_knowledge import REPO_KNOWLEDGE
from tests.kb.test_review import DRAFTS, T1

runner = CliRunner()


@pytest.fixture
def kb_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    url = f"sqlite:///{tmp_path / 'copilot.db'}"
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, database_url=url))  # type: ignore[call-arg]
    directory = tmp_path / "kb"
    write_templates(DRAFTS, directory / TEMPLATES_FILE)
    return directory


def _run(*args: str) -> str:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def _current(kb_dir: Path) -> dict[str, ReplyTemplate]:
    engine = create_engine_for(cli.get_settings().database_url)
    return {t.id: t for t in KnowledgeStore(engine).current(ReplyTemplate)}


def test_review_loop(kb_dir: Path) -> None:
    kb = ["--kb-dir", str(kb_dir)]
    out = _run("kb", "publish", *kb)
    assert "新版本 2" in out and "knowledge/：新插入" in out
    assert "新版本 0" in _run("kb", "publish", *kb)  # 幂等

    decisions = kb_dir / "decisions.jsonl"
    decisions.write_text(
        json.dumps(
            {
                "template_id": "tpl-thanks",
                "template_version": 1,
                "decision": "approved",
                "decided_at": T1.isoformat(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert "更新 1" in _run("kb", "review", "decisions", str(decisions), *kb)
    _run("kb", "publish", *kb)
    assert _current(kb_dir)["tpl-thanks"].review_status is ReviewStatus.APPROVED

    assert "2 个审核文件" in _run("kb", "review", "export", *kb)
    assert "跳过 2" in _run("kb", "review", "export", *kb)  # 不覆盖已有的审核文件
    path = kb_dir / "review" / "tpl-refund.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(review_status="retired", note_zh="合成：测试下线")
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    # 没有修改的 tpl-thanks 与覆盖层中的审核相同：导出后原样导入不产生变化。
    assert "更新 1 · 没有变化 1" in _run("kb", "review", "import", *kb)
    _run("kb", "publish", *kb)
    assert sorted(_current(kb_dir)) == ["tpl-thanks"]


def test_publish_rejects_edited_knowledge_without_a_new_version(
    kb_dir: Path, tmp_path: Path
) -> None:
    knowledge = tmp_path / "knowledge"
    shutil.copytree(REPO_KNOWLEDGE, knowledge)
    _run("kb", "publish", "--kb-dir", str(kb_dir), "--knowledge", str(knowledge))
    path = knowledge / "glossary.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("term_zh: 背包", "term_zh: 书包"))
    result = runner.invoke(
        cli.app, ["kb", "publish", "--kb-dir", str(kb_dir), "--knowledge", str(knowledge)]
    )
    assert result.exit_code == 1 and "新建版本" in result.output


def test_publish_without_drafts(kb_dir: Path, tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["kb", "publish", "--kb-dir", str(tmp_path / "empty")])
    assert result.exit_code == 1 and "copilot kb annotate" in result.output
