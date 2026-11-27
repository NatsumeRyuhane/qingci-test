"""模板正文的清理（#5，#8 的演示中发现）：在构建候选时去掉历史邮件带来的排版问题。

历史客服回复来自不同的邮件客户端，部分正文按固定宽度（常见 50 字，也有 38、44、58 字）硬换行，
把一个词从中间断开（例如“アカウ↵ント”“でし↵ょうか”）；还有一些回复在正文末尾自带一句结语
（“引き続き『ぽちゃガチョ！』をよろしくお願いいたします。”）。起草时（#6）会按风格指南追加固定
结语，于是草稿中出现两句结语。

- `unwrap_lines`：把被硬换行断开的行接回去。日语不用空格分词，直接拼接。只在上一行没有以
  句末标点、冒号或分隔线结束，并且下一行不是列表项、注释或标题时拼接，所以列表、
  “プレイヤー名：”这样的表单项、`----` 分隔块和短的标题行都保持原样。空行（段落）不变。
- `drop_trailing_closing`：去掉正文最后一行只是游戏结语的那一句。“これからもチーム一同尽力して
  参りますので…”这样有实际内容的句子不受影响。
"""

import re

_SENTENCE_END = re.compile(r"[。．！？!?」』）)】：:]\s*$")
_SEPARATOR_LINE = re.compile(r"^\s*[-ー－=＝─━_＿~～*＊]{3,}\s*$")
_LIST_START = re.compile(r"^\s*(?:[・※▼▽■□●○◆◇★☆【〒\-－＊*>＞]|[0-9０-９]+[.．)）、]|[①-⑳])")
_CLOSING_LINE = re.compile(
    r"^\s*(?:引き続き|今後とも)、?\s*『ぽちゃガチョ！』\s*を\s*(?:何卒|どうぞ)?\s*"
    r"よろしくお願い(?:いた|致)します。?\s*$"
)
_PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}\s*$")
MIN_WRAPPED_CHARS = 20
"""硬换行发生在长行上（样本中 38–58 字）。短行（例如“■注意事项”这样的标题）不与下一行拼接。"""


def _continues(line: str, following: str) -> bool:
    """`following` 是否是 `line` 被硬换行断开后的后半部分。"""
    if len(line.strip()) < MIN_WRAPPED_CHARS or not following.strip():
        return False
    if _SENTENCE_END.search(line) or _SEPARATOR_LINE.match(line) or _PLACEHOLDER.search(line):
        return False
    return not (_LIST_START.match(following) or _SEPARATOR_LINE.match(following))


def unwrap_lines(text: str) -> str:
    """把被硬换行断开的行接回去。见模块说明。"""
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        if out and _continues(out[-1], line):
            out[-1] = out[-1].rstrip() + line.lstrip()
        else:
            out.append(line)
    return "\n".join(out)


def drop_trailing_closing(text: str) -> str:
    """去掉正文最后一行只是游戏结语的那一句（起草时会按风格指南追加固定结语）。"""
    lines = text.rstrip().split("\n")
    if lines and _CLOSING_LINE.match(lines[-1]):
        lines.pop()
    return "\n".join(lines).rstrip()


def clean_body(text: str) -> str:
    """构建模板候选时使用的清理：先接回硬换行，再去掉末尾的结语。"""
    return drop_trailing_closing(unwrap_lines(text))
