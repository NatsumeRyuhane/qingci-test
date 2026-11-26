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
    SheetRow("2", 1, "【合成データ】The mail system\n\nReason: 5.1.1\n"),
    SheetRow("10", 2, AGENT),
]


def test_build_cases() -> None:
    cases, stats = build_cases(ROWS)
    assert [c.id for c in cases] == ["2", "10"]  # 数字 ID 按数值排序

    bounce, case = cases
    assert bounce.messages[0].role is MessageRole.SYSTEM
    assert bounce.status is CaseStatus.REPLIED  # 没有待回复的玩家邮件

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
