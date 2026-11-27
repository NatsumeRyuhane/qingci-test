"""`copilot` 命令行入口。各模块的子命令（ingest、kb、draft、eval 等）随各自的 issue 加入。"""

from pathlib import Path
from typing import Annotated

import typer

from copilot import __version__
from copilot.config import get_settings

app = typer.Typer(help="面向中文客服的日语客服坐席助手。", no_args_is_help=True)
db_app = typer.Typer(help="数据库管理。", no_args_is_help=True)
app.add_typer(db_app, name="db")
contracts_app = typer.Typer(help="共享数据契约（#3）。", no_args_is_help=True)
app.add_typer(contracts_app, name="contracts")
kb_app = typer.Typer(help="知识库（#5）。", no_args_is_help=True)
app.add_typer(kb_app, name="kb")


@app.callback()
def main() -> None:
    """面向中文客服的日语客服坐席助手。"""


@app.command()
def version() -> None:
    """显示版本号。"""
    typer.echo(__version__)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="监听地址。"),
    port: int = typer.Option(8000, help="监听端口。"),
    reload: bool = typer.Option(False, help="代码变动时自动重启（仅用于开发）。"),
) -> None:
    """启动后端 API 服务。"""
    import uvicorn

    uvicorn.run("copilot.api:create_app", factory=True, host=host, port=port, reload=reload)


@app.command()
def openapi() -> None:
    """输出 API 的 OpenAPI 规范（JSON）。前端据此生成类型，见 scripts/gen-api-types.sh。"""
    import json

    from copilot.api import create_app

    typer.echo(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True))


@db_app.command("upgrade")
def db_upgrade(
    revision: str = typer.Argument("head", help="目标版本，默认最新。"),
) -> None:
    """把数据库（DATABASE_URL）迁移到指定版本。"""
    from copilot.storage import upgrade_database

    upgrade_database(get_settings().database_url, revision)
    typer.echo(f"数据库已迁移到 {revision}")


@app.command()
def ingest(
    input_path: Annotated[
        Path | None,
        typer.Option("--input", help="样本表格路径。默认取 SAMPLE_DATA_PATH。"),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="输出的 JSONL 路径。默认 data/interim/cases.jsonl。"),
    ] = None,
    stats: Annotated[
        bool, typer.Option("--stats", help="输出完整的清洗统计，可与 docs/data.md 对照。")
    ] = False,
) -> None:
    """读取样本表格，清洗并写出案件（#4）。重复运行产出完全相同的文件。"""
    from copilot.ingest import (
        DEFAULT_OUTPUT,
        SheetFormatError,
        build_cases,
        read_sheet,
        write_cases,
    )

    source = input_path or Path(get_settings().sample_data_path)
    if not source.is_file():
        typer.echo(f"找不到样本表格：{source}（见 data/README.md）", err=True)
        raise typer.Exit(1)
    try:
        cases, ingest_stats = build_cases(read_sheet(source))
    except SheetFormatError as exc:
        typer.echo(f"表格格式错误：{exc}", err=True)
        raise typer.Exit(1) from exc
    target = out or DEFAULT_OUTPUT
    write_cases(cases, target)
    typer.echo(f"已写入 {target}：{ingest_stats.threads} 个案件，{ingest_stats.messages} 封邮件")
    if stats:
        for line in ingest_stats.lines():
            typer.echo(line)


@contracts_app.command("export")
def contracts_export(
    out_dir: Annotated[
        Path | None,
        typer.Option(
            "--out-dir",
            help="把每个模型写成 <目录>/<模型名>.schema.json。不指定时输出到标准输出。",
        ),
    ] = None,
) -> None:
    """导出契约模型的 JSON Schema。"""
    from copilot.contracts.export import dumps, json_schemas, write_json_schemas

    if out_dir is None:
        typer.echo(dumps(json_schemas()), nl=False)
        return
    for path in write_json_schemas(out_dir):
        typer.echo(f"已写入 {path}")


