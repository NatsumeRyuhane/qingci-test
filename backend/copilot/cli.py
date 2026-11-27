"""`copilot` 命令行入口。各模块的子命令（ingest、kb、draft、eval 等）随各自的 issue 加入。"""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from copilot import __version__
from copilot.config import get_settings

if TYPE_CHECKING:
    from datetime import datetime

    from copilot.config import Settings
    from copilot.draft.service import DraftOutcome
    from copilot.kb.knowledge import Knowledge
    from copilot.llm import LLMClient
    from copilot.llm.embedding import EmbeddingClient

app = typer.Typer(help="面向中文客服的日语客服坐席助手。", no_args_is_help=True)
db_app = typer.Typer(help="数据库管理。", no_args_is_help=True)
app.add_typer(db_app, name="db")
contracts_app = typer.Typer(help="共享数据契约（#3）。", no_args_is_help=True)
app.add_typer(contracts_app, name="contracts")
kb_app = typer.Typer(help="知识库（#5）。", no_args_is_help=True)
app.add_typer(kb_app, name="kb")
review_app = typer.Typer(help="模板的人工审核（#5）。", no_args_is_help=True)
kb_app.add_typer(review_app, name="review")


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


KbDirOption = Annotated[
    Path | None, typer.Option("--kb-dir", help="知识库目录。默认 data/processed/kb。")
]
KnowledgeOption = Annotated[
    Path | None, typer.Option("--knowledge", help="人工编写的知识。默认 knowledge/。")
]


def _load_review_inputs(
    kb_dir: Path | None, knowledge_dir: Path | None
) -> "tuple[Path, Knowledge]":
    """读取知识目录；有问题时退出。"""
    from copilot.kb.build import DEFAULT_KB_DIR
    from copilot.kb.knowledge import KNOWLEDGE_DIR, KnowledgeError, load_knowledge

    try:
        knowledge = load_knowledge(knowledge_dir or KNOWLEDGE_DIR)
    except KnowledgeError as exc:
        typer.echo(f"knowledge/ 校验失败：{exc}", err=True)
        raise typer.Exit(1) from exc
    return kb_dir or DEFAULT_KB_DIR, knowledge


@review_app.command("decisions")
def kb_review_decisions(
    path: Annotated[Path, typer.Argument(help="审核工具写出的决定（JSONL）。")],
    kb_dir: KbDirOption = None,
) -> None:
    """把审核工具的决定（批准 / 拒绝）导入覆盖层。之后运行 copilot kb publish。"""
    from copilot.kb.annotate import TEMPLATES_FILE, read_templates
    from copilot.kb.build import DEFAULT_KB_DIR
    from copilot.kb.review import (
        OVERLAY_FILE,
        ReviewError,
        import_decisions,
        read_overlay,
        write_overlay,
    )

    directory = kb_dir or DEFAULT_KB_DIR
    if not path.is_file():
        typer.echo(f"找不到 {path}", err=True)
        raise typer.Exit(1)
    overlay = read_overlay(directory / OVERLAY_FILE)
    try:
        report = import_decisions(overlay, path, read_templates(directory / TEMPLATES_FILE))
    except ReviewError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    write_overlay(overlay, directory / OVERLAY_FILE)
    for line in report.lines():
        typer.echo(line)


