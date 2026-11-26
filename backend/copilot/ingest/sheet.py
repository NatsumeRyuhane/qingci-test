"""读取样本表格（data/raw/*.xlsx）。表格结构见 docs/data.md。"""

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import openpyxl

COLUMNS = ("メールID", "メールID枝番", "本文")

# 表格可能来自外部，内容不可信（#18）。以下上限防止构造的文件耗尽时间和内存。
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
"""xlsx（zip）中所有文件解压后的总大小上限。样本表格解压后约 10 MB。"""
MAX_ROWS = 1_048_576
"""最多读取的行数（含表头），等于 Excel 工作表的行数上限。"""
MAX_BODY_CHARS = 32_767
"""单封正文的字符数上限，等于 Excel 单元格的字符数上限。样本中最长约 5,900 字符。"""
MAX_ID_CHARS = 64
"""メールID 的字符数上限。"""


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
    if len(text) > MAX_ID_CHARS:
        raise SheetFormatError(f"第 {row} 行的 メールID 超过 {MAX_ID_CHARS} 字符")
    return text


def _check_archive(path: Path) -> None:
    """打开前检查解压后的总大小，防止压缩炸弹。zip 头中声明的大小在解压时会被校验。"""
    with zipfile.ZipFile(path) as archive:
        total = sum(info.file_size for info in archive.infolist())
    if total > MAX_UNCOMPRESSED_BYTES:
        raise SheetFormatError(f"表格解压后共 {total} 字节，超过上限 {MAX_UNCOMPRESSED_BYTES} 字节")


def read_sheet(path: Path) -> list[SheetRow]:
    """读取第一个工作表的前 3 列。跳过完全为空的行；正文为空的单元格读作空字符串。"""
    _check_archive(path)
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        # 只读需要的 3 列：工作表声明的尺寸不可信，否则每行都会被补齐到声明的列数。
        rows = sheet.iter_rows(max_col=len(COLUMNS), values_only=True)
        header = next(rows, ())
        if tuple(header[:3]) != COLUMNS:
            raise SheetFormatError(f"表头应为 {COLUMNS}，实际为 {tuple(header[:3])}")
        result: list[SheetRow] = []
        for index, values in enumerate(rows, start=2):
            if index > MAX_ROWS:
                raise SheetFormatError(f"表格超过 {MAX_ROWS} 行")
            thread_id, seq, body = (*values, None, None, None)[:3]
            if body is not None and len(str(body)) > MAX_BODY_CHARS:
                raise SheetFormatError(f"第 {index} 行的 本文 超过 {MAX_BODY_CHARS} 字符")
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