@app.command("split")
def split_command(
    cases_path: Annotated[
        Path | None,
        typer.Option("--cases", help="导入结果（copilot ingest）。默认 data/interim/cases.jsonl。"),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="输出的切分结果。默认 data/processed/split.json。"),
    ] = None,
    method: Annotated[
        str,
        typer.Option(
            "--method",
            help="stratified：在时间上均匀分布的分层抽样（默认）；chronological：按时间先后切分。",
        ),
    ] = "stratified",
    stats: Annotated[bool, typer.Option("--stats", help="输出各侧的汇总统计。")] = False,
) -> None:
    """把案件切分为 train / dev / test（#5、#7，D-012）。知识库只用 train 构建。"""
    from copilot.eval import DEFAULT_MANIFEST, SplitMethod, split_cases, split_stats, write_manifest
    from copilot.ingest import DEFAULT_OUTPUT, read_cases

    try:
        chosen = SplitMethod(method)
    except ValueError:
        typer.echo(f"不认识的切分方法：{method}（可选 {', '.join(SplitMethod)}）", err=True)
        raise typer.Exit(2) from None
    source = cases_path or DEFAULT_OUTPUT
    if not source.is_file():
        typer.echo(f"找不到导入结果：{source}（先运行 copilot ingest）", err=True)
        raise typer.Exit(1)
    cases = read_cases(source)
    manifest = split_cases(cases, method=chosen)
    target = out or DEFAULT_MANIFEST
    write_manifest(manifest, target)
    counts = {side: len(manifest.ids(side)) for side in manifest.ratios}
    summary = " · ".join(f"{side.value}: {n}" for side, n in counts.items())
    typer.echo(f"已写入 {target}：{summary}")
    if stats:
        for line in split_stats(cases, manifest):
            typer.echo(line)


@kb_app.command("build")
def kb_build(
    cases_path: Annotated[
        Path | None,
        typer.Option("--cases", help="导入结果（copilot ingest）。默认 data/interim/cases.jsonl。"),
    ] = None,
    split_path: Annotated[
        Path | None,
        typer.Option("--split", help="切分结果（copilot split）。默认 data/processed/split.json。"),
    ] = None,
    out_dir: Annotated[
        Path | None,
        typer.Option("--out-dir", help="输出目录。默认 data/processed/kb。"),
    ] = None,
) -> None:
    """只用 train 线程构建模板候选，按覆盖率排列（#5，D-012）。重复运行保持模板 ID 不变。"""
    from copilot.eval import DEFAULT_MANIFEST, StaleSplitError, read_manifest
    from copilot.ingest import DEFAULT_OUTPUT, read_cases
    from copilot.kb.build import (
        CANDIDATES_FILE,
        DEFAULT_KB_DIR,
        build_candidates,
        read_candidates,
        write_candidates,
    )

    source = cases_path or DEFAULT_OUTPUT
    manifest_path = split_path or DEFAULT_MANIFEST
    for path, hint in ((source, "copilot ingest"), (manifest_path, "copilot split")):
        if not path.is_file():
            typer.echo(f"找不到 {path}（先运行 {hint}）", err=True)
            raise typer.Exit(1)
    target = (out_dir or DEFAULT_KB_DIR) / CANDIDATES_FILE
    try:
        candidates, report = build_candidates(
            read_cases(source), read_manifest(manifest_path), read_candidates(target)
        )
    except StaleSplitError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    write_candidates(candidates, target)
    typer.echo(f"已写入 {target}：{len(candidates)} 个模板候选")
    for line in report.lines():
        typer.echo(line)


