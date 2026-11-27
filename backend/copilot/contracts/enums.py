"""契约中使用的枚举。取值是稳定的英文标识，会写进数据库和对外 API，不要随意改名。"""

from enum import StrEnum


class MessageRole(StrEnum):
    """邮件的发送方。"""

    PLAYER = "player"
    """玩家（首次咨询或追问）。"""
    AGENT = "agent"
    """客服。样本中以固定问候语开头的邮件（见 docs/data.md）。"""
    SYSTEM = "system"
    """系统自动生成的邮件，例如退信和客服工具的批量回复报告。不是咨询，不需要回复。"""
    UNKNOWN = "unknown"
    """无法判断，例如正文为空。"""


class InquiryCategory(StrEnum):
    """咨询类别，对应游戏内表单的 `お問い合わせ内容の種類`（见 docs/data.md）。"""

    BUG = "bug"
    """不具合について：故障反馈。"""
    PURCHASE = "purchase"
    """アイテム購入時に問題が発生した：购买道具时出现问题。"""
    FEEDBACK = "feedback"
    """ご意見・ご要望：意见或建议。"""
    DATA_TRANSFER = "data_transfer"
    """データの引き継ぎについて：跨设备数据迁移。"""
    OTHER = "other"
    """以上都不是，或表单中的取值无法识别。"""


class Platform(StrEnum):
    """玩家使用的平台，对应表单的 `ご利用環境`。"""

    IOS = "ios"
    GOOGLE_PLAY = "google_play"
    OTHER = "other"


class CaseStatus(StrEnum):
    """案件在坐席流程中的状态。"""

    OPEN = "open"
    """待处理：有一封玩家邮件还没有回复。"""
    DRAFTED = "drafted"
    """已生成草稿，等待客服处理。"""
    ESCALATED = "escalated"
    """已升级，交由高级客服或主管处理。"""
    REPLIED = "replied"
    """已回复：线程以客服回复结束，之后没有玩家追问。玩家再次追问时回到 `open`。"""
    NO_REPLY_NEEDED = "no_reply_needed"
    """不需要回复：例如重复提交（对应的线程已回复）、只含系统邮件的线程。由 #6 的分析步骤
    提议（`CaseAnalysis.no_reply`），是否自动生效见提案 0003 §8。"""
    UNDETERMINED = "undetermined"
    """待判断：线程以系统邮件或无法判断发送方的邮件结束，单看结构无法确定是否已结案，
    需要语义分析。例如客服回复后的退信可能是我们的回复没有发出去，也可能是玩家粘贴的内容
    或伪装成系统邮件的输入。"""


class SensitivityTag(StrEnum):
    """敏感标签。为访问控制（#13）预留：带这些标签的案件以后只对获得授权的人员可见。

    MVP 中由分析步骤（#6）提议，暂不影响可见性。确切的清单在 #13 的设计中确定。
    """

    PAYMENT = "payment"
    """支付纠纷、退款。"""
    ACCOUNT_SECURITY = "account_security"
    """账号被盗、异常登录。"""
    MINOR = "minor"
    """涉及未成年人。"""
    LEGAL = "legal"
    """法律威胁、投诉到监管机构等。"""
    PERSONAL_INFO = "personal_info"
    """邮件中含有个人隐私信息。"""


class RiskLevel(StrEnum):
    """回复模板的风险等级。只有 `low` 的模板可以允许自动发送（D-001）。"""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class Sentiment(StrEnum):
    """玩家邮件的情绪。"""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    ANGRY = "angry"
    """明显愤怒，回复需要特别注意安抚。"""


class Urgency(StrEnum):
    """处理的紧急程度。"""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    """例如付费后未到账、无法登录。"""


class Route(StrEnum):
    """草稿的分流结果（D-001）。"""

    AUTO_SEND = "auto_send"
    """无需人工审核即可发送。只用于少数低风险类别。"""
    REVIEW = "review"
    """由客服审核、修改后发送。"""
    ESCALATE = "escalate"
    """升级给高级客服或主管。"""


