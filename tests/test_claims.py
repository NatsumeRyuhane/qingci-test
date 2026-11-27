"""docs/claims.md 与测试保持一致：引用的每个测试都存在，每条主张至少有一个测试。"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
CLAIMS = ROOT / "docs" / "claims.md"
_REF = re.compile(r"`(tests/[\w/]+\.py)(?:::(\w+))?`")
_ROW = re.compile(r"^\| (C-\d+) \|")


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    }


def test_every_referenced_test_exists() -> None:
    missing = []
    for file, name in _REF.findall(CLAIMS.read_text(encoding="utf-8")):
        path = ROOT / file
        if not path.is_file():
            missing.append(file)
        elif name and name not in _test_names(path):
            missing.append(f"{file}::{name}")
    assert not missing, f"docs/claims.md 引用了不存在的测试：{missing}"


def test_every_claim_names_a_test() -> None:
    rows = [line for line in CLAIMS.read_text(encoding="utf-8").splitlines() if _ROW.match(line)]
    assert rows, "docs/claims.md 中没有主张"
    without = [m.group(1) for line in rows if (m := _ROW.match(line)) and not _REF.search(line)]
    assert not without, f"这些主张没有引用测试：{without}"
    ids = [m.group(1) for line in rows if (m := _ROW.match(line))]
    assert len(ids) == len(set(ids)), "主张编号重复"
