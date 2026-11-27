"""契约模型的往返测试、结构约束测试与校验规则测试。数据全部是合成的（tests/fixtures/）。"""

import typing
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from copilot.contracts import (
    EXPORTED_MODELS,
    Case,
    CaseAnalysis,
    CaseStatus,
    Contract,
    Document,
    Draft,
    DraftMode,
    Fact,
    Feedback,
    GlossaryEntry,
    InquiryForm,
    Intent,
    Message,
    Notice,
    OutboundMail,
    Policy,
    ReplyTemplate,
    is_valid_case_id,
)
from tests.fixtures import load_items, load_models

FIXTURES: list[tuple[type[Contract], str]] = [
    (Message, "message"),
    (InquiryForm, "inquiry_form"),
    (Case, "case"),
    (ReplyTemplate, "reply_template"),
    (Intent, "intent"),
    (Fact, "fact"),
    (Policy, "policy"),
    (GlossaryEntry, "glossary_entry"),
    (Notice, "notice"),
    (CaseAnalysis, "case_analysis"),
    (Draft, "draft"),
    (Feedback, "feedback"),
    (OutboundMail, "outbound_mail"),
]


def _all_contract_models() -> set[type[Contract]]:
    """`EXPORTED_MODELS` 及其嵌套引用到的所有契约模型。"""
    found: set[type[Contract]] = set()

    def visit(tp: Any) -> None:
        if isinstance(tp, type) and issubclass(tp, Contract):
            if tp in found:
                return
            found.add(tp)
            for field in tp.model_fields.values():
                visit(field.annotation)
            return
        for arg in typing.get_args(tp):
            visit(arg)

    for model in EXPORTED_MODELS:
        visit(model)
    return found


def test_every_exported_model_has_a_fixture() -> None:
    assert {model for model, _ in FIXTURES} == set(EXPORTED_MODELS)


@pytest.mark.parametrize(("model", "name"), FIXTURES, ids=[name for _, name in FIXTURES])
def test_roundtrip(model: type[BaseModel], name: str) -> None:
    """模型 → JSON → 模型，前后相等；再序列化一次，JSON 也相同。"""
    items = load_models(model, name)
    assert items, f"{name}.json 没有数据"
    for original in items:
        json_text = original.model_dump_json()
        restored = model.model_validate_json(json_text)
        assert restored == original
        assert restored.model_dump_json() == json_text


@pytest.mark.parametrize("model", sorted(_all_contract_models(), key=lambda m: m.__name__))
def test_every_field_is_documented(model: type[Contract]) -> None:
    """每个字段都有中文 docstring，它会成为 JSON Schema 中的 description。"""
    undocumented = [name for name, field in model.model_fields.items() if not field.description]
    assert not undocumented, f"{model.__name__} 的字段缺少 docstring：{undocumented}"
    assert model.__doc__, f"{model.__name__} 缺少 docstring"


@pytest.mark.parametrize("model", [m for m in EXPORTED_MODELS if issubclass(m, Document)])
def test_documents_carry_schema_version(model: type[Document]) -> None:
    field = model.model_fields["schema_version"]
    assert typing.get_origin(field.annotation) is typing.Literal
    assert typing.get_args(field.annotation) == (field.default,)


def test_persisted_models_are_documents() -> None:
    """持久化和导出的顶层对象带 schema_version；嵌入其中的模型不需要。"""
    documents = {m for m in EXPORTED_MODELS if issubclass(m, Document)}
    assert documents == {
        Case,
        ReplyTemplate,
        Intent,
        Fact,
        Policy,
        GlossaryEntry,
        Notice,
        CaseAnalysis,
        Draft,
        Feedback,
        OutboundMail,
    }


def test_unknown_fields_are_rejected() -> None:
    item = load_items("message")[0] | {"unexpected": 1}
    with pytest.raises(ValidationError, match="unexpected"):
        Message.model_validate(item)


def test_other_schema_version_is_rejected() -> None:
    item = load_items("case")[0] | {"schema_version": 1}
    with pytest.raises(ValidationError, match="schema_version"):
        Case.model_validate(item)