class DraftCheckKind(StrEnum):
    """对草稿做的自动检查。"""

    STRUCTURE = "structure"
    """必需结构：固定的问候语、结语和禁止转载声明是否齐全，正文是否非空且无重复框架。"""
    REGISTER = "register"
    """语体：正文的敬体（です・ます調）是否得体（见 docs/data.md 关键发现 3）。"""
    FORBIDDEN_PROMISE = "forbidden_promise"
    """是否做出了不允许的承诺，例如补偿、退款、具体的修复时间。"""
    TEMPLATE_WINDOW = "template_window"
    """引用的模板是否在有效期内（只适用于某次事故的模板，见 docs/data.md 关键发现 2）。"""
    SLOTS_FILLED = "slots_filled"
    """模板中的占位符是否都已填写。"""
    CITATIONS = "citations"
    """引用检查：引用的知识库条目必须已提供且已批准，新增的事实性说法须有引用。"""
    CONTENT_SAFETY = "content_safety"
    """内容安全：草稿中不能含有非模板的外部链接、邮箱、HTML/脚本、不可见控制符或其他案件信息。"""


class FeedbackAction(StrEnum):
    """客服对草稿的处理。"""

    APPROVED = "approved"
    """原样批准。"""
    EDITED = "edited"
    """修改后发送。"""
    ESCALATED = "escalated"
    """升级处理。"""
    REJECTED = "rejected"
    """草稿不可用。"""
    NO_REPLY = "no_reply"
    """不需要回复，客服直接结案（重复提交、垃圾邮件、没有需要答复的内容等），必须写明理由。
    案件变为 `no_reply_needed`。"""


class TimeStatus(StrEnum):
    """一个时间点的可信程度（提案 0003 §9）。"""

    KNOWN = "known"
    """已知：有确切的值，例如系统时间、表单中完整的日期和时间。"""
    ESTIMATED = "estimated"
    """估计：只知道一个区间，例如按线程 ID 相邻的已知时间推算，或表单中只有时刻没有日期。"""
    UNAVAILABLE = "unavailable"
    """没有：不知道，也不估计。没有就是没有，不能用相邻线程或常识猜测填补。"""


class ReviewStatus(StrEnum):
    """知识库条目的审核状态（提案 0003 §4）。"""

    DRAFT = "draft"
    """未审核。只作为客服的参考，不提供给模型，永远不会自动发送。"""
    APPROVED = "approved"
    """已由仓库所有者批准。只有已批准的条目会提供给模型。"""
    RETIRED = "retired"
    """已下线。最新版本为 `retired` 时，整个条目不再被选中。"""
    NEEDS_REREVIEW = "needs_rereview"
    """引用的政策或事实发生了变化，需要重新审核。重新审核前按未审核处理。"""


class EntryOrigin(StrEnum):
    """知识库条目的来源方式。"""

    RECONSTRUCTED = "reconstructed"
    """从历史数据中归纳，必须列出来源邮件。"""
    MANUAL = "manual"
    """人工录入，例如政策、公告和意图目录。"""


class NoticeKind(StrEnum):
    """公告与事件的类型（提案 0003 §2）。"""

    INCIDENT = "incident"
    """事故，例如商店故障导致购买异常。"""
    MAINTENANCE = "maintenance"
    """维护。"""
    FIX = "fix"
    """修复，例如某个版本修复了某个问题。"""
    POLICY_CHANGE = "policy_change"
    """运营政策的变更。"""


class PolicyKind(StrEnum):
    """政策的类型。政策由代码检查，不靠模型自觉遵守（提案 0003 §2）。"""

    FORBIDDEN_PROMISE = "forbidden_promise"
    """草稿中不允许出现的承诺，例如退款、补偿、具体的修复日期。检查草稿正文。"""
    ESCALATION_TRIGGER = "escalation_trigger"
    """需要升级处理的情况，例如法律威胁。检查玩家邮件和分析得出的敏感标签。"""


