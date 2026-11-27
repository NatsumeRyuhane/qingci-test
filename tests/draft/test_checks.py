"""草稿的组装与代码自检（#6）。SYNTHETIC：全部是合成数据。"""

from pathlib import Path

import pytest

from copilot.contracts import DraftCheckKind, EntryType
from copilot.draft.checks import (
    CheckContext,
    CitedClaim,
    ComposedDraft,
    assemble,
    fill_slots,
    run_checks,
    sentences,
)
from copilot.draft.retrieval import Candidate
from copilot.draft.timectx import WindowMatch
from copilot.kb.knowledge import StyleGuide, load_knowledge
from tests.draft.factories import forbidden, template

KNOWLEDGE = Path(__file__).parents[2] / "knowledge"
TEMPLATE_BODY = "【合成データ】ご不便をおかけしております。現在調査を進めております。"


@pytest.fixture(scope="module")
def guide() -> StyleGuide:
    return load_knowledge(KNOWLEDGE).style_guide


def candidate(
    body: str = TEMPLATE_BODY, *, window: WindowMatch = WindowMatch.INSIDE, time_bound: bool = False
) -> Candidate:
    return Candidate(
        template=template("tpl-syn", body=body, slots=sorted(set(_slots(body)))),
        time_bound=time_bound,
        window=window,
    )


def _slots(body: str) -> list[str]:
    from copilot.contracts import PLACEHOLDER_PATTERN

    return PLACEHOLDER_PATTERN.findall(body)


def compose(
    guide: StyleGuide,
    core: str,
    *,
    chosen: Candidate | None = None,
    template_text: str | None = None,
    citations: list[CitedClaim] | None = None,
    requests: list[str] | None = None,
) -> ComposedDraft:
    chosen = chosen if chosen is not None else candidate()
    return ComposedDraft(
        body_ja=assemble(guide, core),
        core_ja=core,
        candidate=chosen,
        template_text_ja=chosen.template.body_ja if template_text is None else template_text,
        citations=citations or [],
        info_requests_ja=requests or [],
    )


def results(draft: ComposedDraft, context: CheckContext) -> dict[DraftCheckKind, str | None]:
    """未通过的检查 → 说明。"""
    return {c.kind: c.detail_zh for c in run_checks(draft, context) if not c.passed}


def ctx(guide: StyleGuide, **values: object) -> CheckContext:
    return CheckContext(guide=guide, forbidden_promises=[forbidden()], **values)  # type: ignore[arg-type]


def test_template_only_draft_passes_everything(guide: StyleGuide) -> None:
    draft = compose(guide, TEMPLATE_BODY)
    assert results(draft, ctx(guide)) == {}
    checks = run_checks(draft, ctx(guide))
    assert [c.kind for c in checks] == [
        DraftCheckKind.STRUCTURE,
        DraftCheckKind.REGISTER,
        DraftCheckKind.FORBIDDEN_PROMISE,
        DraftCheckKind.TEMPLATE_WINDOW,
        DraftCheckKind.SLOTS_FILLED,
        DraftCheckKind.CITATIONS,
        DraftCheckKind.CONTENT_SAFETY,
    ]


def test_assemble_order_and_no_duplicate_phrases(guide: StyleGuide) -> None:
    apology = guide.apologies[0].ja
    contact = guide.contact_again[0].ja
    body = assemble(guide, "【合成】答复。", apology_ja=apology, contact_again_ja=contact)
    assert body.index(guide.greeting.ja) < body.index(apology) < body.index("【合成】答复")
    assert body.index("【合成】答复") < body.index(contact) < body.index(guide.closing.ja)
    assert body.endswith(guide.notice.ja)
    # 答复中已有这句致歉时不重复添加。
    again = assemble(guide, f"{apology}\n【合成】答复。", apology_ja=apology)
    assert again.count(apology) == 1


def test_structure_detects_missing_or_repeated_frame(guide: StyleGuide) -> None:
    draft = ComposedDraft(body_ja=f"【合成】{TEMPLATE_BODY}", core_ja=TEMPLATE_BODY, candidate=None)
    detail = results(draft, ctx(guide))[DraftCheckKind.STRUCTURE]
    assert detail is not None and "问候语" in detail and "结语" in detail
    doubled = compose(guide, f"{guide.greeting.ja}\n{TEMPLATE_BODY}")
    assert "重复" in (results(doubled, ctx(guide))[DraftCheckKind.STRUCTURE] or "")
    empty = compose(guide, " ")
    assert "为空" in (results(empty, ctx(guide))[DraftCheckKind.STRUCTURE] or "")


def test_register_flags_plain_form_added_sentence(guide: StyleGuide) -> None:
    core = f"{TEMPLATE_BODY}\n【合成】すぐに直る。"
    failed = results(compose(guide, core), ctx(guide))
    assert DraftCheckKind.REGISTER in failed
    polite = compose(
        guide, f"{TEMPLATE_BODY}\n【合成】ご確認ください。", requests=["ご確認ください"]
    )
    assert DraftCheckKind.REGISTER not in results(polite, ctx(guide))
    # 没有句末标点的列表项不检查。
    listed = compose(guide, f"{TEMPLATE_BODY}\n・購入日時", requests=["購入日時"])
    assert DraftCheckKind.REGISTER not in results(listed, ctx(guide))


def test_forbidden_promise_in_added_text_fails(guide: StyleGuide) -> None:
    draft = compose(guide, f"{TEMPLATE_BODY}\n【合成】全額を返金いたします。")
    detail = results(draft, ctx(guide))[DraftCheckKind.FORBIDDEN_PROMISE]
    assert detail is not None and "返金いたします" in detail