def test_reserved_access_control_fields_exist() -> None:
    """为 #13 预留的字段存在，且默认值不限制任何人。"""
    minimal = Case.model_validate(load_items("case")[1])
    assert minimal.queue is None
    assert minimal.sensitivity_tags == []
    assert minimal.assigned_to is None
    for name in ("queue", "sensitivity_tags", "assigned_to"):
        assert "预留（#13）" in (Case.model_fields[name].description or "")


def test_message_id() -> None:
    message = load_models(Message, "message")[0]
    assert message.id == "SYN-0001:1"
    assert "id" not in message.model_dump()


def test_message_core_strips_greeting_and_closing() -> None:
    agent = next(m for m in load_models(Message, "message") if m.greeting)
    assert agent.core
    assert not agent.core.startswith("いつもご利用")
    assert "今後とも" not in agent.core
    assert "core" not in agent.model_dump()
    player = load_models(Message, "message")[0]
    assert player.core == player.body.strip()


@pytest.mark.parametrize("field", ["greeting", "closing"])
def test_message_frame_must_be_part_of_body(field: str) -> None:
    item = next(m for m in load_items("message") if m.get("greeting"))
    item[field] = "【合成データ】本文にない文"
    with pytest.raises(ValidationError, match=field):
        Message.model_validate(item)


# --- 校验规则 ---


def test_case_rejects_message_from_other_thread() -> None:
    item = load_items("case")[0]
    item["messages"][1]["thread_id"] = "SYN-9999"
    with pytest.raises(ValidationError, match="thread_id"):
        Case.model_validate(item)


def test_case_rejects_unordered_messages() -> None:
    item = load_items("case")[0]
    item["messages"].reverse()
    with pytest.raises(ValidationError, match="严格递增"):
        Case.model_validate(item)


@pytest.mark.parametrize(
    "case_id",
    ["../SYN", "SYN/0001", "SYN 0001", "SYN\n0001", "SYN-0001\n", "ＳＹＮ", "", "S" * 65],
    ids=[
        "traversal",
        "slash",
        "space",
        "newline",
        "trailing-newline",
        "fullwidth",
        "empty",
        "long",
    ],
)
def test_case_id_rejects_unsafe_characters(case_id: str) -> None:
    item = load_items("case")[1]
    item["id"] = case_id
    for message in item["messages"]:
        message["thread_id"] = case_id
    with pytest.raises(ValidationError):
        Case.model_validate(item)
    assert not is_valid_case_id(case_id)


@pytest.mark.parametrize("case_id", ["SYN-0001", "1234", "a_b-C9", "9" * 64])
def test_case_id_accepts_safe_ids(case_id: str) -> None:
    assert is_valid_case_id(case_id)


def test_message_ids_follow_case_id_rules() -> None:
    item = load_items("reply_template")[0]
    item["source_message_ids"] = ["../SYN:1"]
    with pytest.raises(ValidationError, match="source_message_ids"):
        ReplyTemplate.model_validate(item)


def test_case_requires_messages() -> None:
    item = load_items("case")[1] | {"messages": []}
    with pytest.raises(ValidationError):
        Case.model_validate(item)


def test_naive_datetime_is_rejected() -> None:
    item = load_items("message")[0] | {"sent_at": "2024-03-10T21:30:00"}
    with pytest.raises(ValidationError, match="timezone"):
        Message.model_validate(item)


def test_template_slots_must_match_placeholders() -> None:
    item = load_items("reply_template")[0]
    item["slots"] = item["slots"][:1]
    with pytest.raises(ValidationError, match="占位符"):
        ReplyTemplate.model_validate(item)


def test_template_rejects_duplicate_slots() -> None:
    item = load_items("reply_template")[0]
    item["slots"].append(item["slots"][0])
    with pytest.raises(ValidationError, match="重复"):
        ReplyTemplate.model_validate(item)


