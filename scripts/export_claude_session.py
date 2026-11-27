"""把 Claude Code 会话的 JSONL 记录导出为结构化、便于审阅的 JSON。

用法：python3 -I scripts/export_claude_session.py <session.jsonl> <output.json> [标题]
会话文件位于 ~/.claude/projects/<项目目录名>/<session-id>.jsonl。

输出结构（schema_version 1）：
  {
    "schema_version": 1, "title": …, "session_id": …, "exported_at": …, "redactions": […],
    "turns": [                                   # 每条用户消息开启一轮
      {
        "turn": 1, "timestamp": …,
        "kind": "message",                       # 或 "shell_command"（用户用 ! 前缀直接执行的命令）
        "user": "用户消息原文",
        "steps": [                               # 该轮中依次发生的事
          {"type": "agent_message", "timestamp": …, "text": …},
          {"type": "tool_call", "timestamp": …, "tool": "Bash", "description": …, "input": {…},
           "result": {"is_error": false, "output": …, "omitted_lines": 0}},   # 未完成的调用为 null
          {"type": "skill_loaded", "timestamp": …},
          {"type": "shell_output", "timestamp": …, "stdout": …, "stderr": …},  # ! 命令的输出
          {"type": "harness_notice", "timestamp": …, "text": …}               # 运行环境消息，如 API 报错
        ]
      }
    ]
  }

保留：用户消息、Agent 回复、工具调用（含输入）及其结果（截断）。
省略：模型思考过程、系统提示、运行时元数据、Write 写入的文件全文（已在仓库中）。
隐去：邮箱地址、本地用户名和目录、scratchpad 路径、SSH 密钥信息，
GitHub 风格的访问令牌，
以及读取样本数据或其派生数据的命令的全部输出（见 data/README.md）。
导出后仍需人工通读一遍再提交。
"""
import html
import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_OUTPUT_LINES = 40      # 工具结果最多保留的行数
MAX_FIELD_CHARS = 4000     # 单个输入字段最多保留的字符数
LOCAL_USER = os.path.basename(os.path.expanduser("~"))
ORG_IDS: set[str] = set()  # 由 main() 预先扫描会话文件填充
# 命中即视为读取了样本数据或由它派生的数据（导入结果 data/interim/、查看器的 threads.json、
# 临时复制的 .jsonl），整段输出省略
DATA_MARKERS = ("data.json", ".xlsx", "readx.py", "sharedStrings", "data/interim", ".jsonl", "threads.json")
REDACTIONS = [
    "省略模型思考过程与系统提示",
    "隐去邮箱地址、本地用户名与目录、scratchpad 路径、SSH 密钥信息",
    "读取样本数据或其派生数据（data/interim/、.jsonl、threads.json）的命令输出整段省略",
    "读取被持久化的长输出（tool-results/ 下的文件）的结果整段省略",
    "隐去 GitHub 风格的访问令牌（如 CodeRabbit 链接中的 ghh_…）",
    f"工具结果最多保留 {MAX_OUTPUT_LINES} 行，单个输入字段最多 {MAX_FIELD_CHARS} 字符",
    "Write 工具写入的文件内容省略（以仓库中的文件为准）",
]


def redact(s: str) -> str:
    s = re.sub(r"<system-reminder>.*?</system-reminder>", "", s, flags=re.S)
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email redacted]", s)
    s = re.sub(r"/private/tmp/claude-\d+/[^\s'\"]*?/scratchpad", "$SCRATCHPAD", s)
    s = re.sub(r"/Users/[^/\s]+", "~", s)
    # 本地用户名也会出现在项目目录名（-Users-<user>-…）等处，统一隐去（区分大小写，不影响 GitHub 用户名）
    s = s.replace(LOCAL_USER, "<user>")
    s = re.sub(r"ssh-ed25519 [A-Za-z0-9+/=]+", "ssh-ed25519 [key redacted]", s)
    # GitHub 风格的令牌，例如 CodeRabbit 评论链接中的 scope=ghh_…
    s = re.sub(r"\bgh[hopsur]_[A-Za-z0-9_]{16,}", "[token redacted]", s)
    s = re.sub(r"SHA256:[A-Za-z0-9+/=]+", "SHA256:[redacted]", s)
    # 报错链接中的组织 ID：完整 ID 及其前 8 位（排查命令里可能只出现前缀）都隐去
    for org_id in ORG_IDS:
        s = s.replace(org_id, "[redacted]").replace(org_id[:8], "[redacted]")
    return s.strip()


def clip(s: str, limit: int = MAX_FIELD_CHARS) -> str:
    return s if len(s) <= limit else s[:limit] + f"… [截断 {len(s) - limit} 字符]"