@review_app.command("export")
def kb_review_export(
    kb_dir: KbDirOption = None,
    force: Annotated[bool, typer.Option("--force", help="覆盖已存在的审核文件。")] = False,
) -> None:
    """把每个模板的最新版本导出为审核文件 <kb-dir>/review/<模板 ID>.yaml。"""
    from copilot.contracts import ReplyTemplate
    from copilot.kb.annotate import TEMPLATES_FILE, read_templates
    from copilot.kb.build import DEFAULT_KB_DIR
    from copilot.kb.review import REVIEW_DIR, review_yaml
    from copilot.kb.store import KnowledgeStore
    from copilot.storage import create_engine_for, upgrade_database

    directory = kb_dir or DEFAULT_KB_DIR
    url = get_settings().database_url
    upgrade_database(url)
    latest = KnowledgeStore(create_engine_for(url)).latest(ReplyTemplate)
    if not latest:
        typer.echo("数据库中还没有模板（先运行 copilot kb publish）", err=True)
        raise typer.Exit(1)
    info = {t.template.id: t for t in read_templates(directory / TEMPLATES_FILE)}
    out = directory / REVIEW_DIR
    out.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for template in sorted(
        latest.values(), key=lambda t: info[t.id].rank if t.id in info else 10**6
    ):
        path = out / f"{template.id}.yaml"
        if path.exists() and not force:
            skipped += 1
            continue
        meta = info.get(template.id)
        details = {"状态": template.review_status.value, "意图": template.intent}
        if meta is not None:
            details |= {
                "覆盖率名次": f"#{meta.rank}（{meta.coverage:.1%}）",
                "证据": meta.evidence.value,
                "限定事故或期间": "是" if meta.time_bound else "否",
                "建议的公告": meta.suggested_notice_id or "无",
            }
        path.write_text(review_yaml(template, details), encoding="utf-8")
        written += 1
    typer.echo(f"已写入 {out}：{written} 个审核文件 · 已存在而跳过 {skipped} 个（--force 覆盖）")


@review_app.command("import")
def kb_review_import(kb_dir: KbDirOption = None, knowledge_dir: KnowledgeOption = None) -> None:
    """导入 <kb-dir>/review/ 中修改过的审核文件。之后运行 copilot kb publish。"""
    from datetime import datetime

    from copilot.ingest.timestamps import JST
    from copilot.kb.review import (
        OVERLAY_FILE,
        REVIEW_DIR,
        ReviewError,
        import_review_files,
        read_overlay,
        write_overlay,
    )

    directory, knowledge = _load_review_inputs(kb_dir, knowledge_dir)
    overlay = read_overlay(directory / OVERLAY_FILE)
    try:
        report = import_review_files(
            overlay, directory / REVIEW_DIR, knowledge, datetime.now(JST).replace(microsecond=0)
        )
    except ReviewError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    write_overlay(overlay, directory / OVERLAY_FILE)
    for line in report.lines():
        typer.echo(line)


@kb_app.command("publish")
def kb_publish(kb_dir: KbDirOption = None, knowledge_dir: KnowledgeOption = None) -> None:
    """把 knowledge/、模板草稿和覆盖层合并后写入数据库（DATABASE_URL）。只插入新版本。"""
    from copilot.contracts import ReplyTemplate
    from copilot.kb.annotate import TEMPLATES_FILE, read_templates
    from copilot.kb.review import OVERLAY_FILE, ReviewError, merge, read_overlay
    from copilot.kb.store import ImmutableEntryError, KnowledgeStore
    from copilot.storage import create_engine_for, upgrade_database

    directory, knowledge = _load_review_inputs(kb_dir, knowledge_dir)
    drafts = read_templates(directory / TEMPLATES_FILE)
    if not drafts:
        typer.echo(
            f"找不到模板：{directory / TEMPLATES_FILE}（先运行 copilot kb annotate）", err=True
        )
        raise typer.Exit(1)
    url = get_settings().database_url
    upgrade_database(url)
    store = KnowledgeStore(create_engine_for(url))
    try:
        knowledge_counts = store.add_all(knowledge.entries())
        new, report = merge(
            drafts, read_overlay(directory / OVERLAY_FILE), knowledge, store.latest(ReplyTemplate)
        )
        store.add_all(new)
    except (ImmutableEntryError, ReviewError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    report.knowledge = knowledge_counts
    for line in report.lines():
        typer.echo(line)


def _llm_client(settings: "Settings") -> "LLMClient":
    """起草使用的 LLM 客户端。测试中替换为 fake。"""
    from copilot.llm import OpenAICompatibleClient

    return OpenAICompatibleClient(settings)


def _embedding_client(settings: "Settings") -> "EmbeddingClient | None":
    """相似案件检索使用的 embedding 客户端；没有配置时返回 None（只用 BM25）。"""
    from copilot.config import MissingConfigError
    from copilot.llm import OpenAIEmbeddingClient

    try:
        settings.embedding_endpoint()
    except MissingConfigError:
        return None
    return OpenAIEmbeddingClient(settings)


EMBEDDING_CACHE = Path("data/processed/embeddings.jsonl")
"""相似案件检索的 embedding 缓存（含客户文本的哈希和向量，被 git 忽略）。"""


def _parse_now(value: str | None) -> "datetime | None":
    from datetime import datetime

    from copilot.ingest.timestamps import JST

    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        typer.echo(f"--now 不是 ISO 8601 时间：{value}", err=True)
        raise typer.Exit(2) from None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=JST)