@kb_app.command("knowledge")
def kb_knowledge(
    directory: Annotated[
        Path | None, typer.Option("--dir", help="人工编写的知识。默认 knowledge/。")
    ] = None,
    check_leaks: Annotated[
        bool,
        typer.Option(
            "--check-leaks", help="检查是否有一段与样本相同（需要 copilot ingest 的结果）。"
        ),
    ] = False,
    cases_path: Annotated[
        Path | None,
        typer.Option(
            "--cases", help="导入结果，--check-leaks 时使用。默认 data/interim/cases.jsonl。"
        ),
    ] = None,
) -> None:
    """校验 knowledge/ 中人工编写的知识（#5）。有问题时退出码为 1。"""
    from copilot.ingest import DEFAULT_OUTPUT, read_cases
    from copilot.kb.knowledge import KNOWLEDGE_DIR, KnowledgeError, find_leaks, load_knowledge

    try:
        knowledge = load_knowledge(directory or KNOWLEDGE_DIR)
    except KnowledgeError as exc:
        typer.echo(f"校验失败：{exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"校验通过：意图 {len(knowledge.intents)} · 政策 {len(knowledge.policies)}"
        f" · 公告与事件 {len(knowledge.notices)} · 术语 {len(knowledge.glossary)} · 风格指南"
    )
    if not check_leaks:
        return
    source = cases_path or DEFAULT_OUTPUT
    if not source.is_file():
        typer.echo(f"找不到导入结果：{source}（先运行 copilot ingest）", err=True)
        raise typer.Exit(1)
    leaks = find_leaks(knowledge, read_cases(source))
    if not leaks:
        typer.echo("没有发现与样本相同的文本")
        return
    labels = {"player_text": "与玩家写的内容相同", "agent_reply": "与客服回复（模板原文）相同"}
    for leak in leaks:
        typer.echo(f"{labels[leak.kind.value]}：{leak.source}（{leak.threads} 个线程）", err=True)
    raise typer.Exit(1)


