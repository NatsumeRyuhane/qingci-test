"""契约模型的往返测试、结构约束测试与校验规则测试。数据全部是合成的（tests/fixtures/）。"""

import typing
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from copilot.contracts import (
    EXPORTED_MODELS,
    Case,
    CaseAnalysis,
    Contract,
    Document,
    Draft,
    Fact,
    Feedback,
    GlossaryEntry,
    InquiryForm,
    Intent,
    Message,
    Notice,
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
    template_ids = {template_id for template_id, _ in templates}
    for notice in load_models(Notice, "notice"):
        assert set(notice.template_ids) <= template_ids
    for analysis in load_models(CaseAnalysis, "case_analysis"):
        assert f"{analysis.case_id}:{analysis.message_seq}" in messages
    for draft in drafts.values():
        assert f"{draft.case_id}:{draft.message_seq}" in messages
