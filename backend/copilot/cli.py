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
