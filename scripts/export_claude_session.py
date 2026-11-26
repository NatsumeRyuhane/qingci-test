"""把 Claude Code 会话的 JSONL 记录导出为便于审阅的 Markdown。

用法：python3 -I scripts/export_claude_session.py <session.jsonl> <output.md> [标题]
会话文件位于 ~/.claude/projects/<项目目录名>/<session-id>.jsonl。

保留：用户消息、助手回复、工具调用（简写）、工具结果（截断）。
省略：模型思考过程、系统提示、运行时元数据。
隐去：邮箱地址、本地用户目录、scratchpad 路径、SSH 密钥信息，
以及读取样本数据的命令的全部输出（见 data/README.md）。
导出后仍需人工通读一遍再提交。
"""
import json, os, re, sys

src, dst = sys.argv[1], sys.argv[2]
title = sys.argv[3] if len(sys.argv) > 3 else "会话记录"
MAX_RESULT_LINES = 25
MAX_CMD_CHARS = 1500
LOCAL_USER = os.path.basename(os.path.expanduser("~"))
DATA_MARKERS = ("data.json", ".xlsx", "readx.py", "sharedStrings")

def redact(s: str) -> str:
    s = re.sub(r"<system-reminder>.*?</system-reminder>", "", s, flags=re.S)
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email redacted]", s)
    s = re.sub(r"/private/tmp/claude-\d+/[^\s'\"]*?/scratchpad", "$SCRATCHPAD", s)
    s = re.sub(r"/Users/[^/\s]+", "~", s)
    # 本地用户名也会出现在项目目录名（-Users-<user>-…）和其他命令里，统一隐去（区分大小写，不影响 GitHub 用户名）
    s = s.replace(LOCAL_USER, "<user>")
    s = re.sub(r"ssh-ed25519 [A-Za-z0-9+/=]+", "ssh-ed25519 [key redacted]", s)
    s = re.sub(r"SHA256:[A-Za-z0-9+/=]+", "SHA256:[redacted]", s)
    return s.strip()

def truncate(s: str, n: int) -> str:
    lines = s.splitlines()
    if len(lines) <= n:
        return s
    return "\n".join(lines[:n]) + f"\n… [{len(lines) - n} more lines truncated]"

def fence(s: str, lang: str = "") -> str:
    ticks = "````" if "```" in s else "```"
    return f"{ticks}{lang}\n{s}\n{ticks}"

def result_text(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if b.get("type") == "text":
            out.append(b.get("text", ""))
        else:
            out.append(f"[{b.get('type')} omitted]")
    return "\n".join(out)

tool_inputs = {}  # tool_use_id -> (工具名, 输入)
parts = []
for line in open(src, encoding="utf-8"):
    o = json.loads(line)
    t = o.get("type")
    if t not in ("user", "assistant"):
        continue
    msg = o.get("message") or {}
    content = msg.get("content")
    if t == "user":
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        else:
            blocks = content or []
        for b in blocks:
            if b.get("type") == "text":
                txt = redact(b["text"])
                if not txt:
                    continue
                if txt.startswith("Base directory for this skill"):
                    parts.append("_[技能说明已加载，此处省略全文]_")
                    continue
                parts.append(f"## 👤 User\n\n{txt}")
            elif b.get("type") == "tool_result":
                name, inp = tool_inputs.get(b.get("tool_use_id"), ("?", {}))
                raw = result_text(b.get("content"))
                cmd = json.dumps(inp, ensure_ascii=False)
                if any(m in cmd for m in DATA_MARKERS) and name == "Bash":
                    body = "[输出包含样本数据片段，按 data/README.md 的规则省略]"
                elif name in ("Write", "Edit", "Read"):
                    body = truncate(redact(raw), 3)
                else:
                    body = truncate(redact(raw), MAX_RESULT_LINES)
                if body:
                    parts.append("<details><summary>结果</summary>\n\n" + fence(body) + "\n\n</details>")
    else:
        for b in content or []:
            bt = b.get("type")
            if bt == "text":
                txt = redact(b["text"])
                if txt:
                    parts.append(f"## 🤖 Assistant\n\n{txt}")
            elif bt == "tool_use":
                name, inp = b.get("name"), b.get("input") or {}
                tool_inputs[b.get("id")] = (name, inp)
                if name == "Bash":
                    desc = inp.get("description", "")
                    cmd = redact(inp.get("command", ""))
                    if len(cmd) > MAX_CMD_CHARS:
                        cmd = cmd[:MAX_CMD_CHARS] + "\n… [command truncated]"
                    parts.append(f"**🔧 Bash** — {desc}\n\n" + fence(cmd, "sh"))
                elif name in ("Write", "Edit", "Read"):
                    parts.append(f"**🔧 {name}** `{redact(inp.get('file_path', ''))}`")
                else:
                    shown = {k: v for k, v in inp.items() if k not in ("content",)}
                    s = redact(json.dumps(shown, ensure_ascii=False))
                    parts.append(f"**🔧 {name}** `{s[:300]}`")

header = f"""# {title}

由 Claude Code 会话导出。

导出时做了以下处理（见 [README](README.md)）：
- 保留用户消息、助手回复、工具调用（简写）和工具结果（截断）；省略模型的思考过程和系统提示
- 隐去邮箱地址、本地用户目录、SSH 密钥信息；读取样本数据的命令输出整段省略
- 对话内容本身未改写；原对话主要为英文，保持原文

---
"""
with open(dst, "w", encoding="utf-8") as f:
    f.write(header + "\n\n" + "\n\n".join(parts) + "\n")
print("wrote", dst, len(parts), "parts")
