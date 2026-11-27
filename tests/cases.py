"""在测试中直接构造 `Case`，不经过表格解析。

SYNTHETIC：这里构造的内容全部是合成数据，不来自客户的样本表格（D-002）。
"""

from copilot.contracts import (
    Case,
    InquiryForm,
    Message,
    MessageRole,
    RoleEvidence,
    RoleEvidenceKind,
)

GREETING = "いつもご利用いただきありがとうございます。\n『ぽちゃガチョ！』サポート担当です。\n\n"
CLOSING = "\n\n今後とも『ぽちゃガチョ！』をよろしくお願いいたします。"


def player(thread_id: str, seq: int, body: str = "【合成データ】問い合わせ") -> Message:
    return Message(thread_id=thread_id, seq=seq, role=MessageRole.PLAYER, raw_body=body, body=body)


def agent(
    thread_id: str, seq: int, core: str, *evidence: RoleEvidenceKind, source: str | None = None
) -> Message:
    """一封客服回复。`evidence` 中的 `quote_corroboration` 和 `corroborated_text` 需要 `source`，
    `repeated_text` 记为出现在 3 个线程中。"""
    body = GREETING + core + CLOSING
    role_evidence = []
    for kind in evidence:
        needs_source = kind in {
            RoleEvidenceKind.QUOTE_CORROBORATION,
            RoleEvidenceKind.CORROBORATED_TEXT,
        }
        role_evidence.append(
            RoleEvidence(
                kind=kind,
                source_message_id=source if needs_source else None,
                thread_count=3 if kind is RoleEvidenceKind.REPEATED_TEXT else None,
            )
        )
    return Message(
        thread_id=thread_id,
        seq=seq,
        role=MessageRole.AGENT,
        raw_body=body,
        body=body,
        greeting=GREETING,
        closing=CLOSING,
        role_evidence=role_evidence,
    )


def case(
    thread_id: str,
    *messages: Message,
    content: str | None = None,
    device: str | None = "SYN-DEVICE",
    app_version: str | None = "9.9.9",
    occurred_at: str | None = "2099-01-02 09:00",
) -> Case:
    """一个案件。给出 `content` 时带一个表单；没有给出邮件时只有一封玩家邮件。"""
    form = (
        None
        if content is None
        else InquiryForm(
            content=content, device=device, app_version=app_version, occurred_at=occurred_at
        )
    )
    return Case(id=thread_id, messages=list(messages) or [player(thread_id, 1)], form=form)
