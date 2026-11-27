#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "questionary==2.1.1",
#     "rich==15.0.0",
# ]
# ///
"""坐席助手的运维脚本：部署、重新部署、健康检查、日志与停止（#14）。

不带参数在终端中运行时显示交互菜单；每个菜单项都有等价的子命令，方便在脚本和 CI 中调用：

    deploy/manage.sh check       环境检查
    deploy/manage.sh deploy      首次部署
    deploy/manage.sh redeploy    重新部署（先备份数据库）
    deploy/manage.sh status      容器状态与健康检查
    deploy/manage.sh logs        查看日志
    deploy/manage.sh down        停止并删除容器（默认保留数据）

服务器上只需要 Docker（含 Compose v2）和 Python 3.9+。依赖同时写在上方的 PEP 723 元数据
（供 `uv run --script`）和 deploy/requirements.txt（供 pip）中，测试会检查两者一致。
用法详见 deploy/README.md。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_DIR = DEPLOY_DIR.parent
COMPOSE_FILE = DEPLOY_DIR / "compose.yaml"
SERVICES = ("backend", "frontend")
DB_FILENAME = "copilot.db"
# SQLite 的 WAL 模式会在数据库旁边生成这两个文件，删除数据库时一起删除。
DB_SIDECARS = ("-wal", "-shm")
HEALTH_PATH = "/v1/health"
DEFAULT_WAIT_SECONDS = 120

# 应用必须配置的 LLM 任务，对应 backend/copilot/config.py 的 LLMTask。
# 评测（judge）只在离线评测时用到，不是部署的必填项。
REQUIRED_LLM_TASKS = ("analyze", "draft", "backtranslate")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
HttpGet = Callable[[str, float], int]


# --- 配置 ---------------------------------------------------------------------


def parse_env_file(path: Path) -> dict[str, str]:
    """读取 KEY=VALUE 格式的 env 文件。忽略空行和注释，去掉值两侧的引号。"""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def missing_app_env(values: dict[str, str]) -> list[str]:
    """应用 .env 中缺少的必填变量名（只返回变量名，绝不返回值）。

    规则与 backend/copilot/config.py 一致：任务专用的端点和 key 优先，没有时回落到全局的
    LLM_BASE_URL、LLM_API_KEY；embedding 的端点和 key 也回落到全局设置。
    """

    def filled(*names: str) -> bool:
        return any(values.get(name, "").strip() for name in names)

    missing: set[str] = set()
    for task in REQUIRED_LLM_TASKS:
        suffix = task.upper()
        if not filled(f"LLM_MODEL_{suffix}"):
            missing.add(f"LLM_MODEL_{suffix}")
        if not filled(f"LLM_BASE_URL_{suffix}", "LLM_BASE_URL"):
            missing.add("LLM_BASE_URL")
        if not filled(f"LLM_API_KEY_{suffix}", "LLM_API_KEY"):
            missing.add("LLM_API_KEY")
    if not filled("EMBEDDING_MODEL"):
        missing.add("EMBEDDING_MODEL")
    if not filled("EMBEDDING_BASE_URL", "LLM_BASE_URL"):
        missing.add("LLM_BASE_URL")
    if not filled("EMBEDDING_API_KEY", "LLM_API_KEY"):
        missing.add("LLM_API_KEY")
    return sorted(missing)


@dataclass
class Settings:
    """部署参数。优先级：进程环境变量 > deploy/.env > 默认值，与 docker compose 一致。"""

    repo_dir: Path = REPO_DIR
    port: int = 8080
    bind: str = "127.0.0.1"
    data_dir: Path = REPO_DIR / "data"
    uid: str | None = None
    gid: str | None = None

    @classmethod
    def load(cls, repo_dir: Path = REPO_DIR, environ: dict[str, str] | None = None) -> Settings:
        deploy_dir = repo_dir / "deploy"
        values: dict[str, str] = {}
        deploy_env = deploy_dir / ".env"
        if deploy_env.is_file():
            values.update(parse_env_file(deploy_env))
        env = os.environ if environ is None else environ
        values.update({k: v for k, v in env.items() if k.startswith("COPILOT_")})

        def get(name: str) -> str | None:
            value = values.get(name, "").strip()
            return value or None

        data_dir = Path(get("COPILOT_DATA_DIR") or "../data")
        if not data_dir.is_absolute():
            # 与 compose 一致：相对路径相对于 deploy/ 解析。
            data_dir = (deploy_dir / data_dir).resolve()
        return cls(
            repo_dir=repo_dir,
            port=int(get("COPILOT_PORT") or 8080),
            bind=get("COPILOT_BIND") or "127.0.0.1",
            data_dir=data_dir,
            uid=get("COPILOT_UID"),
            gid=get("COPILOT_GID"),
        )

    @property
    def app_env_file(self) -> Path:
        return self.repo_dir / ".env"

    @property
    def compose_file(self) -> Path:
        return self.repo_dir / "deploy" / "compose.yaml"

    @property
    def db_path(self) -> Path:
        return self.data_dir / DB_FILENAME

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "deploy" / "backups"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "deploy" / "state.json"

    @property
    def health_url(self) -> str:
        host = "127.0.0.1" if self.bind in ("", "0.0.0.0", "::") else self.bind
        return f"http://{host}:{self.port}{HEALTH_PATH}"

    def container_user(self) -> tuple[str, str]:
        """后端容器的 UID 和 GID：显式设置的优先，否则用数据目录的属主。"""
        if self.uid and self.gid:
            return self.uid, self.gid
        try:
            stat = self.data_dir.stat()
        except OSError:
            # 数据目录不存在（check 会报告），回落到镜像中的 copilot 用户。
            return self.uid or "10001", self.gid or "10001"
        return self.uid or str(stat.st_uid), self.gid or str(stat.st_gid)


# --- 外部调用（测试中替换） ---------------------------------------------------


def run_command(
    args: Sequence[str],
    *,
    capture: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """运行外部命令。capture=False 时输出直接显示在终端。"""
    return subprocess.run(
        list(args),
        capture_output=capture,
        text=True,
        check=check,
        env=None if env is None else {**os.environ, **env},
    )


def http_get_status(url: str, timeout: float) -> int:
    """GET 请求，返回 HTTP 状态码；连不上时返回 0。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status: int = response.status
            return status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return 0


