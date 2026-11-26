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

    REGISTER = "register"
    """语体：敬语和固定的开头、结尾是否符合规范（见 docs/data.md 关键发现 3）。"""
    FORBIDDEN_PROMISE = "forbidden_promise"
    """是否做出了不允许的承诺，例如补偿、退款、具体的修复时间。"""
    TEMPLATE_WINDOW = "template_window"
    """引用的模板是否在有效期内（只适用于某次事故的模板，见 docs/data.md 关键发现 2）。"""
    SLOTS_FILLED = "slots_filled"
    """模板中的占位符是否都已填写。"""


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


class TimeStatus(StrEnum):
    """一个时间点的可信程度（提案 0003 §9）。"""

    KNOWN = "known"
    """已知：有确切的值，例如系统时间、表单中完整的日期和时间。"""
    ESTIMATED = "estimated"
    """估计：只知道一个区间，例如按线程 ID 相邻的已知时间推算，或表单中只有时刻没有日期。"""
    UNAVAILABLE = "unavailable"
    """没有：不知道，也不估计。没有就是没有，不能用相邻线程或常识猜测填补。"""
