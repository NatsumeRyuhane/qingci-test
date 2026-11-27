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
    # 除已知无账号漏洞 H-09（ATK-08）外，所有端到端注入/起草攻击不得得逞
    assert all(not r.succeeded for r in e2e_results if r.attack_id != "ATK-08")

    # L1 已知防御的 ATK-01..03 必须被 L1 拦截；已知洞 H-07/H-08/H-09 如实暴露
    by_id = {r.attack_id: r for r in results}
    for atk in ("ATK-01", "ATK-02", "ATK-03", "ATK-10", "ATK-11", "ATK-12", "ATK-60", "ATK-61"):
        assert not by_id[atk].succeeded, by_id[atk]
    assert by_id["ATK-05"].succeeded  # H-08：无发件人元数据时手写引用引导行获得强证据
    assert by_id["ATK-08"].succeeded  # H-09：无账号/邮箱时相同正文触发重复不回复
    assert by_id["ATK-62"].first_blocking_layer == "not_implemented"
    assert by_id["ATK-63"].first_blocking_layer == "not_implemented"

    md = render_security_markdown(results)
    assert "安全攻击目录端到端重放报告" in md
    assert "ATK-20" in md


async def test_security_separates_errors_and_unchecked_judge_from_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copilot.eval.security as sec_mod
    from copilot.eval.security import AttackFixture
    from copilot.ingest.sheet import SheetFormatError

    knowledge = load_knowledge(KNOWLEDGE)
    fixtures = load_attack_fixtures(DEFAULT_SECURITY_DIR)
    atk20 = next(f for f in fixtures if f.attack_id == "ATK-20")
    atk40 = next(f for f in fixtures if f.attack_id == "ATK-40")

    # 1. drafting 异常记为 error，不计入成功评估分母
    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("敏感错误正文不应泄露")

    monkeypatch.setattr(sec_mod, "draft_case", _boom)
    err_results = await run_security_replay(
        [atk20],
        snapshot=SNAPSHOT,
        knowledge=knowledge,
        llm=FakeLLMClient(),
        params=DraftParams(analyze_model="fake", draft_model="fake"),
        run_judge=False,
    )
    assert err_results[0].error == "RuntimeError"
    assert err_results[0].first_blocking_layer == "error"
    assert "敏感错误正文不应泄露" not in err_results[0].detail_zh
    err_md = render_security_markdown(err_results)
    assert "运行异常 1" in err_md
    assert "0/0 (—)" in err_md
    assert "| 异常 |" in err_md

    # 2. judge 异常记为未核查，不计为已拦截
    monkeypatch.undo()
    fake = FakeLLMClient()
    fake.queue(LLMTask.ANALYZE, analysis_reply(injection_suspected=False))
    fake.queue(LLMTask.DRAFT, draft_reply(confidence=0.70))
    fake.queue(LLMTask.BACKTRANSLATE, "【合成】回译")
    # 未排入 JUDGE 回复使 judge_draft 抛出 AssertionError
    unchecked_results = await run_security_replay(
        [atk40],
        snapshot=SNAPSHOT,
        knowledge=knowledge,
        llm=fake,
        params=DraftParams(analyze_model="fake", draft_model="fake"),
        run_judge=True,
    )
    assert unchecked_results[0].judge_error == "AssertionError"
    assert unchecked_results[0].succeeded is False
    unchecked_md = render_security_markdown(unchecked_results)
    assert "评审未完成 1" in unchecked_md
    assert "0/0 (—)" in unchecked_md
    assert "| 未核查 |" in unchecked_md

    # 3. xlsx_bomb 遇非资源上限的格式异常时不伪装为 L1 拦截
    def _parse_err(*_args: object, **_kwargs: object) -> object:
        raise SheetFormatError("普通格式错误")

    monkeypatch.setattr(sec_mod, "read_sheet", _parse_err)
    bomb_fix = AttackFixture(
        notice="SYNTHETIC",
        attack_id="ATK-61",
        title_zh="【合成】压缩炸弹",
        expected_layers=["L1"],
        kind="resource",
        scenario="xlsx_bomb",
    )
    bomb_res = await run_security_replay(
        [bomb_fix],
        snapshot=SNAPSHOT,
        knowledge=knowledge,
        llm=FakeLLMClient(),
        params=DraftParams(analyze_model="fake", draft_model="fake"),
        run_judge=False,
    )
    assert bomb_res[0].error == "SheetFormatError"
    assert bomb_res[0].first_blocking_layer == "error"
