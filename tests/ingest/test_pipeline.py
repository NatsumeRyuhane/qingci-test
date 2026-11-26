"""从表格到案件的完整流程：组装、发送时间、状态、确定性、xlsx 读取和 CLI。

SYNTHETIC：全部是模仿样本格式编写的合成数据（带 【合成データ】 标记），不来自客户数据（D-002）。
xlsx 文件在测试中临时生成，不提交到仓库。
"""

from datetime import datetime
from pathlib import Path

import openpyxl
import pytest
from typer.testing import CliRunner

from copilot.cli import app
from copilot.contracts import CaseStatus, InquiryCategory, MessageRole
from copilot.ingest import (
    SheetFormatError,
    SheetRow,
    build_cases,
    read_cases,
    read_sheet,
    write_cases,
)
from copilot.ingest import sheet as sheet_module
from copilot.ingest.sheet import MAX_ID_CHARS
from copilot.ingest.timestamps import JST

FORM = (
    "\n\n\nお問い合わせ\nアカウントID : \n\nプレイヤー名 : \nアプリバージョン : 9.9.9\n"
    "お問い合わせ内容の種類 : ご意見?ご要望\nご利用環境 : iOS\n"
    "問題が発生した日時 : 2099-01-02 09:00\nメールアドレス : \n"
    "お問い合わせ内容 ：【合成データ】運営?開発チームへの要望です\n\n添付ファイル ：\n\n"
)
AGENT = (
    "いつもご利用いただきありがとうございます。\n『ぽちゃガチョ！』サポート担当です。\n\n"
    "【合成データ】貴重なご意見をありがとうございます。\n\n"
    "今後とも『ぽちゃガチョ！』をよろしくお願いいたします。\n"
    "※本メール内容の無断掲載、無断複製、転送はお控えください。\n\n"
    "> お問い合わせ内容の種類 : ご意見?ご要望\n"
)
FOLLOW_UP = (
    "【合成データ】ありがとうございます。もう一つ要望があります。\n\n"
    "2099年1月2日(金) 午後3:04 【ぽちゃガチョ！カスタマーサポート】 <>:\n\n"
    "> いつもご利用いただきありがとうございます。\n"
    "> 『ぽちゃガチョ！』サポート担当です。\n>\n"
    "> 【合成データ】貴重なご意見をありがとうございます。\n"
)

ROWS = [
    # 故意打乱顺序：导入要按 メールID 分组、按 メールID枝番 排序。
    SheetRow("10", 3, FOLLOW_UP),
    SheetRow("10", 1, FORM),
    SheetRow("2", 1, "The mail system\n\nReason: 5.1.1\n【合成データ】\n"),
    SheetRow("10", 2, AGENT),
]


def test_build_cases() -> None:
    cases, stats = build_cases(ROWS)
    assert [c.id for c in cases] == ["2", "10"]  # 数字 ID 按数值排序

    bounce, case = cases
    assert bounce.messages[0].role is MessageRole.SYSTEM
    assert bounce.status is CaseStatus.UNDETERMINED  # 以系统邮件结束，需要语义分析

    form, agent, follow_up = case.messages
    assert [m.seq for m in case.messages] == [1, 2, 3]
    assert [m.role for m in case.messages] == [
        MessageRole.PLAYER,
        MessageRole.AGENT,
        MessageRole.PLAYER,
    ]
    assert form.raw_body == FORM
    assert form.body.startswith("お問い合わせ")
    assert "ご意見・ご要望" in form.body
    assert case.form is not None
    assert case.form.category is InquiryCategory.FEEDBACK
    assert case.form.content == "【合成データ】運営・開発チームへの要望です"
    assert case.category is InquiryCategory.FEEDBACK

    assert agent.core == "【合成データ】貴重なご意見をありがとうございます。"
    assert agent.quoted.startswith("> お問い合わせ内容の種類")
    assert agent.sent_at == datetime(2099, 1, 2, 15, 4, tzinfo=JST)

    assert follow_up.body == "【合成データ】ありがとうございます。もう一つ要望があります。"
    assert follow_up.sent_at is None
    assert case.status is CaseStatus.OPEN

    assert stats.threads == 2
    assert stats.messages == 4
    assert stats.sent_at_known == 1
    assert stats.forms == 1
    assert stats.redacted_fields_not_empty == 0


