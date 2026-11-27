"""邮件、游戏内表单与案件。"""

import re
from itertools import pairwise
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from copilot.contracts.base import Contract, Document
from copilot.contracts.enums import (
    ROLE_EVIDENCE_STRENGTH,
    CaseStatus,
    InquiryCategory,
    MessageRole,
    Platform,
    RoleEvidenceKind,
    RoleEvidenceStrength,
    SensitivityTag,
)

CASE_ID_CHARS = r"[A-Za-z0-9_-]{1,64}"
"""案件 ID（即线程 ID）允许的字符：ASCII 字母、数字、`_` 和 `-`，1 到 64 个。

案件 ID 会出现在 URL、文件路径和日志中，不能含 `/`、`..`、空白或控制字符（#18 第 8 项）。
样本中的 ID 都是 3–4 位数字。"""

CaseId = Annotated[str, StringConstraints(pattern=rf"^{CASE_ID_CHARS}$")]
"""案件 ID，见 `CASE_ID_CHARS`。"""

MessageId = Annotated[str, StringConstraints(pattern=rf"^{CASE_ID_CHARS}:[1-9][0-9]*$")]
"""一封邮件的全局标识，格式为 `<thread_id>:<seq>`，由 `message_id()` 生成。"""

_CASE_ID_RE = re.compile(CASE_ID_CHARS)


def is_valid_case_id(value: str) -> bool:
    """`value` 是否是合法的案件 ID。供导入步骤在构造 `Case` 之前检查，给出更清楚的错误。"""
    return _CASE_ID_RE.fullmatch(value) is not None


def message_id(thread_id: str, seq: int) -> str:
    """生成邮件标识 `<thread_id>:<seq>`。"""
    return f"{thread_id}:{seq}"


_AGENT_EVIDENCE = {
    RoleEvidenceKind.METADATA,
    RoleEvidenceKind.QUOTE_CORROBORATION,
    RoleEvidenceKind.CORROBORATED_TEXT,
    RoleEvidenceKind.REPEATED_TEXT,
    RoleEvidenceKind.KEYWORD,
}
_SYSTEM_EVIDENCE = {RoleEvidenceKind.METADATA, RoleEvidenceKind.SYSTEM_PATTERN}
_STRENGTH_ORDER = [
    RoleEvidenceStrength.WEAK,
    RoleEvidenceStrength.AUXILIARY,
    RoleEvidenceStrength.STRONG,
]


class RoleEvidence(Contract):
    """判断一封邮件发送方的一条依据（提案 0003 §11）。由导入步骤（#4）填写，不调用 LLM。"""

    kind: RoleEvidenceKind
    """依据的种类。强度见 `strength`。"""
    source_message_id: MessageId | None = None
    """相关的另一封邮件：`quote_corroboration` 时是引用它的那封邮件，`corroborated_text` 时
    是正文相同、有引用佐证的那封客服回复。这两种依据必填，其他依据为空。"""
    thread_count: int | None = Field(default=None, ge=2)
    """`repeated_text` 时，同一正文出现在多少个不同线程中。只用于这种依据，且必填。"""
    detail: str | None = None
    """补充说明，例如 `keyword` 匹配的是问候语还是结语。"""

    @property
    def strength(self) -> RoleEvidenceStrength:
        """这条依据的强度。不参与序列化。"""
        return ROLE_EVIDENCE_STRENGTH[self.kind]

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        needs_source = self.kind in {
            RoleEvidenceKind.QUOTE_CORROBORATION,
            RoleEvidenceKind.CORROBORATED_TEXT,
        }
        if needs_source != (self.source_message_id is not None):
            raise ValueError(f"{self.kind.value} 的 source_message_id 不符合要求")
        if (self.kind is RoleEvidenceKind.REPEATED_TEXT) != (self.thread_count is not None):
            raise ValueError(f"{self.kind.value} 的 thread_count 不符合要求")
        return self