@app.command("draft")
def draft_command(
    case_id: Annotated[str, typer.Argument(help="案件 ID（线程 ID）。")],
    cases_path: Annotated[
        Path | None,
        typer.Option("--cases", help="导入结果（copilot ingest）。默认 data/interim/cases.jsonl。"),
    ] = None,
    split_path: Annotated[
        Path | None,
        typer.Option(
            "--split",
            help="切分结果。存在时相似案件只从 train 中检索（D-012）。"
            "默认 data/processed/split.json。",
        ),
    ] = None,
    knowledge_dir: KnowledgeOption = None,
    now: Annotated[
        str | None,
        typer.Option(
            "--now",
            help="模拟的“现在”（ISO 8601，不带时区时按 JST）。指定时为历史重放：知识库只看当时"
            "已知的版本。默认取系统时间。",
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="输出完整结果的 JSON。")] = False,
    out: Annotated[
        Path | None, typer.Option("--out", help="把完整结果的 JSON 写到这个文件。")
    ] = None,
) -> None:
    """为一个案件生成分析、草稿和分流结果（#6）。不会发送任何邮件。"""
    import asyncio
    import time
    from datetime import datetime

    from copilot.config import LLMTask, MissingConfigError
    from copilot.draft.retrieval import load_snapshot
    from copilot.draft.service import DraftParams, draft_case
    from copilot.draft.similar import EmbeddingCache, SimilarCaseRetriever, corpus_before
    from copilot.eval import DEFAULT_MANIFEST, Split, StaleSplitError, read_manifest
    from copilot.ingest import DEFAULT_OUTPUT, read_cases
    from copilot.ingest.timestamps import JST
    from copilot.kb.knowledge import KNOWLEDGE_DIR, KnowledgeError, load_knowledge
    from copilot.kb.store import KnowledgeStore
    from copilot.storage import create_engine_for, upgrade_database

    settings = get_settings()
    try:
        models = {task: settings.llm_endpoint(task).model for task in (
            LLMTask.ANALYZE, LLMTask.DRAFT, LLMTask.BACKTRANSLATE
        )}  # fmt: skip
    except MissingConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    source = cases_path or DEFAULT_OUTPUT
    if not source.is_file():
        typer.echo(f"找不到导入结果：{source}（先运行 copilot ingest）", err=True)
        raise typer.Exit(1)
    cases = read_cases(source)
    target = next((c for c in cases if c.id == case_id), None)
    if target is None:
        typer.echo(f"找不到案件 {case_id!r}", err=True)
        raise typer.Exit(1)
    try:
        guide = load_knowledge(knowledge_dir or KNOWLEDGE_DIR).style_guide
    except KnowledgeError as exc:
        typer.echo(f"knowledge/ 校验失败：{exc}", err=True)
        raise typer.Exit(1) from exc
    upgrade_database(settings.database_url)
    snapshot = load_snapshot(KnowledgeStore(create_engine_for(settings.database_url)))
    if not snapshot.templates:
        typer.echo("数据库中还没有模板（先运行 copilot kb publish）", err=True)
        raise typer.Exit(1)

    manifest_path = split_path or DEFAULT_MANIFEST
    corpus = cases
    if manifest_path.is_file():
        try:
            corpus = read_manifest(manifest_path).select(cases, Split.TRAIN)
        except StaleSplitError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
    embedder = _embedding_client(settings)
    cache = EmbeddingCache(EMBEDDING_CACHE) if embedder is not None else None
    retriever = SimilarCaseRetriever(corpus_before(corpus, target), embedder=embedder, cache=cache)
    llm = _llm_client(settings)
    simulated = _parse_now(now)
    params = DraftParams(
        analyze_model=models[LLMTask.ANALYZE],
        draft_model=models[LLMTask.DRAFT],
        min_confidence=settings.draft_auto_send_min_confidence,
        lookback_days=settings.draft_problem_time_lookback_days,
    )
    started = time.perf_counter()
    outcome = asyncio.run(
        draft_case(
            target,
            snapshot=snapshot,
            guide=guide,
            llm=llm,
            params=params,
            history=cases,
            retriever=retriever,
            now=simulated,
            replay=simulated is not None,
            created_at=datetime.now(JST).replace(microsecond=0),
        )
    )
    elapsed = time.perf_counter() - started
    if cache is not None:
        cache.save()
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(outcome.model_dump_json(indent=2), encoding="utf-8")
    if as_json:
        typer.echo(outcome.model_dump_json(indent=2))
        return
    for line in _outcome_lines(outcome):
        typer.echo(line)
    typer.echo("")
    typer.echo(
        f"用时 {elapsed:.1f} 秒（相似案件：{'BM25 + embedding' if embedder else '只用 BM25'}）"
    )
    for line in llm.usage.lines():
        typer.echo(f"  {line}")


