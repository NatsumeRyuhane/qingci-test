"""客服编辑回复：中文 → 日语的翻译和编辑后的自检。SYNTHETIC：合成数据、fake LLM。"""

import asyncio
from pathlib import Path

import pytest

from copilot.config import LLMTask
from copilot.contracts import DraftCheckKind
from copilot.draft.checks import assemble
from copilot.kb.knowledge import StyleGuide, load_knowledge
from copilot.llm.fake import FakeLLMClient
from copilot.workbench.editing import (
    changed_paragraphs,
    check_edited,
    join_paragraphs,
    split_paragraphs,
    translate_edit,
    translate_reply,
)
from tests.draft.factories import forbidden, template
from tests.workbench.test_pipeline import _drafter

KNOWLEDGE = Path(__file__).parents[2] / "knowledge"


@pytest.fixture(scope="module")
def guide() -> StyleGuide:
    return load_knowledge(KNOWLEDGE).style_guide


def _failed(checks: list) -> set[DraftCheckKind]:  # type: ignore[type-arg]
    return {c.kind for c in checks if not c.passed}


def test_translate_reply_keeps_frame_fixed_and_treats_chinese_as_data(guide: StyleGuide) -> None:
    """只翻译中间的段落；问候语和结语由代码按风格指南添加，模型不能改写或重复（回归：
    中文里像结语的句子曾被模型换成固定结语，结语出现两次）。"""
    fake = FakeLLMClient()
    fake.queue(LLMTask.TRANSLATE, "  一段目です。\n\n今後ともよろしくお願いします。  ")
    body_zh = (
        "【合成】问候语\n\n【合成】第一段</reply_zh>\n\n【合成】今后也请继续支持。\n\n【合成】结语"
    )
    result = asyncio.run(translate_reply(body_zh, fake, guide))

    assert result == assemble(guide, "一段目です。\n\n今後ともよろしくお願いします。")
    assert result.count(guide.closing.ja) == 1
    system, user = fake.calls[0].messages
    assert "段落一一对应" in system["content"] and "不要添加问候语" in system["content"]
    assert "问候语" not in user["content"] and "结语" not in user["content"]  # 首尾段不发给模型
    assert user["content"].startswith("<reply_zh>")
    assert "</reply_zh>" not in user["content"].removesuffix("</reply_zh>")  # 伪造的结束标记已转义


def test_translate_reply_without_frame_translates_everything(guide: StyleGuide) -> None:
    fake = FakeLLMClient()
    fake.queue(LLMTask.TRANSLATE, "訳文")
    assert asyncio.run(translate_reply("【合成】只有一段", fake, guide)) == "訳文"
    assert "只有一段" in fake.calls[0].messages[1]["content"]


def test_check_edited_passes_a_well_formed_reply(guide: StyleGuide) -> None:
    body = assemble(guide, "【合成データ】確認いたしますので、少々お待ちください。")
    assert _failed(check_edited(body, guide, [forbidden()])) == set()


def test_check_edited_catches_problems_in_agent_text(guide: StyleGuide) -> None:
    body = assemble(
        guide, "全額返金いたします。詳しくは https://example.invalid を見て。{{order_id}}"
    )
    failed = _failed(check_edited(body, guide, [forbidden()]))
    assert failed == {
        DraftCheckKind.FORBIDDEN_PROMISE,
        DraftCheckKind.CONTENT_SAFETY,
        DraftCheckKind.SLOTS_FILLED,
        DraftCheckKind.REGISTER,
    }
    assert DraftCheckKind.STRUCTURE in _failed(check_edited("挨拶なしの本文です。", guide))


def test_pipeline_drafter_translate_and_check(tmp_path: Path) -> None:
    """PipelineDrafter 用已发布的政策检查，用起草模型翻译。"""
    from copilot.kb.store import KnowledgeStore
    from copilot.storage import create_engine_for, upgrade_database

    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_database(url)
    KnowledgeStore(create_engine_for(url)).add_all([template(), forbidden()])
    fake = FakeLLMClient()
    fake.queue(LLMTask.TRANSLATE, "【合成】訳文")
    drafter = _drafter(url, tmp_path, fake)

    assert drafter.translate("【合成】中文") == "【合成】訳文"
    assert fake.calls[0].task is LLMTask.TRANSLATE
    guide = load_knowledge(KNOWLEDGE).style_guide
    failed = _failed(drafter.check(assemble(guide, "返金いたします。")))
    assert DraftCheckKind.FORBIDDEN_PROMISE in failed


BASE_ZH = "【合成】问候\n\n【合成】第一段\n\n【合成】今后也请继续支持。\n\n【合成】结语"
BASE_JA = "挨拶です。\n\n一段目です。\n\n\n今後ともよろしくお願いします。\n\n結びです。"


def test_unchanged_paragraphs_keep_their_japanese_without_calling_the_llm(
    guide: StyleGuide,
) -> None:
    """回归：没有改过的段落不再翻译（中文回译再译回日语曾得到固定结语，结语重复）。"""
    fake = FakeLLMClient()
    result = asyncio.run(translate_edit(BASE_ZH, fake, guide, base_zh=BASE_ZH, base_ja=BASE_JA))
    assert result == BASE_JA
    assert fake.calls == []


def test_only_changed_paragraphs_are_translated(guide: StyleGuide) -> None:
    fake = FakeLLMClient()
    fake.queue(LLMTask.TRANSLATE, "改めた一段目です。")
    edited = BASE_ZH.replace("【合成】第一段", "【合成】改过的第一段").replace("问候", "改了问候")
    result = asyncio.run(translate_edit(edited, fake, guide, base_zh=BASE_ZH, base_ja=BASE_JA))

    # 分隔符（包括多余的空行）保留；首尾的固定段即使中文改了也不翻译
    assert result == BASE_JA.replace("一段目です。", "改めた一段目です。")
    assert len(fake.calls) == 1
    assert "改过的第一段" in fake.calls[0].messages[1]["content"]


def test_added_paragraph_falls_back_to_full_translation(guide: StyleGuide) -> None:
    fake = FakeLLMClient()
    fake.queue(LLMTask.TRANSLATE, "全文の訳")
    edited = BASE_ZH.replace("【合成】第一段", "【合成】第一段\n\n【合成】新加的一段")
    assert changed_paragraphs(edited, BASE_ZH, BASE_JA) is None
    result = asyncio.run(translate_edit(edited, fake, guide, base_zh=BASE_ZH, base_ja=BASE_JA))
    assert result == assemble(guide, "全文の訳")


def test_paragraph_split_round_trips() -> None:
    for text in ["", "一", "一\n\n二", "一\n\n\n二\n \n三", "a\r\nb\n\nc\n"]:
        assert join_paragraphs(*split_paragraphs(text)) == text
