"""邮件、游戏内表单与案件。"""

from itertools import pairwise
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from copilot.contracts.base import Contract, Document
from copilot.contracts.enums import (
    CaseStatus,
    InquiryCategory,
    MessageRole,
    Platform,
    SensitivityTag,
)

MessageId = Annotated[str, StringConstraints(pattern=r"^.+:[1-9][0-9]*$")]
"""一封邮件的全局标识，格式为 `<thread_id>:<seq>`，由 `message_id()` 生成。"""


def message_id(thread_id: str, seq: int) -> str:
    """生成邮件标识 `<thread_id>:<seq>`。"""
    return f"{thread_id}:{seq}"


class Message(Contract):
    """线程中的一封邮件。来源：样本表格的一行（见 docs/data.md）。"""

    thread_id: str = Field(min_length=1)
    """所属线程的 ID，来源：`メールID`。与 `Case.id` 相同。"""
    seq: int = Field(ge=1)
    """在线程中的序号，从 1 开始。来源：`メールID枝番`。"""
    role: MessageRole
    """发送方，由导入步骤（#4）判断。"""
    raw_body: str
    """原始正文，保持收到时的原样。来源：`本文`。"""
    body: str
    """去除引用的历史邮件、修复常见乱码后的正文。分析和检索都使用它。"""
    quoted: str = ""
    """从 `raw_body` 中去除的引用文本，没有引用时为空字符串。保留它用于核对清洗效果。"""
    sent_at: AwareDatetime | None = None
    """发送时间，必须带时区。样本中大多没有，只能从后续邮件的引用头中推断。"""

    @property
    def id(self) -> str:
        """邮件标识 `<thread_id>:<seq>`。不参与序列化。"""
        return message_id(self.thread_id, self.seq)


class InquiryForm(Contract):
    """玩家通过游戏内表单提交的字段，从线程第 1 封邮件中解析（见 docs/data.md）。

    所有字段都可选：样本中很多字段为空。账号 ID、玩家名和邮箱地址已被脱敏清空，
    这里不保留。
    """

    app_version: str | None = None
    """App 版本，来源：`アプリバージョン`，例如 `1.4.0`。"""
    os_version: str | None = None
    """OS 版本，来源：`ご利用のOSバージョン`。"""
    device: str | None = None
    """设备名，来源：`ご利用端末名`。"""
    category: InquiryCategory | None = None
    """玩家选择的咨询类别，来源：`お問い合わせ内容の種類`。"""
    platform: Platform | None = None
    """平台，来源：`ご利用環境`。"""
    occurred_at: str | None = None
    """问题发生的时间，来源：`問題が発生した日時`。保留玩家填写的原文，格式不统一。"""
    content: str | None = None
    """咨询内容的自由文本，来源：`お問い合わせ内容`。"""
    has_attachment: bool | None = None
    """是否有附件，来源：`添付ファイル`。无法判断时为 null。"""


class Case(Document):
    """一个案件，对应一个邮件线程（`メールID`）。

    `queue`、`sensitivity_tags`、`assigned_to` 是为访问控制（#13）预留的字段：MVP 中只记录，
    不影响谁能看到案件。
    """

    schema_version: Literal[1] = 1
    """本模型的 schema 版本。"""
    id: str = Field(min_length=1)
    """案件 ID，等于线程 ID（`メールID`）。"""
    messages: list[Message] = Field(min_length=1)
    """线程中的邮件，按 `seq` 严格递增排列。"""
    form: InquiryForm | None = None
    """第 1 封邮件中的表单。不是通过表单提交的线程没有这一项。"""
    category: InquiryCategory | None = None
    """案件的类别。优先采用分析结果（`CaseAnalysis.category`），其次是表单中玩家的选择。"""
    status: CaseStatus = CaseStatus.OPEN
    """在坐席流程中的状态。"""
    queue: str | None = None
    """**预留（#13）**：案件所属的队列，例如按游戏、地区或外包商划分。"""
    sensitivity_tags: list[SensitivityTag] = Field(default_factory=list)
    """**预留（#13）**：已确认的敏感标签。来自分析结果的提议，经人工确认后写入。"""
    assigned_to: str | None = None
    """**预留（#13）**：负责的客服身份。身份体系随 #13 引入，现在是任意字符串。"""

    @model_validator(mode="after")
    def _check_messages(self) -> Self:
        for message in self.messages:
            if message.thread_id != self.id:
                raise ValueError(f"邮件 {message.id} 的 thread_id 与案件 ID {self.id!r} 不一致")
        seqs = [message.seq for message in self.messages]
        if any(a >= b for a, b in pairwise(seqs)):
            raise ValueError(f"邮件必须按 seq 严格递增排列，实际为 {seqs}")
        return self