def _agent_reply(core: str) -> str:
    return (
        "いつもご利用いただきありがとうございます。\n『ぽちゃガチョ！』サポート担当です。\n\n"
        f"{core}\n\n今後とも『ぽちゃガチョ！』をよろしくお願いいたします。"
    )


def _quoting(text: str, time: str, sender: str, quoted_core: str) -> str:
    return (
        f"【合成データ】{text}\n\n2099年1月2日(金) {time} {sender} <>:\n\n"
        f"> いつもご利用いただきありがとうございます。\n> {quoted_core}\n"
    )


SUPPORT = "【ぽちゃガチョ！カスタマーサポート】"
FIRST_REPLY = "【合成データ】一回目のご案内です。再起動をお試しください。"
SECOND_REPLY = "【合成データ】二回目のご案内です。再インストールをお試しください。"


def test_sent_at_goes_to_the_quoted_message_not_the_latest() -> None:
    rows = [
        SheetRow("1", 1, FORM),
        SheetRow("1", 2, _agent_reply(FIRST_REPLY)),
        SheetRow("1", 3, _agent_reply(SECOND_REPLY)),
        SheetRow("1", 4, _quoting("一回目について", "10:00", SUPPORT, FIRST_REPLY)),
    ]
    (case,), _ = build_cases(rows)
    assert case.messages[1].sent_at == datetime(2099, 1, 2, 10, 0, tzinfo=JST)
    assert case.messages[2].sent_at is None


def test_sent_at_is_skipped_when_quoted_message_is_ambiguous() -> None:
    rows = [
        SheetRow("1", 1, FORM),
        SheetRow("1", 2, _agent_reply(FIRST_REPLY)),
        SheetRow("1", 3, _agent_reply(FIRST_REPLY)),
        SheetRow("1", 4, _quoting("同じ案内でした", "10:00", SUPPORT, FIRST_REPLY)),
    ]
    (case,), stats = build_cases(rows)
    assert [m.sent_at for m in case.messages] == [None] * 4
    assert stats.sent_at_known == 0


def test_sent_at_is_skipped_when_sender_is_unknown() -> None:
    rows = [
        SheetRow("1", 1, FORM),
        SheetRow("1", 2, _agent_reply(FIRST_REPLY)),
        SheetRow("1", 3, _quoting("まだです", "10:00", "", FIRST_REPLY)),  # 只有 `<>:`，没有名称
    ]
    (case,), stats = build_cases(rows)
    assert [m.sent_at for m in case.messages] == [None] * 3
    assert stats.sent_at_known == 0


STATUS_BOUNCE = (
    "This is the mail system at host mx.example.test.\n\n【合成データ】The mail system\n\n"
    "<>: host mx.example.test[192.0.2.1] said: 550"
)


@pytest.mark.parametrize(
    ("bodies", "roles", "status"),
    [
        pytest.param([FORM], "P", CaseStatus.OPEN, id="inquiry-unanswered"),
        pytest.param([FORM, AGENT], "PA", CaseStatus.REPLIED, id="answered-no-follow-up"),
        pytest.param([FORM, AGENT, FOLLOW_UP], "PAP", CaseStatus.OPEN, id="follow-up-unanswered"),
        pytest.param(
            [FORM, AGENT, FOLLOW_UP, AGENT], "PAPA", CaseStatus.REPLIED, id="follow-up-answered"
        ),
        pytest.param([STATUS_BOUNCE], "S", CaseStatus.UNDETERMINED, id="system-only"),
        pytest.param([FORM, STATUS_BOUNCE], "PS", CaseStatus.UNDETERMINED, id="ends-on-system"),
        pytest.param(
            [FORM, AGENT, STATUS_BOUNCE], "PAS", CaseStatus.UNDETERMINED, id="bounce-after-reply"
        ),
        pytest.param(
            [FORM, AGENT, STATUS_BOUNCE, FOLLOW_UP],
            "PASP",
            CaseStatus.OPEN,
            id="player-after-system",
        ),
        pytest.param([FORM, AGENT, "  "], "PAU", CaseStatus.UNDETERMINED, id="ends-on-unknown"),
    ],
)
def test_status_is_decided_by_the_last_message(
    bodies: list[str], roles: str, status: CaseStatus
) -> None:
    (case,), _ = build_cases([SheetRow("1", i + 1, b) for i, b in enumerate(bodies)])
    letters = {"player": "P", "agent": "A", "system": "S", "unknown": "U"}
    assert "".join(letters[m.role.value] for m in case.messages) == roles
    assert case.status is status


