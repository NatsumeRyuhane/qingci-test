"""数据切分（D-012）。SYNTHETIC：全部是合成数据。"""

import json
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from copilot.cli import app
from copilot.contracts import Case
from copilot.eval import (
    Split,
    SplitMethod,
    StaleSplitError,
    read_manifest,
    split_cases,
    split_stats,
    write_manifest,
)
from copilot.eval.split import RATIOS, STRATUM_SIZE
from copilot.ingest import write_cases
from tests.cases import case

DUPLICATE = "【合成データ】ログインボーナスが受け取れません。何度試しても同じです。"


def _cases(n: int) -> list[Case]:
    return [case(str(i)) for i in range(1, n + 1)]


def test_stratified_split_is_uniform_in_time() -> None:
    cases = _cases(10 * STRATUM_SIZE)
    manifest = split_cases(cases)
    for start in range(0, len(cases), STRATUM_SIZE):
        stratum = cases[start : start + STRATUM_SIZE]
        counts = Counter(manifest.assignments[c.id] for c in stratum)
        for side, ratio in RATIOS.items():
            assert abs(counts[side] - ratio * STRATUM_SIZE) <= 1, (start, counts)


def test_stratified_split_is_shuffled_within_strata() -> None:
    manifest = split_cases(_cases(STRATUM_SIZE))
    sides = list(manifest.assignments.values())
    # 不是按位置切成三段：test 中的线程分散在层内各处。
    test_positions = [i for i, side in enumerate(sides) if side is Split.TEST]
    assert test_positions[0] < STRATUM_SIZE // 3 and test_positions[-1] > 2 * STRATUM_SIZE // 3


def test_split_is_reproducible_and_independent_of_input_order() -> None:
    cases = _cases(250)
    first = split_cases(cases)
    assert split_cases(list(reversed(cases))) == first
    assert split_cases(cases, seed="other").assignments != first.assignments


def test_chronological_split_keeps_time_order() -> None:
    cases = _cases(100)
    manifest = split_cases(cases, method=SplitMethod.CHRONOLOGICAL)
    sides = [manifest.assignments[c.id] for c in cases]
    assert sides == [Split.TRAIN] * 70 + [Split.DEV] * 15 + [Split.TEST] * 15


def test_chronological_boundaries_use_original_positions() -> None:
    # 重复提交 [1, 20] 整组在 train；其他线程仍按自己在时间线上的位置分配，不被这组推后
    # （边界：14 和 17）。
    cases = _cases(20)
    for i in (1, 20):
        cases[i - 1] = case(str(i), content=DUPLICATE)
    manifest = split_cases(cases, method=SplitMethod.CHRONOLOGICAL)
    sides = [manifest.assignments[c.id] for c in cases]
    assert sides == [Split.TRAIN] * 14 + [Split.DEV] * 3 + [Split.TEST] * 2 + [Split.TRAIN]


@pytest.mark.parametrize("method", list(SplitMethod))
def test_duplicates_stay_on_one_side(method: SplitMethod) -> None:
    cases = _cases(300)
    # 一组跨过按时间切分的边界、也跨过分层边界的重复提交。
    for i in (199, 210, 290):
        cases[i - 1] = case(str(i), content=DUPLICATE)
    manifest = split_cases(cases, method=method)
    sides = {manifest.assignments[i] for i in ("199", "210", "290")}
    assert len(sides) == 1
    if method is SplitMethod.CHRONOLOGICAL:
        assert sides == {Split.TRAIN}  # 按组内最早的线程的位置分配


def test_select_rejects_a_stale_split() -> None:
    cases = _cases(20)
    manifest = split_cases(cases)
    train = manifest.select(cases, Split.TRAIN)
    assert [c.id for c in train] == sorted(manifest.ids(Split.TRAIN), key=int)
    with pytest.raises(StaleSplitError):
        manifest.select([*cases, case("999")], Split.TRAIN)
    with pytest.raises(StaleSplitError):
        manifest.select(cases[1:], Split.TEST)


def test_ratios_are_validated() -> None:
    with pytest.raises(ValueError, match="之和"):
        split_cases(_cases(3), ratios={Split.TRAIN: 0.5, Split.DEV: 0.2, Split.TEST: 0.2})
    with pytest.raises(ValueError, match="大于 0"):
        split_cases(_cases(3), ratios={Split.TRAIN: 1.0, Split.DEV: 0.0, Split.TEST: 0.0})


def test_manifest_round_trip(tmp_path: Path) -> None:
    manifest = split_cases(_cases(30))
    path = tmp_path / "split.json"
    write_manifest(manifest, path)
    assert read_manifest(path) == manifest
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_stats_contain_only_aggregates() -> None:
    cases = _cases(30)
    lines = split_stats(cases, split_cases(cases))
    assert any(line.startswith("test：") for line in lines)
    assert not any("【合成データ】" in line for line in lines)


def test_cli(tmp_path: Path) -> None:
    source, target = tmp_path / "cases.jsonl", tmp_path / "split.json"
    write_cases(_cases(40), source)
    runner = CliRunner()
    args = ["split", "--cases", str(source), "--out", str(target)]
    result = runner.invoke(app, [*args, "--stats"])
    assert result.exit_code == 0, result.output
    assert "train: 28 · dev: 6 · test: 6" in result.output
    assert read_manifest(target).method is SplitMethod.STRATIFIED

    result = runner.invoke(app, [*args, "--method", "chronological"])
    assert result.exit_code == 0
    assert read_manifest(target).method is SplitMethod.CHRONOLOGICAL

    assert runner.invoke(app, [*args, "--method", "random"]).exit_code == 2
    missing = runner.invoke(app, ["split", "--cases", str(tmp_path / "none.jsonl")])
    assert missing.exit_code == 1
