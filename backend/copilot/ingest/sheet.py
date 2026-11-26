"""读取样本表格（data/raw/*.xlsx）。表格结构见 docs/data.md。"""

import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl

COLUMNS = ("メールID", "メールID枝番", "本文")


class SheetFormatError(ValueError):
    """表格结构与预期不符。"""


@dataclass(frozen=True)
class SheetRow:
    thread_id: str
    seq: int
    body: str


_INT_TEXT = re.compile(r"[+-]?\d+(?:\.0*)?")
"""文本形式的整数，允许 `3.0` 这样的写法。"""


def _as_int(value: object, what: str, row: int) -> int:
    """整数和文本都直接按整数解析，不经过 float，大数也不会丢失精度。

    Excel 的数字单元格本身就是浮点数，只接受没有小数部分的值。
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and _INT_TEXT.fullmatch(value.strip()):
        return int(value.strip().split(".")[0])
    raise SheetFormatError(f"第 {row} 行的 {what} 不是整数：{value!r}")


def _as_id(value: object, row: int) -> str:
    """数字 ID 统一写成不带小数点的字符串；其他值去掉首尾空白。"""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(_as_int(value, "メールID", row))
    text = str(value).strip()
    if not text:
        raise SheetFormatError(f"第 {row} 行的 メールID 为空")
    return text


def read_sheet(path: Path) -> list[SheetRow]:
    """读取第一个工作表。跳过完全为空的行；正文为空的单元格读作空字符串。"""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header = next(rows, ())
        if tuple(header[:3]) != COLUMNS:
            raise SheetFormatError(f"表头应为 {COLUMNS}，实际为 {tuple(header[:3])}")
        result: list[SheetRow] = []
        for index, values in enumerate(rows, start=2):
            thread_id, seq, body = (*values, None, None, None)[:3]
            if thread_id is None and seq is None and body is None:
                continue
            if thread_id is None or seq is None:
                raise SheetFormatError(f"第 {index} 行缺少 メールID 或 メールID枝番")
            result.append(
                SheetRow(
                    thread_id=_as_id(thread_id, index),
                    seq=_as_int(seq, "メールID枝番", index),
                    body="" if body is None else str(body),
                )
            )
        return result
    finally:
        workbook.close()
