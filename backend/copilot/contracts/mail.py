"""系统发出的回复（由 #8 的工作台在客服批准后生成）。"""

from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field

from copilot.contracts.base import Document
from copilot.contracts.case import CaseId


class OutboundMail(Document):
    """系统发出的一封回复，即发件箱中的一项。

    客服批准（或修改后批准）草稿时，与 `Feedback` 在同一个事务中写入，回复同时作为一封
    客服邮件追加到案件。MVP 中没有真实的邮件系统，发件箱就是终点（模拟邮箱，#8）；
    以后接入真实发送时，由发送程序读取发件箱投递，工作台不需要改动。
    """

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    """发件 ID。默认随机生成。"""
    case_id: CaseId
    """所属案件 ID。"""
    message_seq: int = Field(ge=1)
    """这封回复追加到案件后，作为客服邮件的 `seq`。"""
    draft_id: str = Field(min_length=1)
    """来源草稿 ID（`Draft.id`）。"""
    body_ja: str = Field(min_length=1)
    """发出的正文（日语）：原样批准时是草稿正文，修改后批准时是客服修改后的正文。"""
    sent_by: str = Field(min_length=1)
    """点击发送的客服，与对应 `Feedback.actor` 相同。"""
    sent_at: AwareDatetime
    """发送时间。"""
