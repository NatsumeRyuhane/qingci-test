"""读取样本表格（data/raw/*.xlsx）。表格结构见 docs/data.md。"""

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import openpyxl

COLUMNS = ("メールID", "メールID枝番", "本文")

# 表格可能来自外部，内容不可信（#18）。以下上限防止构造的文件耗尽时间和内存。
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
"""xlsx 文件本身的大小上限。样本约 1.4 MB。"""
MAX_MEMBERS = 1_000
"""xlsx（zip）中的文件数上限。样本有 17 个。"""
MAX_CENTRAL_DIRECTORY_BYTES = 1024 * 1024
"""zip 中央目录（文件索引）的大小上限。Python 打开 zip 时会把整个中央目录读进内存并建立索引，
所以必须在打开之前限制它。"""
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


DESCRIBE_CHARS = 20
"""错误信息中最多原样显示这么多字符的单元格内容。"""


def describe_cell(value: object) -> str:
    """在错误信息里描述一个单元格，不泄露内容（#18）。

    短文本用 `repr` 显示，换行、控制字符和方向控制符都会被转义，不能伪造日志行或终端输出；
    长文本和其他值只显示类型和长度，避免把整封邮件打印到终端或日志中。
    """
    if value is None:
        return "空"
    text = repr(value)
    if isinstance(value, str | int | float) and len(text) <= DESCRIBE_CHARS + 2:
        return text
    if isinstance(value, str):
        return f"{len(value)} 字符的文本"
    return type(value).__name__


_INT_TEXT = re.compile(r"[+-]?[0-9]{1,18}(?:\.0*)?")
"""文本形式的整数，允许 `3.0` 这样的写法。最多 18 位 ASCII 数字，
远低于 Python 整数与字符串转换的位数上限。"""


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
    raise SheetFormatError(f"第 {row} 行的 {what} 不是整数：{describe_cell(value)}")


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


_EOCD = b"PK\x05\x06"
"""zip 的中央目录结束记录（end of central directory）的签名。"""
_ZIP64_LOCATOR = b"PK\x06\x07"
_EOCD_SIZE = 22
_MAX_COMMENT = 0xFFFF


def _check_archive_metadata(path: Path) -> None:
    """在 `zipfile.ZipFile` 建立索引之前，直接读取中央目录结束记录，限制文件大小、文件数和
    中央目录大小，防止包含海量空文件的压缩包耗尽内存。不支持 zip64（xlsx 用不到）。"""
    size = path.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise SheetFormatError(f"表格文件共 {size} 字节，超过上限 {MAX_ARCHIVE_BYTES} 字节")
    with path.open("rb") as f:
        tail_size = min(size, _EOCD_SIZE + _MAX_COMMENT)
        f.seek(size - tail_size)
        tail = f.read(tail_size)
    position = tail.rfind(_EOCD)
    if position < 0 or len(tail) - position < _EOCD_SIZE:
        raise SheetFormatError("不是有效的 xlsx（zip）文件")
    entries = int.from_bytes(tail[position + 10 : position + 12], "little")
    directory_size = int.from_bytes(tail[position + 12 : position + 16], "little")
    is_zip64 = position >= 20 and tail[position - 20 : position - 16] == _ZIP64_LOCATOR
    if is_zip64 or entries == 0xFFFF or directory_size == 0xFFFFFFFF:
        raise SheetFormatError("不支持 zip64 格式的表格")
    if entries > MAX_MEMBERS:
        raise SheetFormatError(f"表格中有 {entries} 个文件，超过上限 {MAX_MEMBERS}")
    if directory_size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise SheetFormatError(f"表格的文件索引超过 {MAX_CENTRAL_DIRECTORY_BYTES} 字节")


def _check_archive(path: Path) -> None:
    """打开前检查元数据和解压后的总大小，防止压缩炸弹。zip 头中声明的大小在解压时会被校验。"""
    _check_archive_metadata(path)
    with zipfile.ZipFile(path) as archive:
        total = sum(info.file_size for info in archive.infolist())
    if total > MAX_UNCOMPRESSED_BYTES:
        raise SheetFormatError(f"表格解压后共 {total} 字节，超过上限 {MAX_UNCOMPRESSED_BYTES} 字节")


def read_sheet(path: Path) -> list[SheetRow]:
    """读取第一个工作表的前 3 列。跳过完全为空的行；正文为空的单元格读作空字符串。

    任何格式问题（包括损坏的 zip 或 XML、被 defusedxml 拒绝的实体）都报告为
    `SheetFormatError`，而不是让原始异常中止导入。错误信息只包含异常类型，不包含单元格内容。
    """
    try:
        return _read_sheet(path)
    except SheetFormatError:
        raise
    except (
        zipfile.BadZipFile,
        ElementTree.ParseError,
        ValueError,
        KeyError,
        IndexError,
        NotImplementedError,  # 不支持的 zip 版本或压缩方法
    ) as exc:
        raise SheetFormatError(f"无法解析表格（{type(exc).__name__}）") from exc


def _read_sheet(path: Path) -> list[SheetRow]:
    _check_archive(path)
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if not workbook.worksheets:
            raise SheetFormatError("表格中没有工作表")
        sheet = workbook.worksheets[0]
        # 只读需要的 3 列：工作表声明的尺寸不可信，否则每行都会被补齐到声明的列数。
        rows = sheet.iter_rows(max_col=len(COLUMNS), values_only=True)
        header = next(rows, ())
        if tuple(header[:3]) != COLUMNS:
            actual = "、".join(describe_cell(v) for v in (*header, None, None, None)[:3])
            raise SheetFormatError(f"表头应为 {'、'.join(COLUMNS)}，实际为 {actual}")
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
            number = _as_int(seq, "メールID枝番", index)
            if number < 1:
                raise SheetFormatError(f"第 {index} 行的 メールID枝番 必须从 1 开始")
            result.append(
                SheetRow(
                    thread_id=_as_id(thread_id, index),
                    seq=number,
                    body="" if body is None else str(body),
                )
            )
        return result
    finally:
        workbook.close()