def test_template_validity_window_must_be_ordered() -> None:
    item = load_items("reply_template")[1]
    item["valid_from"], item["valid_to"] = item["valid_to"], item["valid_from"]
    with pytest.raises(ValidationError, match="valid_from"):
        ReplyTemplate.model_validate(item)


def test_only_low_risk_templates_can_auto_send() -> None:
    item = load_items("reply_template")[2] | {"risk": "normal"}
    with pytest.raises(ValidationError, match="自动发送"):
        ReplyTemplate.model_validate(item)


def test_draft_cannot_auto_send_with_failed_check() -> None:
    item = load_items("draft")[1]
    item["checks"][0]["passed"] = False
    with pytest.raises(ValidationError, match="不能自动发送"):
        Draft.model_validate(item)


def test_draft_requires_route_reason() -> None:
    item = load_items("draft")[0] | {"route_reasons": []}
    with pytest.raises(ValidationError, match="route_reasons"):
        Draft.model_validate(item)


def test_draft_confidence_range() -> None:
    item = load_items("draft")[0] | {"confidence": 1.5}
    with pytest.raises(ValidationError, match="confidence"):
        Draft.model_validate(item)


def test_draft_id_defaults_to_random() -> None:
    item = {k: v for k, v in load_items("draft")[0].items() if k != "id"}
    first, second = Draft.model_validate(item), Draft.model_validate(item)
    assert first.id and first.id != second.id


def test_edited_feedback_requires_final_body() -> None:
    item = load_items("feedback")[1] | {"final_body_ja": None}
    with pytest.raises(ValidationError, match="final_body_ja"):
        Feedback.model_validate(item)


@pytest.mark.parametrize(("index", "distance"), [(0, 3), (1, 0)], ids=["approved", "edited"])
def test_feedback_edit_distance_matches_action(index: int, distance: int) -> None:
    item = load_items("feedback")[index] | {"edit_distance": distance}
    with pytest.raises(ValidationError, match="edit_distance"):
        Feedback.model_validate(item)


# --- 测试数据之间的引用 ---


def test_fixture_cross_references() -> None:
    drafts = {draft.id: draft for draft in load_models(Draft, "draft")}
    templates = {(t.id, t.version) for t in load_models(ReplyTemplate, "reply_template")}
    messages = {m.id for case in load_models(Case, "case") for m in case.messages}

    for feedback in load_models(Feedback, "feedback"):
        assert feedback.draft_id in drafts
    for mail in load_models(OutboundMail, "outbound_mail"):
        assert drafts[mail.draft_id].case_id == mail.case_id
        assert mail.message_seq > drafts[mail.draft_id].message_seq
    for draft in drafts.values():
        for ref in draft.template_refs:
            assert (ref.template_id, ref.template_version) in templates
    intents = {intent.id for intent in load_models(Intent, "intent")}
    for entry in [
        *load_models(ReplyTemplate, "reply_template"),
        *load_models(Fact, "fact"),
        *load_models(Policy, "policy"),
        *load_models(Notice, "notice"),
    ]:
        used = [entry.intent] if isinstance(entry, ReplyTemplate) else entry.intents
        assert set(used) <= intents, f"{entry.id} 引用了不存在的意图"
    entries = {
        "template": templates,
        "intent": {(e.id, e.version) for e in load_models(Intent, "intent")},
        "fact": {(e.id, e.version) for e in load_models(Fact, "fact")},
        "policy": {(e.id, e.version) for e in load_models(Policy, "policy")},
        "glossary": {(e.id, e.version) for e in load_models(GlossaryEntry, "glossary_entry")},
        "notice": {(e.id, e.version) for e in load_models(Notice, "notice")},
    }
    for draft in drafts.values():
        for citation in draft.citations:
            assert (citation.entry_id, citation.entry_version) in entries[citation.entry_type]
    template_ids = {template_id for template_id, _ in templates}
    for notice in load_models(Notice, "notice"):
        assert set(notice.template_ids) <= template_ids
    for analysis in load_models(CaseAnalysis, "case_analysis"):
        assert f"{analysis.case_id}:{analysis.message_seq}" in messages
    for draft in drafts.values():
        assert f"{draft.case_id}:{draft.message_seq}" in messages