class StepFailed(RuntimeError):
    """部署步骤失败。消息会展示给运维人员。"""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warning: bool = False
    """只是提醒，不影响部署。"""


@dataclass
class Manager:
    settings: Settings
    # soft_wrap：不在终端宽度处折行，输出中的路径和命令可以直接复制。
    console: Console = field(default_factory=lambda: Console(soft_wrap=True))
    run: Runner = run_command
    http_get: HttpGet = http_get_status
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    # --- docker compose ---

    def compose(
        self, *args: str, capture: bool = False, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        uid, gid = self.settings.container_user()
        env = {
            "COPILOT_UID": uid,
            "COPILOT_GID": gid,
            "COPILOT_DATA_DIR": str(self.settings.data_dir),
            "COPILOT_PORT": str(self.settings.port),
            "COPILOT_BIND": self.settings.bind,
        }
        cmd = ["docker", "compose", "-f", str(self.settings.compose_file), *args]
        return self.run(cmd, capture=capture, check=check, env=env)

    def step(self, title: str, *args: str) -> None:
        self.console.print(f"[bold cyan]==>[/] {title}")
        try:
            self.compose(*args)
        except subprocess.CalledProcessError as exc:
            raise StepFailed(f"{title}失败（退出码 {exc.returncode}）") from exc

    # --- 检查 ---

    def collect_checks(self) -> list[Check]:
        checks: list[Check] = []

        docker = self.run(
            ["docker", "version", "--format", "{{.Server.Version}}"], capture=True, check=False
        )
        checks.append(
            Check(
                "Docker",
                docker.returncode == 0,
                f"服务端 {docker.stdout.strip()}"
                if docker.returncode == 0
                else "无法连接 Docker。确认已安装 Docker 且当前用户有权限（例如在 docker 组中）",
            )
        )

        compose = self.run(["docker", "compose", "version", "--short"], capture=True, check=False)
        version = compose.stdout.strip().lstrip("v")
        major = version.split(".")[0]
        compose_ok = compose.returncode == 0 and major.isdigit() and int(major) >= 2
        checks.append(
            Check(
                "Docker Compose v2",
                compose_ok,
                version
                if compose_ok
                else "需要 Docker Compose v2（`docker compose`，不是旧版的 `docker-compose`）",
            )
        )

        env_file = self.settings.app_env_file
        if env_file.is_file():
            checks.append(Check(".env", True, str(env_file)))
            missing = missing_app_env(parse_env_file(env_file))
            checks.append(
                Check(
                    "必填变量",
                    not missing,
                    "已全部填写" if not missing else "未填写：" + "、".join(missing),
                )
            )
        else:
            checks.append(
                Check(".env", False, f"不存在。执行 cp .env.example .env 后填写（{env_file}）")
            )

        checks.extend(self._data_dir_checks())
        return checks

    def _data_dir_checks(self) -> list[Check]:
        data_dir = self.settings.data_dir
        if not data_dir.is_dir():
            return [Check("数据目录", False, f"不存在：{data_dir}")]
        checks = [Check("数据目录", True, str(data_dir))]
        owner = data_dir.stat().st_uid
        if owner == 0 and not self.settings.uid:
            checks.append(
                Check(
                    "数据目录属主",
                    False,
                    "属于 root，后端会以 root 运行。请改为部署用户所有：sudo chown -R <用户> "
                    + str(data_dir),
                )
            )
        elif not os.access(data_dir, os.W_OK):
            checks.append(Check("数据目录属主", False, "当前用户没有写权限，无法备份数据库"))
        app_env = (
            parse_env_file(self.settings.app_env_file)
            if self.settings.app_env_file.is_file()
            else {}
        )
        sample = app_env.get("SAMPLE_DATA_PATH", "")
        if sample.startswith("data/"):
            found = (data_dir / sample[len("data/") :]).is_file()
            checks.append(
                Check(
                    "样本数据",
                    found,
                    "已找到"
                    if found
                    else "未找到。服务可以启动，但导入前需要放好样本表格（见 data/README.md）",
                    warning=not found,
                )
            )
        return checks

    def check(self) -> int:
        checks = self.collect_checks()
        table = Table(title="环境检查", show_header=True)
        table.add_column("项目")
        table.add_column("结果")
        table.add_column("说明", overflow="fold")
        for item in checks:
            mark = (
                "[green]通过[/]"
                if item.ok
                else ("[yellow]提醒[/]" if item.warning else "[red]未通过[/]")
            )
            table.add_row(item.name, mark, item.detail)
        self.console.print(table)
        failed = [c for c in checks if not c.ok and not c.warning]
        return 1 if failed else 0

    # --- 健康检查 ---

    def wait_healthy(self, timeout: float = DEFAULT_WAIT_SECONDS) -> bool:
        """轮询经 nginx 暴露的 /v1/health，直到返回 200 或超时。"""
        url = self.settings.health_url
        deadline = self.monotonic() + timeout
        while True:
            if self.http_get(url, 3.0) == 200:
                self.console.print(f"[green]健康检查通过[/]：{url}")
                return True
            if self.monotonic() >= deadline:
                self.console.print(f"[red]健康检查超时[/]：{url}")
                return False
            self.sleep(2.0)

    # --- 备份与部署记录 ---

    def backup_database(self) -> Path | None:
        """用 SQLite 的在线备份接口复制数据库，运行中也能得到一致的副本。

        没有数据库时返回 None。
        """
        db = self.settings.db_path
        if not db.is_file():
            self.console.print("[bold cyan]==>[/] 备份数据库：数据库不存在，跳过")
            return None
        self.settings.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.now().strftime("%Y%m%dT%H%M%SZ")
        target = self.settings.backup_dir / f"copilot-{stamp}.db"
        source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
        self.console.print(f"[bold cyan]==>[/] 备份数据库：{target}")
        return target

    def git_revision(self) -> str:
        result = self.run(
            ["git", "-C", str(self.settings.repo_dir), "rev-parse", "--short", "HEAD"],
            capture=True,
            check=False,
        )
        if result.returncode != 0:
            return "unknown"
        revision = result.stdout.strip()
        dirty = self.run(
            ["git", "-C", str(self.settings.repo_dir), "status", "--porcelain"],
            capture=True,
            check=False,
        )
        return revision + ("+dirty" if dirty.returncode == 0 and dirty.stdout.strip() else "")

    def read_state(self) -> dict[str, str]:
        try:
            data: dict[str, str] = json.loads(self.settings.state_file.read_text(encoding="utf-8"))
            return data
        except (OSError, ValueError):
            return {}

    def write_state(self, revision: str) -> None:
        self.settings.state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {"revision": revision, "deployed_at": self.now().isoformat()}
        self.settings.state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # --- 操作 ---

    def _build_migrate_start(self, *, recreate: bool) -> None:
        self.step("构建镜像", "build")
        self.step("迁移数据库", "run", "--rm", "--no-deps", "backend", "copilot", "db", "upgrade")
        up = ["up", "-d", "--wait", "--wait-timeout", str(DEFAULT_WAIT_SECONDS)]
        if recreate:
            up.append("--force-recreate")
        self.step("启动容器", *up)
        if not self.wait_healthy():
            raise StepFailed(
                "服务已启动，但无法经端口访问 /v1/health。检查 COPILOT_BIND、COPILOT_PORT 和防火墙"
            )

    def deploy(self) -> int:
        if self.check() != 0:
            self.console.print("[red]环境检查未通过，已停止。[/]")
            return 1
        revision = self.git_revision()
        try:
            self._build_migrate_start(recreate=False)
        except StepFailed as exc:
            self.console.print(f"[red]部署失败[/]：{exc}。查看日志：deploy/manage.sh logs")
            return 1
        self.write_state(revision)
        self.console.print(
            f"[green]部署完成[/]（{revision}）：{self.settings.health_url.removesuffix(HEALTH_PATH)}/"
        )
        return 0

    def redeploy(self) -> int:
        if self.check() != 0:
            self.console.print("[red]环境检查未通过，已停止。[/]")
            return 1
        previous = self.read_state().get("revision")
        revision = self.git_revision()
        backup = self.backup_database()
        try:
            self._build_migrate_start(recreate=True)
        except StepFailed as exc:
            self.console.print(f"[red]重新部署失败[/]：{exc}")
            self.print_rollback_hint(backup, previous)
            return 1
        self.write_state(revision)
        self.console.print(f"[green]重新部署完成[/]（{previous or '未知'} → {revision}）")
        return 0

    def print_rollback_hint(self, backup: Path | None, previous: str | None) -> None:
        lines = ["回滚步骤："]
        lines.append("  1. deploy/manage.sh down")
        if backup:
            db = self.settings.db_path
            lines.append(f"  2. cp {backup} {db}，并删除 {db}-wal 和 {db}-shm（如果存在）")
        else:
            lines.append("  2. （本次没有数据库备份，无需恢复数据库）")
        target = previous.removesuffix("+dirty") if previous else "<上次部署的提交>"
        lines.append(f"  3. git checkout {target}")
        lines.append("  4. deploy/manage.sh deploy")
        lines.append("先查看日志找出原因：deploy/manage.sh logs backend")
        self.console.print("\n".join(lines), markup=False)

    def status(self) -> int:
        self.compose("ps", "--all", check=False)
        status = self.http_get(self.settings.health_url, 3.0)
        if status == 200:
            self.console.print(f"[green]健康[/]：{self.settings.health_url} 返回 200")
            state = self.read_state()
            if state:
                self.console.print(
                    f"当前部署：{state.get('revision')}（{state.get('deployed_at')}）", markup=False
                )
            return 0
        detail = "无法连接" if status == 0 else f"返回 {status}"
        self.console.print(f"[red]不健康[/]：{self.settings.health_url} {detail}")
        return 1

    def logs(self, service: str | None = None, follow: bool = False, tail: int = 200) -> int:
        args = ["logs", "--tail", str(tail)]
        if follow:
            args.append("--follow")
        if service:
            args.append(service)
        # 跟踪日志时用 Ctrl+C 退出，不当作错误。
        with contextlib.suppress(KeyboardInterrupt):
            self.compose(*args, check=False)
        return 0

    def down(
        self, delete_data: bool = False, confirmed: bool = False, ask: Callable[[str], str] = input
    ) -> int:
        if delete_data and not confirmed:
            if not sys.stdin.isatty():
                self.console.print("[red]删除数据需要确认。非交互环境中请加 --yes。[/]")
                return 1
            answer = ask(f"将删除数据库 {self.settings.db_path}，无法恢复。输入 delete 确认：")
            if answer.strip() != "delete":
                self.console.print("已取消。")
                return 1
        try:
            self.step("停止并删除容器", "down")
        except StepFailed as exc:
            self.console.print(f"[red]{exc}[/]")
            return 1
        if not delete_data:
            self.console.print(f"容器已删除，数据保留在 {self.settings.data_dir}", markup=False)
            return 0
        for suffix in ("", *DB_SIDECARS):
            path = Path(f"{self.settings.db_path}{suffix}")
            if path.exists():
                path.unlink()
                self.console.print(f"已删除 {path}", markup=False)
        self.console.print("样本数据和数据库备份没有删除。")
        return 0


# --- 交互菜单 -----------------------------------------------------------------


def interactive(manager: Manager) -> int:
    import questionary

    actions = {
        "环境检查": manager.check,
        "首次部署": manager.deploy,
        "重新部署": manager.redeploy,
        "状态与健康": manager.status,
        "日志": None,
        "停止": None,
        "退出": None,
    }
    while True:
        choice = questionary.select("要做什么？", choices=list(actions)).ask()
        if choice is None or choice == "退出":
            return 0
        if choice == "日志":
            service = questionary.select("哪个服务？", choices=["全部", *SERVICES]).ask()
            if service is None:
                continue
            follow = questionary.confirm("持续跟踪（Ctrl+C 退出）？", default=False).ask()
            manager.logs(None if service == "全部" else service, follow=bool(follow))
        elif choice == "停止":
            delete = questionary.confirm("同时删除数据库？（默认保留）", default=False).ask()
            if delete is None:
                continue
            manager.down(
                delete_data=bool(delete), ask=lambda prompt: questionary.text(prompt).ask() or ""
            )
        else:
            action = actions[choice]
            assert action is not None
            action()
        questionary.press_any_key_to_continue("按任意键返回菜单").ask()


# --- 命令行 -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deploy/manage.sh",
        description="坐席助手的运维脚本。不带参数时显示交互菜单。详见 deploy/README.md。",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="检查 Docker、Compose、.env 和数据目录")
    sub.add_parser("deploy", help="首次部署：检查 → 构建 → 迁移 → 启动 → 等待健康检查")
    sub.add_parser(
        "redeploy", help="重新部署：检查 → 备份数据库 → 构建 → 迁移 → 重建容器 → 等待健康检查"
    )
    sub.add_parser("status", help="容器状态与 /v1/health")
    logs = sub.add_parser("logs", help="查看日志")
    logs.add_argument("service", nargs="?", choices=SERVICES, help="只看某个服务，默认全部")
    logs.add_argument("-f", "--follow", action="store_true", help="持续跟踪")
    logs.add_argument("--tail", type=int, default=200, help="显示最后多少行，默认 200")
    down = sub.add_parser("down", help="停止并删除容器，默认保留数据")
    down.add_argument(
        "--delete-data", action="store_true", help="同时删除数据库（不删除样本数据和备份）"
    )
    down.add_argument("--yes", action="store_true", help="跳过删除数据的确认（用于脚本）")
    return parser


def main(argv: Sequence[str] | None = None, manager: Manager | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manager = manager or Manager(Settings.load())
    if args.command is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            return interactive(manager)
        parser.print_help()
        return 2
    if args.command == "logs":
        return manager.logs(args.service, follow=args.follow, tail=args.tail)
    if args.command == "down":
        return manager.down(delete_data=args.delete_data, confirmed=args.yes)
    command: Callable[[], int] = getattr(manager, args.command)
    return command()


if __name__ == "__main__":
    sys.exit(main())
