"""将 agent-conversations/*.json 会话记录渲染为可交互 HTML，或启动本地预览服务。

仅依赖 Python 标准库。支持三种使用方式：

1. 启动本地 HTTP 预览服务（自动加载全部会话 JSON）：
   python3 scripts/render_conversations.py --serve [--port 8765] [--open]

2. 将 viewer.html 与全部（或指定）会话打包为单个离线 HTML 文件（双击即可打开，无 CORS 限制）：
   python3 scripts/render_conversations.py --bundle -o /tmp/conversations.html

3. 将单个（或多个）指定会话渲染为独立 HTML 文件：
   python3 scripts/render_conversations.py agent-conversations/2026-11-26-project-kickoff.json -o /tmp/kickoff.html
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import sys
import urllib.parse
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONVERSATIONS_DIR = REPO_ROOT / "agent-conversations"
VIEWER_TEMPLATE = CONVERSATIONS_DIR / "viewer.html"
BUNDLE_MARKER = "<!-- __BUNDLED_SESSIONS_SCRIPT__ -->"

CANONICAL_ORDER = [
    "2026-11-26-project-kickoff.json",
    "2026-11-26-foundation-stack.json",
    "2026-11-26-contracts.json",
    "2026-11-26-ingest.json",
    "2026-11-26-kb-design-contracts.json",
    "2026-11-27-kb-pipeline.json",
    "2026-11-27-issue-6-draft.json",
    "2026-11-27-issue-7-eval.json",
]


def discover_session_files(conv_dir: Path = CONVERSATIONS_DIR) -> list[Path]:
    """按项目演进顺序返回目录下的会话 JSON 文件。"""
    by_name = {p.name: p for p in sorted(conv_dir.glob("*.json")) if p.is_file()}
    ordered: list[Path] = []
    for name in CANONICAL_ORDER:
        if name in by_name:
            ordered.append(by_name.pop(name))
    for _, remaining in sorted(by_name.items()):
        ordered.append(remaining)
    return ordered


def safe_json_for_html_script(obj: object) -> str:
    """序列化为可安全内嵌进 HTML <script> 标签的 JSON 字符串，防止 </script> 闭合注入。"""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build_bundled_html(json_paths: list[Path], template_path: Path = VIEWER_TEMPLATE) -> str:
    """读取 viewer.html 模板并将给定 JSON 会话内嵌为离线单文件 HTML。"""
    if not template_path.is_file():
        raise FileNotFoundError(f"找不到 HTML 渲染模板：{template_path}")

    template = template_path.read_text(encoding="utf-8")
    if BUNDLE_MARKER not in template:
        raise ValueError(f"模板 {template_path} 缺少内嵌占位符 {BUNDLE_MARKER}")

    sessions = []
    for p in json_paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "turns" not in data:
            raise ValueError(f"{p} 不是合法的会话导出 JSON（缺少 turns 字段）")
        sessions.append({"filename": p.name, "data": data})

    payload_js = (
        "<script>\n"
        f"window.__BUNDLED_SESSIONS__ = {safe_json_for_html_script(sessions)};\n"
        "</script>"
    )
    return template.replace(BUNDLE_MARKER, payload_js, 1)


class _ConversationRequestHandler(http.server.SimpleHTTPRequestHandler):
    """为 agent-conversations/ 提供静态文件及动态 /manifest.json。"""

    def __init__(self, *args, directory: str, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        clean_path = parsed.path

        if clean_path in ("/", "/index.html"):
            self.path = "/viewer.html" + (f"?{parsed.query}" if parsed.query else "")
            return super().do_GET()

        if clean_path == "/manifest.json":
            files = [p.name for p in discover_session_files(Path(self.directory))]
            body = json.dumps({"files": files}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        return super().do_GET()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[viewer] {fmt % args}\n")


class _ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def serve_conversations(host: str, port: int, open_browser: bool, conv_dir: Path) -> None:
    handler = functools.partial(_ConversationRequestHandler, directory=str(conv_dir))
    with _ReusableTCPServer((host, port), handler) as httpd:
        actual_port = httpd.server_address[1]
        url = f"http://{host}:{actual_port}/viewer.html"
        print(f"会话审阅器已启动：{url}", flush=True)
        print("按 Ctrl+C 停止服务。", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止预览服务。")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="渲染或预览 agent-conversations/*.json 会话记录（仅依赖 Python 标准库）。"
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="要渲染的一个或多个会话 JSON 文件（配合 --bundle 可省略，默认打包全部会话）。",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="启动本地 HTTP 预览服务器，直接打开 agent-conversations/viewer.html。",
    )
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="将 viewer.html 与全部（或指定）会话 JSON 打包为单个离线 HTML 文件。",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="输出的 HTML 文件路径（用于单文件渲染或 --bundle 模式）。",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="本地预览服务监听地址（默认：127.0.0.1）。",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="本地预览服务端口（默认：8765，传 0 自动分配空闲端口）。",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="启动 --serve 时自动在默认浏览器中打开页面。",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=CONVERSATIONS_DIR,
        help=f"会话记录所在目录（默认：{CONVERSATIONS_DIR}）。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.serve:
        serve_conversations(
            host=args.host,
            port=args.port,
            open_browser=args.open,
            conv_dir=args.dir.resolve(),
        )
        return 0

    if args.bundle or args.inputs:
        targets = [p.resolve() for p in args.inputs] if args.inputs else discover_session_files(args.dir.resolve())
        if not targets:
            print("错误：未找到任何可渲染的会话 JSON 文件。", file=sys.stderr)
            return 1
        for p in targets:
            if not p.is_file():
                print(f"错误：文件不存在：{p}", file=sys.stderr)
                return 1

        if args.output is None:
            if len(targets) == 1 and not args.bundle:
                out_path = targets[0].with_suffix(".html")
            else:
                print("错误：打包模式请通过 -o / --output 指定输出 HTML 文件路径。", file=sys.stderr)
                return 2
        else:
            out_path = args.output

        html_text = build_bundled_html(targets, template_path=args.dir.resolve() / "viewer.html")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html_text, encoding="utf-8")
        size_kb = out_path.stat().st_size / 1024
        print(f"已生成离线 HTML：{out_path}（内嵌 {len(targets)} 份会话，{size_kb:.1f} KB）")
        return 0

    print(
        "请指定运行模式：\n"
        "  预览全部会话：python3 scripts/render_conversations.py --serve\n"
        "  打包离线单文件：python3 scripts/render_conversations.py --bundle -o /tmp/conversations.html\n"
        "  渲染单个文件：python3 scripts/render_conversations.py agent-conversations/<file>.json -o /tmp/out.html",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