@kb_app.command("annotate")
def kb_annotate(
    cases_path: Annotated[
        Path | None,
        typer.Option("--cases", help="导入结果（copilot ingest）。默认 data/interim/cases.jsonl。"),
    ] = None,
    split_path: Annotated[
        Path | None,
        typer.Option("--split", help="切分结果（copilot split）。默认 data/processed/split.json。"),
    ] = None,
    kb_dir: Annotated[
        Path | None,
        typer.Option("--kb-dir", help="copilot kb build 的输出目录。默认 data/processed/kb。"),
    ] = None,
    knowledge_dir: Annotated[
        Path | None, typer.Option("--knowledge", help="人工编写的知识。默认 knowledge/。")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", min=1, help="只标注覆盖率最高的前 N 个候选。")
    ] = None,
    concurrency: Annotated[int, typer.Option("--concurrency", min=1, help="并发请求数。")] = 4,
) -> None:
    """用 LLM 为模板候选生成标注初稿，写出未审核的模板（#5）。有缓存时不重复调用。"""
    import asyncio
    from datetime import datetime

    from copilot.config import LLMTask, MissingConfigError
    from copilot.eval import DEFAULT_MANIFEST, Split, StaleSplitError, read_manifest
    from copilot.ingest import DEFAULT_OUTPUT, read_cases
    from copilot.ingest.timestamps import JST
    from copilot.kb.annotate import (
        ANNOTATIONS_DIR,
        TEMPLATES_FILE,
        annotate_candidates,
        write_templates,
    )
    from copilot.kb.build import CANDIDATES_FILE, DEFAULT_KB_DIR, read_candidates
    from copilot.kb.knowledge import KNOWLEDGE_DIR, KnowledgeError, load_knowledge
    from copilot.llm import OpenAICompatibleClient

    settings = get_settings()
    try:
        endpoint = settings.llm_endpoint(LLMTask.ANNOTATE)
    except MissingConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    directory = kb_dir or DEFAULT_KB_DIR
    candidates = read_candidates(directory / CANDIDATES_FILE)
    if not candidates:
        typer.echo(
            f"找不到模板候选：{directory / CANDIDATES_FILE}（先运行 copilot kb build）", err=True
        )
        raise typer.Exit(1)
    try:
        train = read_manifest(split_path or DEFAULT_MANIFEST).select(
            read_cases(cases_path or DEFAULT_OUTPUT), Split.TRAIN
        )
        knowledge = load_knowledge(knowledge_dir or KNOWLEDGE_DIR)
    except (StaleSplitError, KnowledgeError, FileNotFoundError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    train_ids = {case.id for case in train}
    stale = [
        c.id
        for c in candidates
        if any(m.split(":")[0] not in train_ids for m in c.source_message_ids)
    ]
    if stale:
        typer.echo(
            f"{len(stale)} 个候选的来源不在 train 中（先重新运行 copilot kb build）", err=True
        )
        raise typer.Exit(1)
    selected = candidates[:limit] if limit else candidates
    templates, report = asyncio.run(
        annotate_candidates(
            selected,
            train,
            knowledge,
            OpenAICompatibleClient(settings),
            model=endpoint.model,
            cache_dir=directory / ANNOTATIONS_DIR,
            now=datetime.now(JST).replace(microsecond=0),
            concurrency=concurrency,
        )
    )
    write_templates(templates, directory / TEMPLATES_FILE)
    typer.echo(f"已写入 {directory / TEMPLATES_FILE}：{len(templates)} 个未审核的模板")
    for line in report.lines():
        typer.echo(line)
    if report.failed:
        raise typer.Exit(1)


@kb_app.command("label")
def kb_label(
    cases_path: Annotated[
        Path | None,
        typer.Option("--cases", help="导入结果（copilot ingest）。默认 data/interim/cases.jsonl。"),
    ] = None,
    split_path: Annotated[
        Path | None,
        typer.Option("--split", help="切分结果（copilot split）。默认 data/processed/split.json。"),
    ] = None,
    kb_dir: Annotated[
        Path | None,
        typer.Option("--kb-dir", help="知识库目录。默认 data/processed/kb。"),
    ] = None,
) -> None:
    """为所有切分生成 #7 用的标准答案：每封客服回复对应的模板与解决信号，以及“不回复”（#5）。"""
    from copilot.eval import DEFAULT_MANIFEST, StaleSplitError, read_manifest
    from copilot.ingest import DEFAULT_OUTPUT, read_cases
    from copilot.kb.annotate import TEMPLATES_FILE, read_templates
    from copilot.kb.build import DEFAULT_KB_DIR
    from copilot.kb.labels import (
        LABELS_DIR,
        NO_REPLY_FILE,
        REPLIES_FILE,
        label_no_reply,
        label_replies,
        summarize,
        write_jsonl,
    )

    directory = kb_dir or DEFAULT_KB_DIR
    templates = read_templates(directory / TEMPLATES_FILE)
    if not templates:
        typer.echo(
            f"找不到模板：{directory / TEMPLATES_FILE}（先运行 copilot kb annotate）", err=True
        )
        raise typer.Exit(1)
    source = cases_path or DEFAULT_OUTPUT
    manifest_path = split_path or DEFAULT_MANIFEST
    for path, hint in ((source, "copilot ingest"), (manifest_path, "copilot split")):
        if not path.is_file():
            typer.echo(f"找不到 {path}（先运行 {hint}）", err=True)
            raise typer.Exit(1)
    cases = read_cases(source)
    manifest = read_manifest(manifest_path)
    try:
        replies = label_replies(cases, manifest, sorted(templates, key=lambda t: t.rank))
        no_reply = label_no_reply(cases, manifest)
    except (StaleSplitError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    out = directory / LABELS_DIR
    write_jsonl(replies, out / REPLIES_FILE)
    write_jsonl(no_reply, out / NO_REPLY_FILE)
    typer.echo(f"已写入 {out}：客服回复 {len(replies)} · 不回复 {len(no_reply)}")
    for line in summarize(replies, no_reply).lines():
        typer.echo(line)