def test_conflicting_quote_headers_are_not_used() -> None:
    leader = (
        "2099年1月2日(金) {} 【ぽちゃガチョ！カスタマーサポート】 <>:\n"
        "> 【合成データ】貴重なご意見をありがとうございます。"
    )
    rows = [
        SheetRow("1", 1, FORM),
        SheetRow("1", 2, AGENT),
        SheetRow("1", 3, "【合成データ】a\n" + leader.format("10:00")),
        SheetRow("1", 4, "【合成データ】b\n" + leader.format("11:00")),
    ]
    (case,), stats = build_cases(rows)
    assert case.messages[1].sent_at is None
    assert stats.sent_at_conflicts == 1


def test_form_is_taken_only_from_the_first_message() -> None:
    rows = [SheetRow("1", 1, "【合成データ】ご担当者様、質問があります。"), SheetRow("1", 2, FORM)]
    (case,), stats = build_cases(rows)
    assert case.form is None
    assert case.category is None
    assert case.messages[1].role is MessageRole.PLAYER
    assert "お問い合わせ内容の種類" in case.messages[1].body
    assert stats.forms == 0


def test_numeric_ids_with_same_value_sort_by_spelling() -> None:
    rows = [SheetRow("01", 1, FORM), SheetRow("1", 1, FORM), SheetRow("SYN", 1, FORM)]
    forward = [c.id for c in build_cases(rows)[0]]
    backward = [c.id for c in build_cases(list(reversed(rows)))[0]]
    assert forward == backward == ["01", "1", "SYN"]


def test_thread_order_handles_unusual_ids() -> None:
    long_id = "9" * 5000  # 超过 Python 整数与字符串转换的默认位数上限
    rows = [SheetRow(i, 1, FORM) for i in ["²", "10", long_id, "9", "１", "SYN"]]
    ids = [c.id for c in build_cases(rows)[0]]
    assert ids == ["9", "10", long_id, "SYN", "²", "１"]


def test_duplicate_seq_is_rejected() -> None:
    with pytest.raises(SheetFormatError, match="重复"):
        build_cases([SheetRow("1", 1, FORM), SheetRow("1", 1, AGENT)])


def test_write_is_deterministic(tmp_path: Path) -> None:
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    write_cases(build_cases(ROWS)[0], first)
    write_cases(build_cases(list(reversed(ROWS)))[0], second)
    assert first.read_bytes() == second.read_bytes()
    assert read_cases(first) == build_cases(ROWS)[0]


def _write_xlsx(
    path: Path, rows: list[tuple[object, object, object]], header: tuple[str, ...]
) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


HEADER = ("メールID", "メールID枝番", "本文")


def test_read_sheet(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [(10, 1, FORM), (None, None, None), (10.0, 2, None)], HEADER)
    assert read_sheet(path) == [SheetRow("10", 1, FORM), SheetRow("10", 2, "")]


def test_read_sheet_keeps_integer_precision(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.xlsx"
    rows: list[tuple[object, object, object]] = [
        ("SYN", "9007199254740992", FORM),
        ("SYN", "9007199254740993", AGENT),
        ("SYN", "3.0", FOLLOW_UP),
    ]
    _write_xlsx(path, rows, HEADER)
    assert [r.seq for r in read_sheet(path)] == [9007199254740992, 9007199254740993, 3]


@pytest.mark.parametrize("seq", ["1.5", 1.5, "abc", True])
def test_read_sheet_rejects_non_integer_seq(tmp_path: Path, seq: object) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [("SYN", seq, FORM)], HEADER)
    with pytest.raises(SheetFormatError, match="不是整数"):
        read_sheet(path)