def tag_text(text: str, tag: str) -> str:
    """取出 <tag>…</tag> 之间的文本（反转义 HTML 实体），没有则返回空串。"""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        out.append(b.get("text", "") if b.get("type") == "text" else f"[{b.get('type')} 已省略]")
    return "\n".join(out)


def tool_input(name: str, inp: dict) -> dict:
    """返回脱敏、截断后的工具输入。"""
    shown = {}
    for k, v in inp.items():
        if name == "Write" and k == "content":
            shown["content"] = f"[文件内容已省略，共 {len(v)} 字符]"
            continue
        if isinstance(v, str):
            shown[k] = clip(redact(v))
        else:
            shown[k] = json.loads(redact(json.dumps(v, ensure_ascii=False)))
    return shown


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else "会话记录"
    ORG_IDS.update(re.findall(r"org=([0-9a-fA-F-]{36})", open(src, encoding="utf-8").read()))

    turns, pending, session_id = [], {}, None  # pending: tool_use_id -> 对应的 tool_call 步骤

    def current_steps():
        if not turns:  # 理论上不会发生：会话总是以用户消息开始
            turns.append({"turn": 1, "timestamp": None, "user": "", "steps": []})
        return turns[-1]["steps"]

    for line in open(src, encoding="utf-8"):
        o = json.loads(line)
        if o.get("type") not in ("user", "assistant"):
            continue
        session_id = session_id or o.get("sessionId")
        ts = o.get("timestamp")
        content = (o.get("message") or {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])

        for b in blocks:
            bt = b.get("type")
            if o["type"] == "user" and bt == "text":
                text = redact(b["text"])
                if not text:
                    continue
                if text.startswith("Base directory for this skill"):
                    current_steps().append({"type": "skill_loaded", "timestamp": ts})
                    continue
                if text.startswith("<bash-input>"):  # 用户在会话中用 ! 前缀直接执行的命令
                    cmd = html.unescape(re.sub(r"</?bash-input>", "", text)).strip()
                    turns.append({"turn": len(turns) + 1, "timestamp": ts, "kind": "shell_command",
                                  "user": cmd, "steps": []})
                    continue
                if text.startswith("<bash-stdout>"):  # 上一条 ! 命令的输出，归入同一轮
                    current_steps().append({"type": "shell_output", "timestamp": ts,
                                            "stdout": tag_text(text, "bash-stdout"),
                                            "stderr": tag_text(text, "bash-stderr")})
                    continue
                turns.append({"turn": len(turns) + 1, "timestamp": ts, "kind": "message",
                              "user": text, "steps": []})

            elif o["type"] == "user" and bt == "tool_result":
                step = pending.pop(b.get("tool_use_id"), None)
                if step is None:
                    continue
                raw = redact(result_text(b.get("content")))
                reads_data = step["tool"] == "Bash" and any(
                    m in json.dumps(step["input"], ensure_ascii=False) for m in DATA_MARKERS
                )
                # 被持久化的长输出（tool-results/ 下的文件）可能来自读取样本数据的命令，一律省略
                reads_persisted = step["tool"] == "Read" and "tool-results/" in str(
                    step["input"].get("file_path", "")
                )
                if reads_data or reads_persisted:
                    output, omitted = "[输出包含样本数据片段，按 data/README.md 的规则省略]", 0
                else:
                    lines = raw.splitlines()
                    keep = 3 if step["tool"] in ("Read", "Write", "Edit") else MAX_OUTPUT_LINES
                    output, omitted = "\n".join(lines[:keep]), max(0, len(lines) - keep)
                step["result"] = {"is_error": bool(b.get("is_error")), "output": output, "omitted_lines": omitted}

            elif o["type"] == "assistant" and bt == "text":
                text = redact(b["text"])
                if not text:
                    continue
                # 由运行环境生成的消息（如 API 报错）不是 Agent 说的话，单独标注
                kind = "harness_notice" if o["message"].get("model") == "<synthetic>" else "agent_message"
                current_steps().append({"type": kind, "timestamp": ts, "text": text})

            elif o["type"] == "assistant" and bt == "tool_use":
                name, inp = b.get("name"), dict(b.get("input") or {})
                step = {"type": "tool_call", "timestamp": ts, "tool": name}
                if "description" in inp:
                    step["description"] = redact(inp.pop("description"))
                step["input"] = tool_input(name, inp)
                step["result"] = None  # 由对应的 tool_result 填充
                current_steps().append(step)
                pending[b.get("id")] = step
            # thinking 等其他块类型一律省略

    doc = {
        "schema_version": 1,
        "title": title,
        "source": "Claude Code",
        "session_id": session_id,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "redactions": REDACTIONS,
        "turns": turns,
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    steps = sum(len(t["steps"]) for t in turns)
    print(f"wrote {dst}: {len(turns)} turns, {steps} steps")


if __name__ == "__main__":
    main()
