"""解析游戏内表单（线程第 1 封邮件）的 `key : value` 字段。"""

import re
import unicodedata

from copilot.contracts import InquiryCategory, InquiryForm, Platform

FORM_KEYS = (
    "アカウントID",
    "プレイヤー名",
    "アプリバージョン",
    "ご利用のOSバージョン",
    "ご利用端末名",
    "お問い合わせ内容の種類",
    "ご利用環境",
    "問題が発生した日時",
    "メールアドレス",
    "お問い合わせ内容",
    "添付ファイル",
)
"""表单的全部字段，顺序与样本一致（docs/data.md）。"""

REDACTED_KEYS = ("アカウントID", "プレイヤー名", "メールアドレス")
"""脱敏时应已清空的字段。`InquiryForm` 不保留它们；导入统计会报告其中非空的个数。"""

# 内容字段用全角 `：`，其他字段用半角 ` : `，两种都接受。
# `お問い合わせ内容の種類` 必须紧跟冒号，所以不会被误认成 `お問い合わせ内容`。
_FIELD_LINE = re.compile(
    rf"^[ \t\u3000]*({'|'.join(map(re.escape, FORM_KEYS))})[ \t\u3000]*[:：][ \t\u3000]?(.*)$"
)
_CATEGORY_MARKER = re.compile(r"^[ \t\u3000]*お問い合わせ内容の種類[ \t\u3000]*[:：]", re.MULTILINE)

CATEGORIES: dict[str, InquiryCategory] = {
    "不具合について": InquiryCategory.BUG,
    "アイテム購入時に問題が発生した": InquiryCategory.PURCHASE,
    "ご意見・ご要望": InquiryCategory.FEEDBACK,
    "データの引き継ぎについて": InquiryCategory.DATA_TRANSFER,
}
PLATFORMS: dict[str, Platform] = {"ios": Platform.IOS, "google play": Platform.GOOGLE_PLAY}
NO_ATTACHMENT = {"なし", "無し", "無", "ない", "no", "none"}


def looks_like_form(text: str) -> bool:
    """正文中有表单的类别字段行，就认为是表单。"""
    return _CATEGORY_MARKER.search(text) is not None


def parse_fields(text: str) -> dict[str, str]:
    """解析字段，返回 {字段名: 值}。

    值可以跨行（直到下一个字段），去掉首尾空白；空值保留为空字符串。

    同一字段出现多次时取第一次。字段之前的行（例如标题 `お問い合わせ`）忽略。
    """
    fields: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.split("\n"):
        match = _FIELD_LINE.match(line)
        if match:
            key, value = match.groups()
            if key in fields:
                current = None
                continue
            current = fields[key] = [value]
        elif current is not None:
            current.append(line)
    return {key: "\n".join(lines).strip(" \t\u3000\n") for key, lines in fields.items()}


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def to_inquiry_form(fields: dict[str, str]) -> InquiryForm:
    """把字段映射成契约模型。空值视为缺失；无法识别的类别和平台记为 `other`。"""

    def get(key: str) -> str | None:
        return fields.get(key) or None

    category = get("お問い合わせ内容の種類")
    platform = get("ご利用環境")
    attachment = get("添付ファイル")
    categories = {_norm(k): v for k, v in CATEGORIES.items()}
    return InquiryForm(
        app_version=get("アプリバージョン"),
        os_version=get("ご利用のOSバージョン"),
        device=get("ご利用端末名"),
        category=(
            None if category is None else categories.get(_norm(category), InquiryCategory.OTHER)
        ),
        platform=None if platform is None else PLATFORMS.get(_norm(platform), Platform.OTHER),
        occurred_at=get("問題が発生した日時"),
        content=get("お問い合わせ内容"),
        has_attachment=None if attachment is None else _norm(attachment) not in NO_ATTACHMENT,
    )
