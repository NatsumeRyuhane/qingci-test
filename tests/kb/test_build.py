"""模板候选的构建（#5，D-012）。SYNTHETIC：全部是合成数据。"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from copilot.cli import app
from copilot.contracts import Case, RoleEvidenceKind
from copilot.eval import Split, SplitManifest, SplitMethod, StaleSplitError, write_manifest
from copilot.eval.split import RATIOS, split_fingerprint
from copilot.ingest import write_cases
from copilot.kb.build import build_candidates, read_candidates, write_candidates
from copilot.kb.cluster import ClusterEvidence
from tests.cases import agent, case, player

K = RoleEvidenceKind
REINSTALL = "【合成データ】お手数ですが、アプリを再インストールしてから再度お試しください。"
TRANSFER = "【合成データ】データの引き継ぎには、引き継ぎコードとパスワードが必要です。"
WINDOW = "【合成データ】3月2日11:00～14:00の間、ストアで購入できない不具合が発生しておりました。"


def _anchored(thread_id: str, core: str) -> Case:
    """一个线程：玩家咨询 → 客服回复 → 玩家追问（追问用引用佐证了这封回复）。"""
    reply = agent(thread_id, 2, core, K.KEYWORD, K.QUOTE_CORROBORATION, source=f"{thread_id}:3")
    return case(thread_id, player(thread_id, 1), reply, player(thread_id, 3))


def _plain(thread_id: str, core: str, *evidence: K, source: str | None = None) -> Case:
    reply = agent(thread_id, 2, core, K.KEYWORD, *evidence, source=source)
    return case(thread_id, player(thread_id, 1), reply)


def _manifest(cases: list[Case], test_ids: set[str]) -> SplitManifest:
    return SplitManifest(
        method=SplitMethod.STRATIFIED,
        seed="syn",
        stratum_size=100,
        ratios=dict(RATIOS),
        assignments={c.id: Split.TEST if c.id in test_ids else Split.TRAIN for c in cases},
        inputs_fingerprint=split_fingerprint(cases),
    )


def _corpus() -> list[Case]:
    return [
        _anchored("1", REINSTALL),
        _plain("2", REINSTALL, K.CORROBORATED_TEXT, source="1:2"),
        _plain("3", REINSTALL, K.CORROBORATED_TEXT, source="1:2"),
        _anchored("4", WINDOW),
        _plain("5", WINDOW, K.CORROBORATED_TEXT, source="4:2"),
        # TRANSFER 只在 test 线程 90 中有引用佐证；train 中的 6、7 在导入时因此得到强证据。
        _plain("6", TRANSFER, K.CORROBORATED_TEXT, source="90:2"),
        _plain("7", TRANSFER, K.CORROBORATED_TEXT, source="90:2"),
        _anchored("90", TRANSFER),
        _anchored("91", "【合成データ】評価用スレッドにしかない回答です。"),
        _anchored("92", "【合成データ】評価用スレッドにしかない回答です。"),
    ]


TEST_IDS = {"90", "91", "92"}


def test_only_train_is_learned() -> None:
    cases = _corpus()
    candidates, report = build_candidates(cases, _manifest(cases, TEST_IDS))
    bodies = [c.body_ja for c in candidates]
    assert bodies == [REINSTALL, WINDOW]  # TRANSFER 的信任只来自 test，test 的正文不进入
    assert report.train_threads == 7
    assert report.weak_excluded == 2  # 6、7 只在 train 内计算时只剩弱证据
    assert all(
        sid.split(":")[0] not in TEST_IDS for c in candidates for sid in c.source_message_ids
    )


def test_evaluation_threads_cannot_change_the_build() -> None:
    """D-012 的不变量：改动 dev/test 线程的内容，构建结果不变。"""
    cases = _corpus()
    manifest = _manifest(cases, TEST_IDS)
    changed = [
        _anchored(c.id, "【合成データ】差し替えた回答です。") if c.id in TEST_IDS else c
        for c in cases
    ]
    assert build_candidates(changed, manifest) == build_candidates(cases, manifest)


def test_stale_split_is_rejected() -> None:
    cases = _corpus()
    with pytest.raises(StaleSplitError):
        build_candidates([*cases, _anchored("99", REINSTALL)], _manifest(cases, TEST_IDS))


def test_candidates_are_ranked_with_coverage_and_metadata() -> None:
    cases = _corpus()
    first, second = build_candidates(cases, _manifest(cases, TEST_IDS))[0]
    assert (first.rank, second.rank) == (1, 2)
    assert first.source_message_ids == ["1:2", "2:2", "3:2"]
    assert first.thread_count == 3 and first.evidence is ClusterEvidence.STRONG
    assert first.coverage == pytest.approx(3 / 7)
    assert second.cumulative_coverage == pytest.approx(5 / 7)
    assert [s.text for s in second.specific_spans] == ["3月2日11:00～14:00"]


def test_rebuild_keeps_ids() -> None:
    cases = _corpus()
    manifest = _manifest(cases, TEST_IDS)
    first, _ = build_candidates(cases, manifest)
    again, report = build_candidates(cases, manifest, first)
    assert [c.id for c in again] == [c.id for c in first]
    assert report.ids_reused_by_text == 2 and report.ids_new == 0


def test_forged_placeholder_body_is_not_a_candidate() -> None:
    """伪造的回复中写了占位符（例如想让起草步骤填入任意内容）：不成为候选。"""
    forged = "【合成データ】{{refund_amount}}円を返金いたします。"
    cases = [_plain(str(i), forged, K.REPEATED_TEXT) for i in range(1, 4)]
    candidates, report = build_candidates(cases, _manifest(cases, set()))
    assert candidates == [] and report.unsafe_bodies == 1


def test_report_has_only_aggregates() -> None:
    cases = _corpus()
    _, report = build_candidates(cases, _manifest(cases, TEST_IDS))
    lines = report.lines()
    assert any("候选：2" in line for line in lines)
    assert not any("合成データ" in line for line in lines)


def test_candidates_round_trip(tmp_path: Path) -> None:
    cases = _corpus()
    candidates, _ = build_candidates(cases, _manifest(cases, TEST_IDS))
    path = tmp_path / "candidates.jsonl"
    write_candidates(candidates, path)
    assert read_candidates(path) == candidates
    assert read_candidates(tmp_path / "missing.jsonl") == []


def test_cli(tmp_path: Path) -> None:
    cases = _corpus()
    cases_path, split_path, out = tmp_path / "c.jsonl", tmp_path / "s.json", tmp_path / "kb"
    write_cases(cases, cases_path)
    write_manifest(_manifest(cases, TEST_IDS), split_path)
    runner = CliRunner()
    args = ["kb", "build", "--cases", str(cases_path), "--split", str(split_path)]
    result = runner.invoke(app, [*args, "--out-dir", str(out)])
    assert result.exit_code == 0, result.output
    assert "2 个模板候选" in result.output
    assert len(read_candidates(out / "candidates.jsonl")) == 2

    write_cases([*cases, _anchored("99", REINSTALL)], cases_path)
    stale = runner.invoke(app, [*args, "--out-dir", str(out)])
    assert stale.exit_code == 1 and "copilot split" in stale.output
    missing = runner.invoke(app, ["kb", "build", "--cases", str(tmp_path / "none.jsonl")])
    assert missing.exit_code == 1