def _outcome_lines(outcome: "DraftOutcome") -> list[str]:
    """给客服看的摘要（中文）。"""
    a, ctx = outcome.analysis, outcome.time_context
    lines = [
        f"案件 {a.case_id} · 分析 seq {a.message_seq}",
        f"现在：{ctx.now.status.value} · 问题发生时间：{ctx.problem_time.status.value}"
        f"（{ctx.problem_time.basis_zh or '无'}）",
        f"意图：{a.intent}（置信度 {'无' if a.intent_confidence is None else a.intent_confidence}）"
        f" · 情绪：{a.sentiment.value} · 紧急程度：{a.urgency.value}",
        f"摘要：{a.summary_zh}",
    ]
    if a.injection_signals:
        lines.append(f"疑似提示注入：{'；'.join(a.injection_signals)}")
    if a.thread_judgement:
        lines.append(f"线程判断：{a.thread_judgement.value}（{a.thread_judgement_reason_zh}）")
    if outcome.no_reply is not None:
        state = "自动生效" if outcome.no_reply_auto else "需要人工确认"
        lines.append(f"不回复（{state}）：{outcome.no_reply_reason_zh}")
    draft = outcome.draft
    if draft is None:
        lines.append(
            f"建议状态：{outcome.proposed_status.value if outcome.proposed_status else '无'}"
        )
        return lines
    lines += ["", f"分流：{draft.route.value}"]
    lines += [f"  - {reason}" for reason in draft.route_reasons]
    template = draft.template_refs[0] if draft.template_refs else None
    lines.append(
        f"模板：{template.template_id} v{template.template_version}（{template.reason_zh}）"
        if template
        else "模板：无"
    )
    lines.append("检查：")
    lines += [
        f"  {'通过' if c.passed else '不通过'} {c.kind.value}"
        + (f"：{c.detail_zh}" if c.detail_zh else "")
        for c in draft.checks
    ]
    if draft.similar_case_refs:
        lines.append("相似案件：" + "、".join(r.case_id for r in draft.similar_case_refs))
    lines += ["", "草稿：", draft.body_ja, "", "回译：", draft.back_translation_zh]
    return lines