def test_read_sheet_rejects_unexpected_header(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [], ("ID", "seq", "body"))
    with pytest.raises(SheetFormatError, match="表头"):
        read_sheet(path)


def test_read_sheet_ignores_declared_extra_columns(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [("SYN", 1, FORM, "余分な列")], (*HEADER, "extra"))
    assert read_sheet(path) == [SheetRow("SYN", 1, FORM)]


def test_read_sheet_rejects_overlong_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # openpyxl 写入时会把字符串截断到 Excel 的上限，所以这里调低上限来模拟构造的文件。
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [("SYN", 1, "あ" * 101)], HEADER)
    monkeypatch.setattr(sheet_module, "MAX_BODY_CHARS", 100)
    with pytest.raises(SheetFormatError, match="超过 100 字符"):
        read_sheet(path)


def test_read_sheet_rejects_overlong_id(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [("S" * (MAX_ID_CHARS + 1), 1, FORM)], HEADER)
    with pytest.raises(SheetFormatError, match="超过"):
        read_sheet(path)


def test_read_sheet_rejects_archive_bomb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [("SYN", 1, "あ" * 2000)], HEADER)
    monkeypatch.setattr(sheet_module, "MAX_UNCOMPRESSED_BYTES", 1000)
    with pytest.raises(SheetFormatError, match="解压后"):
        read_sheet(path)


def _many_member_zip(path: Path, members: int) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        for i in range(members):
            archive.writestr(f"empty/{i}", b"")


def test_read_sheet_rejects_too_many_members(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.xlsx"
    _many_member_zip(path, sheet_module.MAX_MEMBERS + 1)
    with pytest.raises(SheetFormatError, match="个文件"):
        read_sheet(path)


def test_read_sheet_rejects_large_central_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "synthetic.xlsx"
    _many_member_zip(path, 50)
    monkeypatch.setattr(sheet_module, "MAX_CENTRAL_DIRECTORY_BYTES", 100)
    with pytest.raises(SheetFormatError, match="文件索引"):
        read_sheet(path)


def test_metadata_is_checked_before_zipfile_builds_its_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zipfile

    path = tmp_path / "synthetic.xlsx"
    _many_member_zip(path, sheet_module.MAX_MEMBERS + 1)

    def fail(*_: object, **__: object) -> None:
        raise AssertionError("ZipFile 不应在元数据检查之前被打开")

    monkeypatch.setattr(zipfile, "ZipFile", fail)
    with pytest.raises(SheetFormatError):
        read_sheet(path)


def test_read_sheet_rejects_oversized_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [("SYN", 1, FORM)], HEADER)
    monkeypatch.setattr(sheet_module, "MAX_ARCHIVE_BYTES", 100)
    with pytest.raises(SheetFormatError, match="表格文件共"):
        read_sheet(path)


def test_read_sheet_stops_at_row_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [("SYN", i, FORM) for i in range(1, 6)], HEADER)
    monkeypatch.setattr(sheet_module, "MAX_ROWS", 3)
    with pytest.raises(SheetFormatError, match="超过 3 行"):
        read_sheet(path)


def test_cli_ingest(tmp_path: Path) -> None:
    source, out = tmp_path / "synthetic.xlsx", tmp_path / "cases.jsonl"
    _write_xlsx(source, [(r.thread_id, r.seq, r.body) for r in ROWS], HEADER)
    runner = CliRunner()

    result = runner.invoke(app, ["ingest", "--input", str(source), "--out", str(out), "--stats"])
    assert result.exit_code == 0, result.output
    assert "2 个案件，4 封邮件" in result.output
    assert "发送方：player: 2 · agent: 1 · system: 1" in result.output
    first = out.read_bytes()

    result = runner.invoke(app, ["ingest", "--input", str(source), "--out", str(out)])
    assert result.exit_code == 0
    assert out.read_bytes() == first  # 幂等


