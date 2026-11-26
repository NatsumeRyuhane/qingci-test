"""`copilot` 命令行入口。各模块的子命令（ingest、kb、draft、eval 等）随各自的 issue 加入。"""

import typer

from copilot import __version__
from copilot.config import get_settings

app = typer.Typer(help="面向中文客服的日语客服坐席助手。", no_args_is_help=True)
db_app = typer.Typer(help="数据库管理。", no_args_is_help=True)
app.add_typer(db_app, name="db")


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


@db_app.command("upgrade")
def db_upgrade(
    revision: str = typer.Argument("head", help="目标版本，默认最新。"),
) -> None:
    """把数据库（DATABASE_URL）迁移到指定版本。"""
    from copilot.storage import upgrade_database

    upgrade_database(get_settings().database_url, revision)
    typer.echo(f"数据库已迁移到 {revision}")