def test_forbidden_phrase_inside_approved_template_is_noted_not_failed(guide: StyleGuide) -> None:
    body = "【合成データ】対象のお客様には返金いたします。"
    draft = compose(guide, body, chosen=candidate(body))
    checks = {c.kind: c for c in run_checks(draft, ctx(guide))}
    assert checks[DraftCheckKind.FORBIDDEN_PROMISE].passed
    assert "模板原文" in (checks[DraftCheckKind.FORBIDDEN_PROMISE].detail_zh or "")


def test_rewritten_template_is_checked_as_added_text(guide: StyleGuide) -> None:
    body = "【合成データ】対象のお客様には返金いたします。"
    draft = compose(
        guide, "【合成データ】対象のお客様には全員に返金いたします。", chosen=candidate(body)
    )
    assert DraftCheckKind.FORBIDDEN_PROMISE in results(draft, ctx(guide))


@pytest.mark.parametrize(
    ("window", "time_bound", "passed", "text"),
    [
        (WindowMatch.INSIDE, True, True, "匹配"),
        (WindowMatch.UNCERTAIN, True, False, "无法确定"),
        (WindowMatch.UNCERTAIN, False, False, "无法确定"),
        (WindowMatch.OUTSIDE, True, False, "不适用"),
    ],
)
def test_template_window(
    guide: StyleGuide, window: WindowMatch, time_bound: bool, passed: bool, text: str
) -> None:
    draft = compose(guide, TEMPLATE_BODY, chosen=candidate(window=window, time_bound=time_bound))
    check = next(
        c for c in run_checks(draft, ctx(guide)) if c.kind is DraftCheckKind.TEMPLATE_WINDOW
    )
    assert check.passed is passed
    assert text in (check.detail_zh or "")


def test_no_template_passes_window(guide: StyleGuide) -> None:
    draft = ComposedDraft(
        body_ja=assemble(guide, "【合成】ご連絡ありがとうございます。"),
        core_ja="【合成】ご連絡ありがとうございます。",
        candidate=None,
        info_requests_ja=["【合成】ご連絡ありがとうございます。"],
    )
    assert DraftCheckKind.TEMPLATE_WINDOW not in results(draft, ctx(guide))


def test_slots(guide: StyleGuide) -> None:
    body = "【合成データ】{{item}}の件について確認いたします。"
    filled = fill_slots(body, {"item": "ジェム"})
    assert filled == "【合成データ】ジェムの件について確認いたします。"
    ok = compose(guide, filled, chosen=candidate(body), template_text=filled)
    assert DraftCheckKind.SLOTS_FILLED not in results(ok, ctx(guide))
    unfilled = fill_slots(body, {"item": "  "})
    bad = compose(guide, unfilled, chosen=candidate(body), template_text=unfilled)
    assert "item" in (results(bad, ctx(guide))[DraftCheckKind.SLOTS_FILLED] or "")


def test_uncited_added_claim_fails_and_citation_fixes_it(guide: StyleGuide) -> None:
    claim = "【合成】この不具合はバージョン9.9で修正されました。"
    core = f"{TEMPLATE_BODY}\n{claim}"
    assert DraftCheckKind.CITATIONS in results(compose(guide, core), ctx(guide))
    cite = CitedClaim(EntryType.NOTICE, "syn-fix", 1, "已在 9.9 修复", sentence_ja=claim)
    provided = frozenset({(EntryType.NOTICE, "syn-fix", 1)})
    draft = compose(guide, core, citations=[cite])
    assert DraftCheckKind.CITATIONS not in results(draft, ctx(guide, provided_entries=provided))


def test_citation_of_unprovided_entry_fails(guide: StyleGuide) -> None:
    cite = CitedClaim(EntryType.FACT, "syn-secret", 3, "【合成】")
    draft = compose(guide, TEMPLATE_BODY, citations=[cite])
    detail = results(draft, ctx(guide))[DraftCheckKind.CITATIONS]
    assert detail is not None and "fact:syn-secret@3" in detail


def test_info_request_needs_no_citation(guide: StyleGuide) -> None:
    ask = "【合成】お手数ですが、購入日時をお知らせください。"
    draft = compose(guide, f"{TEMPLATE_BODY}\n{ask}", requests=[ask])
    assert results(draft, ctx(guide)) == {}


@pytest.mark.parametrize(
    "added",
    [
        "【合成】詳しくは https://evil.example をご覧ください。",
        "【合成】syn@example.com までご連絡ください。",
        "【合成】<script>alert(1)</script>",
        "【合成】＜img src=x＞",
        "【合成】ご確認ください\u202e。",
    ],
)
def test_content_safety(guide: StyleGuide, added: str) -> None:
    draft = compose(guide, f"{TEMPLATE_BODY}\n{added}", requests=[added])
    assert DraftCheckKind.CONTENT_SAFETY in results(draft, ctx(guide))


def test_url_inside_approved_template_is_allowed(guide: StyleGuide) -> None:
    body = "【合成データ】詳細は https://example.invalid/help をご確認ください。"
    draft = compose(guide, body, chosen=candidate(body))
    assert DraftCheckKind.CONTENT_SAFETY not in results(draft, ctx(guide))


def test_other_case_text_is_detected(guide: StyleGuide) -> None:
    other = "【合成】別のお客様のアカウントで購入したジェムが消えてしまいました"
    draft = compose(guide, f"{TEMPLATE_BODY}\n{other}。", requests=[other])
    detail = results(draft, ctx(guide, foreign_texts=[other]))[DraftCheckKind.CONTENT_SAFETY]
    assert detail is not None and "其他案件" in detail


def test_sentences_split() -> None:
    assert sentences("あ。い！\nう？ え") == ["あ。", "い！", "う？", "え"]