# --- 不回复与分析附加字段 ---


def test_no_reply_proposal_roundtrip() -> None:
    analysis = load_models(CaseAnalysis, "case_analysis")[1]
    assert analysis.no_reply is not None
    assert analysis.no_reply.duplicate_of == "SYN-0003"
    assert analysis.thread_judgement is CaseStatus.NO_REPLY_NEEDED


@pytest.mark.parametrize(
    "no_reply",
    [
        {"reason": "duplicate", "reason_zh": "重复"},
        {"reason": "system_message", "reason_zh": "退信", "duplicate_of": "SYN-0003"},
    ],
    ids=["duplicate-without-target", "target-without-duplicate"],
)
def test_duplicate_of_goes_with_duplicate_reason(no_reply: dict[str, str]) -> None:
    item = load_items("case_analysis")[1] | {"no_reply": no_reply}
    with pytest.raises(ValidationError, match="duplicate_of"):
        CaseAnalysis.model_validate(item)


def test_case_is_not_its_own_duplicate() -> None:
    item = load_items("case_analysis")[1]
    item["no_reply"]["duplicate_of"] = item["case_id"]
    with pytest.raises(ValidationError, match="自己的重复"):
        CaseAnalysis.model_validate(item)


def test_analysis_intent_confidence_range() -> None:
    item = load_items("case_analysis")[0] | {"intent_confidence": 1.2}
    with pytest.raises(ValidationError, match="intent_confidence"):
        CaseAnalysis.model_validate(item)


@pytest.mark.parametrize("status", ["drafted", "escalated", "undetermined"])
def test_analysis_thread_judgement_rejects_other_statuses(status: str) -> None:
    item = load_items("case_analysis")[0] | {"thread_judgement": status}
    with pytest.raises(ValidationError, match="thread_judgement"):
        CaseAnalysis.model_validate(item)


def test_analysis_optional_fields_default_when_omitted() -> None:
    item = {
        k: v
        for k, v in load_items("case_analysis")[0].items()
        if k
        not in {
            "intent_confidence",
            "thread_judgement",
            "thread_judgement_reason_zh",
            "injection_signals",
        }
    }
    analysis = CaseAnalysis.model_validate(item)
    assert analysis.intent_confidence is None
    assert analysis.thread_judgement is None
    assert analysis.thread_judgement_reason_zh is None
    assert analysis.injection_signals == []


# --- 草稿的引用、轨迹与时间上下文 ---


def test_draft_records_citations_trace_and_time() -> None:
    draft = load_models(Draft, "draft")[0]
    assert draft.mode is DraftMode.PIPELINE_WITH_TOOLS
    assert [step.step for step in draft.trace] == [1, 2]
    assert draft.trace[1].error  # 越权的调用被拒绝，并记录在轨迹中
    assert draft.time_context is not None
    assert draft.time_context.problem_time.time_of_day is not None


def test_pipeline_draft_has_no_trace() -> None:
    item = load_items("draft")[0] | {"mode": "pipeline"}
    with pytest.raises(ValidationError, match="trace"):
        Draft.model_validate(item)
    item = load_items("draft")[1] | {"fallback_reason": "超出步数"}
    with pytest.raises(ValidationError, match="fallback_reason"):
        Draft.model_validate(item)


def test_fallback_is_recorded_on_tool_drafts() -> None:
    item = load_items("draft")[0] | {"fallback_reason": "超出步数，回退到固定流水线"}
    assert Draft.model_validate(item).fallback_reason


def test_trace_steps_are_consecutive() -> None:
    item = load_items("draft")[0]
    item["trace"][1]["step"] = 3
    with pytest.raises(ValidationError, match="连续编号"):
        Draft.model_validate(item)


@pytest.mark.parametrize("tool", ["Search", "search facts", "../x", ""])
def test_tool_name_is_restricted(tool: str) -> None:
    item = load_items("draft")[0]
    item["trace"][0]["tool"] = tool
    with pytest.raises(ValidationError, match="tool"):
        Draft.model_validate(item)