def test_cli_ingest_missing_input(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["ingest", "--input", str(tmp_path / "missing.xlsx")])
    assert result.exit_code == 1
    assert "找不到样本表格" in result.output


@pytest.mark.parametrize("seq", [0, -1, "0", "1" * 19])
def test_read_sheet_rejects_invalid_seq(tmp_path: Path, seq: object) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [("SYN", seq, FORM)], HEADER)
    with pytest.raises(SheetFormatError):
        read_sheet(path)


def test_read_sheet_rejects_non_zip(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.xlsx"
    path.write_bytes(b"not a zip file")
    with pytest.raises(SheetFormatError, match="zip"):
        read_sheet(path)


def test_read_sheet_rejects_unsupported_zip_version(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [("SYN", 1, FORM)], HEADER)
    data = bytearray(path.read_bytes())
    entry = data.find(b"PK\x01\x02")  # 第一个中央目录项
    data[entry + 6 : entry + 8] = (99).to_bytes(2, "little")  # 解压所需版本 9.9
    path.write_bytes(bytes(data))
    with pytest.raises(SheetFormatError, match="NotImplementedError"):
        read_sheet(path)


def test_zip64_locator_before_maximum_comment_is_detected(tmp_path: Path) -> None:
    # 注释最长（65,535 字节）时，中央目录结束记录前 20 字节的 zip64 定位记录必须仍在检查范围内。
    eocd = (
        b"PK\x05\x06" + bytes(4) + (1).to_bytes(2, "little") * 2
        + (46).to_bytes(4, "little") + bytes(4) + (0xFFFF).to_bytes(2, "little")
    )  # fmt: skip
    path = tmp_path / "synthetic.xlsx"
    path.write_bytes(bytes(64) + b"PK\x06\x07" + bytes(16) + eocd + bytes(0xFFFF))
    with pytest.raises(SheetFormatError, match="zip64"):
        read_sheet(path)


def test_read_sheet_rejects_broken_xml(tmp_path: Path) -> None:
    import zipfile

    source, broken = tmp_path / "synthetic.xlsx", tmp_path / "broken.xlsx"
    _write_xlsx(source, [("SYN", 1, FORM)], HEADER)
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(broken, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "xl/worksheets/sheet1.xml":
                data = data[: len(data) // 2]  # 截断成不完整的 XML
            dst.writestr(info, data)
    with pytest.raises(SheetFormatError, match="无法解析表格"):
        read_sheet(broken)


def test_cli_reports_format_error_without_traceback(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.xlsx"
    _write_xlsx(source, [("SYN", 0, FORM)], HEADER)
    result = CliRunner().invoke(
        app, ["ingest", "--input", str(source), "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == 1
    assert "表格格式错误" in result.output
    assert "Traceback" not in result.output


def test_header_error_does_not_echo_cell_content(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [], ("SYN", 1, "【合成データ】" + "あ" * 3000))  # 没有表头，第一行就是邮件
    with pytest.raises(SheetFormatError) as info:
        read_sheet(path)
    message = str(info.value)
    assert "あああ" not in message
    assert "3007 字符的文本" in message
    assert len(message) < 120


def test_seq_error_does_not_echo_cell_content(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.xlsx"
    _write_xlsx(path, [("SYN", "【合成データ】" + "い" * 3000, FORM)], HEADER)  # 列错位
    with pytest.raises(SheetFormatError) as info:
        read_sheet(path)
    assert "いいい" not in str(info.value)
    assert len(str(info.value)) < 80


def test_errors_escape_control_characters() -> None:
    thread_id = "SYN\n伪造的日志行\u202e\x9b"
    with pytest.raises(SheetFormatError) as info:
        build_cases([SheetRow(thread_id, 1, FORM), SheetRow(thread_id, 1, AGENT)])
    message = str(info.value)
    assert "\n" not in message
    assert "\u202e" not in message
    assert "\x9b" not in message
    assert "\\u202e" in message


def test_openpyxl_uses_defusedxml() -> None:
    """表格的 XML 由 defusedxml 解析，防止实体扩展和外部实体（#18）。"""
    import openpyxl.xml

    assert openpyxl.xml.DEFUSEDXML
