"""deploy/manage.py 的单元测试。Docker 和 HTTP 调用全部用假实现替换，不需要 Docker。

运行：见 deploy/README.md 的“开发与测试”。
"""

from __future__ import annotations

import importlib.util
import io
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

DEPLOY_DIR = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("manage", DEPLOY_DIR / "manage.py")
assert _spec and _spec.loader
manage = importlib.util.module_from_spec(_spec)
sys.modules["manage"] = manage
_spec.loader.exec_module(manage)

FILLED_ENV = """\
LLM_BASE_URL=https://llm.example/v1
LLM_API_KEY=super-secret-value
LLM_MODEL_ANALYZE=m
LLM_MODEL_DRAFT=m
LLM_MODEL_BACKTRANSLATE=m
EMBEDDING_MODEL=e
SAMPLE_DATA_PATH=data/raw/sample.xlsx
"""


class FakeRunner:
    """记录所有命令；按命令前缀返回预设结果。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []
        self.fail_on: set[str] = set()

    def __call__(
        self,
        args: list[str],
        *,
        capture: bool = False,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        self.envs.append(env)
        stdout, code = "", 0
        if args[:2] == ["docker", "version"]:
            stdout = "29.0.0"
        elif args[:3] == ["docker", "compose", "version"]:
            stdout = "v2.40.0"
        elif args[0] == "git" and "rev-parse" in args:
            stdout = "abc1234"
        if args[:3] == ["docker", "compose", "-f"] and args[4] in self.fail_on:
            code = 1
        if check and code != 0:
            raise subprocess.CalledProcessError(code, args)
        return subprocess.CompletedProcess(args, code, stdout, "")

    def compose_calls(self) -> list[list[str]]:
        """docker compose 的子命令及参数（去掉 `docker compose -f <file>`）。"""
        return [c[4:] for c in self.calls if c[:2] == ["docker", "compose"] and c[2] == "-f"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "sample.xlsx").write_bytes(b"synthetic")
    (tmp_path / ".env").write_text(FILLED_ENV, encoding="utf-8")
    return tmp_path


def make_manager(
    repo: Path, health: int = 200, **environ: str
) -> tuple[Any, FakeRunner, io.StringIO]:
    out = io.StringIO()
    runner = FakeRunner()
    clock = [0.0]  # 假时钟：sleep 推进时间，健康检查超时不用真的等待

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    manager = manage.Manager(
        settings=manage.Settings.load(repo, environ=environ),
        console=Console(file=out, width=200, color_system=None),
        run=runner,
        http_get=lambda url, timeout: health,
        sleep=sleep,
        monotonic=lambda: clock[0],
        now=lambda: datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc),
    )
    return manager, runner, out


# --- 配置 ---------------------------------------------------------------------


def test_parse_env_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# 注释\n\nA=1\nexport B = two \nC='quoted'\nD=\"x=y\"\nE=\nnot a line\n")
    assert manage.parse_env_file(path) == {"A": "1", "B": "two", "C": "quoted", "D": "x=y", "E": ""}


def test_missing_app_env_reports_names_only() -> None:
    missing = manage.missing_app_env({"LLM_API_KEY": "super-secret-value"})
    assert missing == [
        "EMBEDDING_MODEL",
        "LLM_BASE_URL",
        "LLM_MODEL_ANALYZE",
        "LLM_MODEL_BACKTRANSLATE",
        "LLM_MODEL_DRAFT",
    ]


def test_missing_app_env_accepts_task_specific_endpoints() -> None:
    values = {
        f"LLM_{kind}_{task}": "x"
        for task in ("ANALYZE", "DRAFT", "BACKTRANSLATE")
        for kind in ("MODEL", "BASE_URL", "API_KEY")
    }
    values |= {"EMBEDDING_MODEL": "e", "EMBEDDING_BASE_URL": "x", "EMBEDDING_API_KEY": "x"}
    assert manage.missing_app_env(values) == []


def test_settings_defaults(repo: Path) -> None:
    settings = manage.Settings.load(repo, environ={})
    assert settings.port == 8080
    assert settings.bind == "127.0.0.1"
    assert settings.data_dir == (repo / "data").resolve()
    assert settings.health_url == "http://127.0.0.1:8080/v1/health"


def test_settings_precedence(repo: Path) -> None:
    (repo / "deploy" / ".env").write_text(
        "COPILOT_PORT=9000\nCOPILOT_BIND=0.0.0.0\nCOPILOT_DATA_DIR=../srv\n"
    )
    settings = manage.Settings.load(repo, environ={"COPILOT_PORT": "9100", "UNRELATED": "1"})
    assert settings.port == 9100  # 进程环境变量优先
    assert settings.bind == "0.0.0.0"
    assert settings.data_dir == (repo / "srv").resolve()  # 相对于 deploy/
    assert settings.health_url == "http://127.0.0.1:9100/v1/health"


def test_compose_runs_as_data_dir_owner(repo: Path) -> None:
    manager, runner, _ = make_manager(repo)
    manager.compose("ps")
    stat = (repo / "data").stat()
    env = runner.envs[-1]
    assert env is not None
    assert (env["COPILOT_UID"], env["COPILOT_GID"]) == (str(stat.st_uid), str(stat.st_gid))
    assert env["COPILOT_DATA_DIR"] == str((repo / "data").resolve())


def test_explicit_uid_wins(repo: Path) -> None:
    manager, runner, _ = make_manager(repo, COPILOT_UID="1234", COPILOT_GID="5678")
    manager.compose("ps")
    env = runner.envs[-1]
    assert env is not None
    assert (env["COPILOT_UID"], env["COPILOT_GID"]) == ("1234", "5678")


# --- check --------------------------------------------------------------------


def test_check_passes(repo: Path) -> None:
    manager, _, out = make_manager(repo)
    assert manager.check() == 0
    assert "未通过" not in out.getvalue()


def test_check_never_prints_secret_values(repo: Path) -> None:
    (repo / ".env").write_text("LLM_API_KEY=super-secret-value\n")
    manager, _, out = make_manager(repo)
    assert manager.check() == 1
    assert "LLM_MODEL_DRAFT" in out.getvalue()
    assert "super-secret-value" not in out.getvalue()


def test_check_fails_without_env_file(repo: Path) -> None:
    (repo / ".env").unlink()
    manager, _, out = make_manager(repo)
    assert manager.check() == 1
    assert "cp .env.example .env" in out.getvalue()


def test_check_fails_without_data_dir(repo: Path) -> None:
    manager, _, out = make_manager(repo, COPILOT_DATA_DIR=str(repo / "missing"))
    assert manager.check() == 1
    assert "不存在" in out.getvalue()


def test_check_rejects_compose_v1(repo: Path) -> None:
    manager, runner, _ = make_manager(repo)
    original = runner.__call__

    def old_compose(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(args, 0, "1.29.2", "")
        return original(args, **kwargs)

    manager.run = old_compose
    assert manager.check() == 1


def test_missing_sample_data_is_only_a_warning(repo: Path) -> None:
    (repo / "data" / "raw" / "sample.xlsx").unlink()
    manager, _, out = make_manager(repo)
    assert manager.check() == 0
    assert "提醒" in out.getvalue()


# --- deploy / redeploy --------------------------------------------------------


def test_deploy_runs_steps_in_order(repo: Path) -> None:
    manager, runner, _ = make_manager(repo)
    assert manager.deploy() == 0
    assert runner.compose_calls() == [
        ["build"],
        ["run", "--rm", "--no-deps", "backend", "copilot", "db", "upgrade"],
        ["up", "-d", "--wait", "--wait-timeout", "120"],
    ]
    assert manager.read_state()["revision"] == "abc1234"


def test_deploy_stops_when_check_fails(repo: Path) -> None:
    (repo / ".env").unlink()
    manager, runner, _ = make_manager(repo)
    assert manager.deploy() == 1
    assert runner.compose_calls() == []


def test_deploy_fails_when_health_check_fails(repo: Path) -> None:
    manager, _, out = make_manager(repo, health=0)
    assert manager.deploy() == 1
    assert "健康检查超时" in out.getvalue()
    assert manager.read_state() == {}


def test_deploy_stops_at_failed_step(repo: Path) -> None:
    manager, runner, out = make_manager(repo)
    runner.fail_on = {"run"}
    assert manager.deploy() == 1
    assert [c[0] for c in runner.compose_calls()] == ["build", "run"]
    assert "迁移数据库失败" in out.getvalue()


def _create_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE feedback (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO feedback (body) VALUES ('合成数据')")
    conn.close()


def test_redeploy_backs_up_database_first(repo: Path) -> None:
    _create_db(repo / "data" / "copilot.db")
    manager, runner, _ = make_manager(repo)
    manager.write_state("old1234")
    assert manager.redeploy() == 0

    backup = repo / "data" / "deploy" / "backups" / "copilot-20261126T120000Z.db"
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT body FROM feedback").fetchall() == [("合成数据",)]
    conn.close()
    assert runner.compose_calls()[-1][-1] == "--force-recreate"
    assert manager.read_state()["revision"] == "abc1234"
    # 原数据库仍在
    assert (repo / "data" / "copilot.db").is_file()


def test_redeploy_without_database_skips_backup(repo: Path) -> None:
    manager, _, out = make_manager(repo)
    assert manager.redeploy() == 0
    assert "跳过" in out.getvalue()


def test_redeploy_failure_prints_rollback_hint(repo: Path) -> None:
    _create_db(repo / "data" / "copilot.db")
    manager, runner, out = make_manager(repo)
    manager.write_state("old1234")
    runner.fail_on = {"build"}
    assert manager.redeploy() == 1
    text = out.getvalue()
    assert "copilot-20261126T120000Z.db" in text
    assert "git checkout old1234" in text
    assert manager.read_state()["revision"] == "old1234"


def test_redeploy_does_not_git_pull(repo: Path) -> None:
    manager, runner, _ = make_manager(repo)
    manager.redeploy()
    git_calls = [c for c in runner.calls if c[0] == "git"]
    assert all("pull" not in c and "fetch" not in c for c in git_calls)


# --- status / logs / down -----------------------------------------------------


@pytest.mark.parametrize(("health", "code"), [(200, 0), (502, 1), (0, 1)])
def test_status(repo: Path, health: int, code: int) -> None:
    manager, runner, _ = make_manager(repo, health=health)
    assert manager.status() == code
    assert runner.compose_calls() == [["ps", "--all"]]


def test_logs_arguments(repo: Path) -> None:
    manager, runner, _ = make_manager(repo)
    manager.logs("backend", follow=True, tail=50)
    assert runner.compose_calls() == [["logs", "--tail", "50", "--follow", "backend"]]


def test_down_keeps_data_by_default(repo: Path) -> None:
    _create_db(repo / "data" / "copilot.db")
    manager, runner, _ = make_manager(repo)
    assert manager.down() == 0
    assert runner.compose_calls() == [["down"]]
    assert (repo / "data" / "copilot.db").is_file()


def test_down_delete_data_requires_confirmation(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_db(repo / "data" / "copilot.db")
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # 非交互环境
    manager, runner, _ = make_manager(repo)
    assert manager.down(delete_data=True) == 1
    assert runner.compose_calls() == []
    assert (repo / "data" / "copilot.db").is_file()


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_down_delete_data_wrong_answer_cancels(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _create_db(repo / "data" / "copilot.db")
    monkeypatch.setattr(sys, "stdin", _Tty())
    manager, runner, _ = make_manager(repo)
    assert manager.down(delete_data=True, ask=lambda prompt: "yes") == 1
    assert runner.compose_calls() == []
    assert (repo / "data" / "copilot.db").is_file()


def test_down_delete_data_with_yes(repo: Path) -> None:
    db = repo / "data" / "copilot.db"
    _create_db(db)
    Path(f"{db}-wal").write_bytes(b"")
    backups = repo / "data" / "deploy" / "backups"
    backups.mkdir(parents=True)
    (backups / "keep.db").write_bytes(b"")
    manager, _, _ = make_manager(repo)

    assert manager.down(delete_data=True, confirmed=True) == 0
    assert not db.exists()
    assert not Path(f"{db}-wal").exists()
    assert (backups / "keep.db").exists()
    assert (repo / "data" / "raw" / "sample.xlsx").exists()


# --- 命令行 -------------------------------------------------------------------


def test_main_without_command_in_non_tty_prints_help(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    manager, runner, _ = make_manager(repo)
    assert manage.main([], manager=manager) == 2
    assert runner.calls == []


def test_main_dispatches_subcommands(repo: Path) -> None:
    manager, runner, _ = make_manager(repo)
    assert manage.main(["logs", "frontend", "--tail", "10"], manager=manager) == 0
    assert manage.main(["down"], manager=manager) == 0
    assert runner.compose_calls() == [["logs", "--tail", "10", "frontend"], ["down"]]


# --- 依赖声明 -----------------------------------------------------------------


def _pep723_dependencies(script: str) -> list[str]:
    block = re.search(r"^# /// script\n(.*?)^# ///$", script, re.S | re.M)
    assert block, "manage.py 缺少 PEP 723 元数据"
    body = "\n".join(line[2:] for line in block.group(1).splitlines())
    deps = re.search(r"dependencies = \[(.*?)\]", body, re.S)
    assert deps
    return sorted(re.findall(r'"([^"]+)"', deps.group(1)))


def test_requirements_match_inline_metadata() -> None:
    inline = _pep723_dependencies((DEPLOY_DIR / "manage.py").read_text(encoding="utf-8"))
    requirements = sorted(
        line.strip()
        for line in (DEPLOY_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )
    assert inline == requirements
    assert all("==" in dep for dep in inline), "依赖要固定版本"
