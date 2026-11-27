"""演示数据的重放（`copilot seed`）。SYNTHETIC：全部是合成数据，不读取本地样本。"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from copilot import cli
from copilot.config import Settings
from copilot.contracts import Case, CaseStatus, InquiryCategory, RoleEvidenceKind
from copilot.eval import Split
from copilot.eval.split import split_cases, write_manifest
from copilot.ingest import write_cases
from copilot.storage import SqlRepository, create_engine_for, session_scope, upgrade_database
from copilot.workbench.seed import pick, replayable, seed
from tests.cases import agent, case, player

runner = CliRunner()


def _cat(c: Case, category: InquiryCategory) -> Case:
    return c.model_copy(update={"category": category})


def test_replayable_truncates_after_last_player_message() -> None:
    thread = case(
        "100",
        player("100", 1),
        agent("100", 2, "【合成データ】回答", RoleEvidenceKind.QUOTE_CORROBORATION, source="100:3"),
        player("100", 3, "【合成データ】追加"),
        agent("100", 4, "【合成データ】再回答", RoleEvidenceKind.KEYWORD),
    ).model_copy(update={"status": CaseStatus.REPLIED})

    replay = replayable(thread)
    assert replay is not None
    assert [m.seq for m in replay.messages] == [1, 2, 3]
    assert replay.status is CaseStatus.OPEN
    assert replayable(case("101", agent("101", 1, "【合成データ】"))) is None


def test_pick_rotates_categories_deterministically() -> None:
    cases = [
        _cat(case("1"), InquiryCategory.BUG),
        _cat(case("2"), InquiryCategory.BUG),
        _cat(case("3"), InquiryCategory.PURCHASE),
        _cat(case("4"), InquiryCategory.FEEDBACK),
        case("5", agent("5", 1, "【合成データ】")),  # 没有玩家邮件
    ]
    assert [c.id for c in pick(cases, 3)] == ["1", "4", "3"]
    assert [c.id for c in pick(reversed(cases), 10)] == ["1", "4", "3", "2"]


def test_seed_skips_existing_and_reports_missing(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_database(url)
    engine = create_engine_for(url)
    cases = [case("1"), case("2"), case("3", agent("3", 1, "【合成データ】"))]
    with session_scope(engine) as session:
        repo = SqlRepository(session)
        repo.save_case(case("1").model_copy(update={"status": CaseStatus.REPLIED}))
        report = seed(repo, cases, ids=["1", "2", "3", "404"])
        assert report.added == ["2"]
        assert report.skipped_existing == ["1"]
        assert report.missing == ["3", "404"]
        stored = repo.get_case("1")
        assert stored is not None and stored.status is CaseStatus.REPLIED  # 没有被覆盖
    engine.dispose()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, database_url=url))  # type: ignore[call-arg]
    cases = [case(str(i)) for i in range(1, 41)]
    paths = {"cases": tmp_path / "cases.jsonl", "split": tmp_path / "split.json", "db": tmp_path}
    write_cases(cases, paths["cases"])
    write_manifest(split_cases(cases), paths["split"])
    return paths


def _ids(output: str) -> list[str]:
    return output.split("：", 1)[1].split("\n", 1)[0].split(", ")


def test_cli_seeds_only_from_dev(env: dict[str, Path]) -> None:
    from copilot.eval import read_manifest
    from copilot.ingest import read_cases

    args = ["seed", "--cases", str(env["cases"]), "--split-file", str(env["split"]), "--count", "3"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    dev = {c.id for c in read_manifest(env["split"]).select(read_cases(env["cases"]), Split.DEV)}
    assert len(_ids(result.output)) == 3 and set(_ids(result.output)) <= dev

    again = runner.invoke(cli.app, args)
    assert "已存在、跳过 3 个" in again.output


def test_cli_rejects_train_and_missing_inputs(env: dict[str, Path]) -> None:
    base = ["seed", "--cases", str(env["cases"]), "--split-file", str(env["split"])]
    assert runner.invoke(cli.app, [*base, "--split", "train"]).exit_code == 2
    missing = runner.invoke(cli.app, ["seed", "--cases", str(env["db"] / "none.jsonl")])
    assert missing.exit_code == 1
    assert runner.invoke(cli.app, [*base, "--ids", "1,404"]).exit_code == 1
