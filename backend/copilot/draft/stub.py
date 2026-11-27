"""起草服务的占位实现，#6 完成后替换（#8）。

不调用 LLM，只返回符合契约的结果，让工作台的每个操作都能端到端走通。所有文字都以
“【占位】”开头，分流理由写明“占位规则”，避免在演示或测试中被误认为真实结果。

占位的分流规则只按案件已有的类别（导入时来自表单）判断，覆盖三种分流结果，方便界面开发：
- 购买问题、数据迁移 → `escalate`（大致对应 #6 中“支付、数据丢失”的升级条件）
- 意见建议 → `auto_send`（只是建议，仍由客服点击发送）
- 其他 → `review`
"""

from collections.abc import Callable
from datetime import UTC, datetime

from copilot.contracts import Case, CaseAnalysis, Draft
from copilot.contracts.enums import (
    InquiryCategory,
    Route,
    SensitivityTag,
    Sentiment,
    Urgency,
)
from copilot.draft.drafter import DraftResult, latest_player_message

STUB_MODEL = "stub"
GREETING_JA = "いつもご利用いただきありがとうございます。\n『ぽちゃガチョ！』サポート担当です。"
CLOSING_JA = "今後とも『ぽちゃガチョ！』をよろしくお願いいたします。"
_PREVIEW_CHARS = 80

_ROUTES: dict[InquiryCategory, tuple[Route, str]] = {
    InquiryCategory.PURCHASE: (Route.ESCALATE, "占位规则：购买问题，升级处理"),
    InquiryCategory.DATA_TRANSFER: (Route.ESCALATE, "占位规则：数据迁移问题，升级处理"),
    InquiryCategory.FEEDBACK: (Route.AUTO_SEND, "占位规则：意见建议，可直接发送（仍需客服点击）"),
}
_DEFAULT_ROUTE = (Route.REVIEW, "占位规则：需要客服审核")


def _now() -> datetime:
    return datetime.now(UTC)


class StubDrafter:
    """`Drafter` 的占位实现。"""

    def __init__(self, *, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock

    def draft(self, case: Case, *, template_id: str | None = None) -> DraftResult:
        message = latest_player_message(case)
        now = self._clock()
        form_category = case.form.category if case.form else None
        category = case.category or form_category or InquiryCategory.OTHER
        preview = " ".join(message.body.split())[:_PREVIEW_CHARS]

        analysis = CaseAnalysis(
            case_id=case.id,
            message_seq=message.seq,
            translation_zh="【占位】翻译尚未接入（#6），请参考日语原文。",
            summary_zh=f"【占位】{preview}",
            category=category,
            intent="other",
            sentiment=Sentiment.NEUTRAL,
            urgency=Urgency.HIGH if category is InquiryCategory.PURCHASE else Urgency.NORMAL,
            sensitivity_tags=[SensitivityTag.PAYMENT]
            if category is InquiryCategory.PURCHASE
            else [],
            model=STUB_MODEL,
            created_at=now,
        )
        route, reason = _ROUTES.get(category, _DEFAULT_ROUTE)
        reasons = [reason]
        if template_id is not None:
            reasons.append(f"占位实现不使用模板，已忽略指定的模板 {template_id}")
        draft = Draft(
            case_id=case.id,
            message_seq=message.seq,
            body_ja=f"{GREETING_JA}\n\n【占位の下書き】\n\n{CLOSING_JA}",
            back_translation_zh="【占位】这是占位草稿，起草功能尚未接入（#6）。",
            confidence=0.0,
            route=route,
            route_reasons=reasons,
            model=STUB_MODEL,
            created_at=now,
        )
        return DraftResult(analysis=analysis, draft=draft)

    def backtranslate(self, body_ja: str) -> str:
        return f"【占位】回译尚未接入（#6）。日语正文共 {len(body_ja)} 个字符。"