class NoReplyReason(StrEnum):
    """不需要回复的原因（提案 0003 §8）。"""

    DUPLICATE = "duplicate"
    """重复提交：与另一个已回复或正在处理的线程是同一个咨询。"""
    SYSTEM_MESSAGE = "system_message"
    """线程只含系统邮件（退信、批量回复报告），不是咨询。"""
    OTHER = "other"
    """其他原因。永远不自动生效，交给人工确认。"""


class EntryType(StrEnum):
    """知识库条目的类型，用于草稿的引用（`Citation`）。"""

    TEMPLATE = "template"
    """回复模板（`ReplyTemplate`）。"""
    INTENT = "intent"
    """意图（`Intent`）。"""
    FACT = "fact"
    """游戏事实（`Fact`）。"""
    POLICY = "policy"
    """政策（`Policy`）。"""
    GLOSSARY = "glossary"
    """术语表条目（`GlossaryEntry`）。"""
    NOTICE = "notice"
    """公告与事件（`Notice`）。"""


class DraftMode(StrEnum):
    """草稿的生成方式（提案 0003 §10）。#7 对比两种方式。"""

    PIPELINE = "pipeline"
    """固定流水线：分析 → 检索 → 起草 → 自检 → 分流，不调用工具。"""
    PIPELINE_WITH_TOOLS = "pipeline_with_tools"
    """流水线 + 工具：模型可以在有限步数内调用只读工具，失败时回退到固定流水线。"""


class RoleEvidenceStrength(StrEnum):
    """角色证据的强度（提案 0003 §11）。"""

    STRONG = "strong"
    """足以确定角色。"""
    AUXILIARY = "auxiliary"
    """辅助：单独不能确定角色，例如同一正文在多个线程中重复出现（玩家可以把同一封伪造
    回复提交到多个线程）。"""
    WEAK = "weak"
    """弱：玩家可以直接在正文中写出，例如固定的问候语。"""


class RoleEvidenceKind(StrEnum):
    """判断角色的依据（提案 0003 §11）。强度见 `ROLE_EVIDENCE_STRENGTH`。

    前提：输入中没有发件人元数据，角色只能从文本判断；只靠文本时没有无法伪造的证据，
    MVP 中由所有者的审核作为知识库的信任根，生产中应提供元数据。
    """

    METADATA = "metadata"
    """强：数据来源提供的发件人信息（来自我们、玩家还是已知的邮件系统）。样本中没有。"""
    QUOTE_CORROBORATION = "quote_corroboration"
    """强：同一线程中之后的一封邮件用引用引导行标明了客服发件人，并且引用内容与这封邮件吻合。"""
    CORROBORATED_TEXT = "corroborated_text"
    """强：正文（`Message.core`，规范化后）与另一封有引用佐证的客服回复相同。与同一线程中
    之前一封客服回复相同时不算（玩家可能把收到的回复粘贴回来）。"""
    REPEATED_TEXT = "repeated_text"
    """辅助：同一正文在多个不同线程的客服回复中出现。"""
    KEYWORD = "keyword"
    """弱：固定的问候语或结语。"""
    SYSTEM_PATTERN = "system_pattern"
    """系统邮件的固定结构：固定的第一行，或至少两种退信特征。只用于系统邮件。
    可以被伪造，所以“不回复”只在 #6 同时没有发现玩家的问题时才自动生效。"""


ROLE_EVIDENCE_STRENGTH: dict[RoleEvidenceKind, RoleEvidenceStrength] = {
    RoleEvidenceKind.METADATA: RoleEvidenceStrength.STRONG,
    RoleEvidenceKind.QUOTE_CORROBORATION: RoleEvidenceStrength.STRONG,
    RoleEvidenceKind.CORROBORATED_TEXT: RoleEvidenceStrength.STRONG,
    RoleEvidenceKind.REPEATED_TEXT: RoleEvidenceStrength.AUXILIARY,
    RoleEvidenceKind.KEYWORD: RoleEvidenceStrength.WEAK,
    RoleEvidenceKind.SYSTEM_PATTERN: RoleEvidenceStrength.STRONG,
}
"""每种依据的强度。"""
