"""工作台使用的起草服务接口，以及不调用 LLM 的占位实现（#8）。

API 只依赖 `Drafter`。真实实现是 `PipelineDrafter`（调用 #6 的 `draft_case`）；
`StubDrafter` 不调用 LLM，用于前端开发、预览和测试（`WORKBENCH_DRAFTER=stub`）。
两者都返回 #6 的 `DraftOutcome`。
"""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from copilot.contracts import (
    Case,
    CaseAnalysis,
    CaseStatus,
    Draft,
    DraftCheck,
    DraftCheckKind,
    InquiryCategory,
    Message,
    MessageRole,
    Route,
    SensitivityTag,
    Sentiment,
    Urgency,
)
from copilot.draft.service import DraftOutcome
from copilot.draft.timectx import build_time_context
from copilot.ingest.timestamps import JST


class DrafterUnavailableError(RuntimeError):
    """起草服务无法使用：缺少配置、知识库还没有发布等。说明中写明怎么修复，不含密钥。"""


class DrafterFailedError(RuntimeError):
    """起草服务调用失败，例如 LLM 端点出错或输出无法校验。可以稍后重试。"""


class Drafter(Protocol):
    """分析一个案件并起草回复（#6）。"""

    def draft(self, case: Case, *, background: bool = False) -> DraftOutcome:
        """分析最新一封玩家邮件并起草。“不回复”自动生效时 `draft` 为空。

        `background` 为真时是来信后自动起草，可以用更便宜、优先级更低的服务等级（D-017）。
        案件中没有玩家邮件时抛出 `ValueError`。
        """
        ...

    def backtranslate(self, body_ja: str) -> str:
        """把（客服编辑后的）日语回复回译为中文，供不懂日语的客服核对。"""
        ...

    def translate(
        self, body_zh: str, *, base_zh: str | None = None, base_ja: str | None = None
    ) -> str:
        """把客服编辑的中文回复译成日语全文（客服用中文工作）。

        给出基准（上一次对齐时的中日文）时只重新翻译改过的段落，其他段落保留基准日语
        （`copilot.workbench.editing.translate_edit`）。
        """
        ...

    def check(self, body_ja: str) -> list[DraftCheck]:
        """对编辑后的日语正文运行只依赖正文的自检，不调用 LLM。"""
        ...


def latest_player_message(case: Case) -> Message:
    """案件中最新一封玩家邮件。没有时抛出 `ValueError`。"""
    for message in reversed(case.messages):
        if message.role is MessageRole.PLAYER:
            return message
    raise ValueError(f"案件 {case.id!r} 中没有玩家邮件")


def _now() -> datetime:
    return datetime.now(JST).replace(microsecond=0)


STUB_MODEL = "stub"
_GREETING_JA = "いつもご利用いただきありがとうございます。\n『ぽちゃガチョ！』サポート担当です。"
_CLOSING_JA = "今後とも『ぽちゃガチョ！』をよろしくお願いいたします。"
_PREVIEW_CHARS = 80
_ROUTES: dict[InquiryCategory, tuple[Route, str]] = {
    InquiryCategory.PURCHASE: (Route.ESCALATE, "占位规则：购买问题，升级处理"),
    InquiryCategory.DATA_TRANSFER: (Route.ESCALATE, "占位规则：数据迁移问题，升级处理"),
    InquiryCategory.FEEDBACK: (Route.AUTO_SEND, "占位规则：意见建议，可直接发送"),
}
_DEFAULT_ROUTE = (Route.REVIEW, "占位规则：需要客服审核")


class StubDrafter:
    """不调用 LLM 的占位实现。

    所有文字以“【占位】”开头，分流理由写明“占位规则”，避免被误认为真实结果。分流只按案件的
    类别判断，覆盖三种分流结果，方便界面开发：购买和数据迁移升级处理，意见建议 `auto_send`
    （开启自动发送时由系统发出），其他人工审核。
    """

    def __init__(self, *, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock

    def draft(self, case: Case, *, background: bool = False) -> DraftOutcome:
        message = latest_player_message(case)
        now = self._clock()
        form_category = case.form.category if case.form else None
        category = case.category or form_category or InquiryCategory.OTHER
        preview = " ".join(message.body.split())[:_PREVIEW_CHARS]
        purchase = category is InquiryCategory.PURCHASE

        analysis = CaseAnalysis(
            case_id=case.id,
            message_seq=message.seq,
            translation_zh="【占位】翻译尚未接入，请参考日语原文。",
            summary_zh=f"【占位】{preview}",
            category=category,
            intent="other",
            sentiment=Sentiment.NEUTRAL,
            urgency=Urgency.HIGH if purchase else Urgency.NORMAL,
            sensitivity_tags=[SensitivityTag.PAYMENT] if purchase else [],
            model=STUB_MODEL,
            created_at=now,
        )
        route, reason = _ROUTES.get(category, _DEFAULT_ROUTE)
        time_context = build_time_context(case, now=now)
        draft = Draft(
            case_id=case.id,
            message_seq=message.seq,
            body_ja=f"{_GREETING_JA}\n\n【占位の下書き】\n\n{_CLOSING_JA}",
            back_translation_zh="【占位】这是占位草稿，没有调用起草服务。",
            confidence=0.0,
            route=route,
            route_reasons=[reason],
            time_context=time_context,
            model=STUB_MODEL,
            created_at=now,
        )
        status = CaseStatus.ESCALATED if route is Route.ESCALATE else CaseStatus.DRAFTED
        return DraftOutcome(
            time_context=time_context,
            analysis=analysis,
            draft=draft,
            proposed_status=status,
            created_at=now,
        )

    def backtranslate(self, body_ja: str) -> str:
        return f"【占位】没有调用回译服务。日语正文共 {len(body_ja)} 个字符。"

    def translate(
        self, body_zh: str, *, base_zh: str | None = None, base_ja: str | None = None
    ) -> str:
        from copilot.workbench.editing import changed_paragraphs, join_paragraphs, split_paragraphs

        changed = (
            changed_paragraphs(body_zh, base_zh, base_ja)
            if base_zh is not None and base_ja is not None
            else None
        )
        if changed is None or base_ja is None:
            return (
                f"{_GREETING_JA}\n\n【占位：没有调用翻译服务】\n{body_zh.strip()}\n\n{_CLOSING_JA}"
            )
        zh, _ = split_paragraphs(body_zh)
        ja, separators = split_paragraphs(base_ja)
        for i in changed:
            ja[i] = f"【占位翻訳】{zh[i]}"
        return join_paragraphs(ja, separators)

    def check(self, body_ja: str) -> list[DraftCheck]:
        structured = body_ja.startswith(_GREETING_JA) and body_ja.rstrip().endswith(_CLOSING_JA)
        return [
            DraftCheck(
                kind=DraftCheckKind.STRUCTURE,
                passed=structured,
                detail_zh=None if structured else "【占位】开头或结尾不是固定的问候语、结语",
            )
        ]