class Message(Contract):
    """线程中的一封邮件。来源：样本表格的一行（见 docs/data.md）。"""

    thread_id: CaseId
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
    greeting: str = ""
    """客服回复开头的固定问候语，是 `body` 的开头部分；其他邮件为空字符串。由 #4 拆出。"""
    closing: str = ""
    """客服回复结尾的固定结语和禁止转载声明，是 `body` 的结尾部分；其他邮件为空字符串。"""
    sent_at: AwareDatetime | None = None
    """发送时间，必须带时区。样本中大多没有，只能从后续邮件的引用头中推断。"""
    role_evidence: list[RoleEvidence] = Field(default_factory=list)
    """判断 `role` 的依据，由导入步骤填写。只有客服回复和系统邮件有；玩家是默认的角色，
    不记录依据。需要信任角色的下游按 `role_strength` 判断（提案 0003 §11）。"""

    @property
    def id(self) -> str:
        """邮件标识 `<thread_id>:<seq>`。不参与序列化。"""
        return message_id(self.thread_id, self.seq)

    @property
    def role_strength(self) -> RoleEvidenceStrength | None:
        """最强的一条依据的强度，没有依据时为 None。不参与序列化。"""
        strengths = {evidence.strength for evidence in self.role_evidence}
        return max(strengths, key=_STRENGTH_ORDER.index, default=None)

    @property
    def core(self) -> str:
        """去掉 `greeting` 和 `closing` 后的正文，即客服回复中随案件变化的部分（#5 据此归纳模板）。
        不参与序列化。"""
        end = len(self.body) - len(self.closing)
        return self.body[len(self.greeting) : end].strip()

    @model_validator(mode="after")
    def _check_role_evidence(self) -> Self:
        allowed = {MessageRole.AGENT: _AGENT_EVIDENCE, MessageRole.SYSTEM: _SYSTEM_EVIDENCE}
        kinds = {evidence.kind for evidence in self.role_evidence}
        if not kinds <= allowed.get(self.role, set()):
            raise ValueError(
                f"邮件 {self.id!r}（{self.role.value}）不能有这些角色依据：{sorted(kinds)}"
            )
        for evidence in self.role_evidence:
            if evidence.source_message_id == self.id:
                raise ValueError(f"邮件 {self.id!r} 的角色依据不能指向自己")
            source = evidence.source_message_id
            if evidence.kind is RoleEvidenceKind.QUOTE_CORROBORATION and source is not None:
                thread, _, seq = source.rpartition(":")
                if thread != self.thread_id or int(seq) <= self.seq:
                    raise ValueError(f"邮件 {self.id!r} 的引用佐证必须来自同一线程中之后的邮件")
        return self

    @model_validator(mode="after")
    def _check_frame(self) -> Self:
        if not self.body.startswith(self.greeting):
            raise ValueError(f"邮件 {self.id!r} 的 greeting 不是 body 的开头")
        if not self.body.endswith(self.closing):
            raise ValueError(f"邮件 {self.id!r} 的 closing 不是 body 的结尾")
        if (
            self.greeting
            and self.closing
            and len(self.greeting) + len(self.closing) > len(self.body)
        ):
            raise ValueError(f"邮件 {self.id!r} 的 greeting 和 closing 重叠")
        return self


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

    schema_version: Literal[2] = 2
    """本模型的 schema 版本。2：限制 `id` 允许的字符（#19）。"""
    id: CaseId
    """案件 ID，等于线程 ID（`メールID`）。允许的字符见 `CASE_ID_CHARS`。"""
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
                raise ValueError(f"邮件 {message.id!r} 的 thread_id 与案件 ID {self.id!r} 不一致")
        seqs = [message.seq for message in self.messages]
        if any(a >= b for a, b in pairwise(seqs)):
            raise ValueError(f"邮件必须按 seq 严格递增排列，实际为 {seqs}")
        return self
