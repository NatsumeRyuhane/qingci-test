"""安全攻击目录重放测试（#7，docs/security.md）。SYNTHETIC：全部是合成数据。"""

import re
from pathlib import Path

import pytest

from copilot.config import LLMTask
from copilot.draft.service import DraftParams
from copilot.eval.security import (
    DEFAULT_SECURITY_DIR,
    load_attack_fixtures,
    render_security_markdown,
    run_security_replay,
)
from copilot.kb.knowledge import load_knowledge
from copilot.llm.fake import FakeLLMClient
from tests.draft.test_service import KNOWLEDGE, SNAPSHOT, analysis_reply, draft_reply

pytestmark = pytest.mark.anyio

SECURITY_DOC = Path(__file__).parents[2] / "docs" / "security.md"


def test_every_security_doc_attack_has_a_synthetic_fixture() -> None:
    """docs/security.md 中登记的每一个 ATK-NN 都在 tests/fixtures/security/ 有合成样本。"""
    doc_ids = set(
        re.findall(r"^\| (ATK-\d{2}) \|", SECURITY_DOC.read_text(encoding="utf-8"), re.MULTILINE)
    )
    fixtures = load_attack_fixtures(DEFAULT_SECURITY_DIR)
    fixture_ids = {f.attack_id for f in fixtures}
    assert doc_ids, "docs/security.md 中没有找到攻击编号"
    assert doc_ids == fixture_ids, f"缺少：{doc_ids - fixture_ids}；多余：{fixture_ids - doc_ids}"
    assert all(f.synthetic and "SYNTHETIC" in f.notice for f in fixtures)


async def test_security_replay_smoke_with_fake_llm() -> None:
    """CI 冒烟测试：不依赖真实模型，验证全部 37 个攻击样本都能完成重放并输出报告。"""
    knowledge = load_knowledge(KNOWLEDGE)
    fixtures = load_attack_fixtures(DEFAULT_SECURITY_DIR)
    e2e_count = sum(1 for f in fixtures if f.kind == "draft_e2e")

    fake = FakeLLMClient()
    for f in fixtures:
        if f.kind != "draft_e2e":
            continue
        fake.queue(
            LLMTask.ANALYZE,
            analysis_reply(
                injection_suspected=True,
                injection_reason_zh="【合成】测试注入",
            ),
        )
        if f.use_tools:
            import json

            fake.queue(
                LLMTask.DRAFT,
                json.dumps(
                    {"tool": None, "arguments": {}, "final": json.loads(draft_reply())},
                    ensure_ascii=False,
                ),
            )
        else:
            fake.queue(LLMTask.DRAFT, draft_reply())
        fake.queue(LLMTask.BACKTRANSLATE, "【合成】回译")

    results = await run_security_replay(
        fixtures,
        snapshot=SNAPSHOT,
        knowledge=knowledge,
        llm=fake,
        params=DraftParams(analyze_model="fake", draft_model="fake"),
        concurrency=1,
        run_judge=False,
    )
    assert len(results) == len(fixtures)
    e2e_results = [r for r in results if r.kind == "draft_e2e"]
    assert len(e2e_results) == e2e_count
    assert all(not r.succeeded for r in e2e_results)

    # L1 已知防御的 ATK-01..03 必须被 L1 拦截
    by_id = {r.attack_id: r for r in results}
    for atk in ("ATK-01", "ATK-02", "ATK-03", "ATK-10", "ATK-11", "ATK-12", "ATK-60", "ATK-61"):
        assert not by_id[atk].succeeded, by_id[atk]
    assert by_id["ATK-62"].first_blocking_layer == "not_implemented"
    assert by_id["ATK-63"].first_blocking_layer == "not_implemented"

    md = render_security_markdown(results)
    assert "安全攻击目录端到端重放报告" in md
    assert "ATK-20" in md
