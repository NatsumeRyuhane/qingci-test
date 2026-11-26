# agent-conversations/

与 AI Agent 协作开发本项目的会话记录，按会话导出，一个会话一个文件，命名为 `YYYY-MM-DD-<主题>.json`。这些原始记录是交付物 C（AI 协作过程）的素材；最终提交时如何整理，由 issue #9 决定（D-007、D-009）。

| 文件 | 内容 |
|---|---|
| [2026-11-26-project-kickoff.json](2026-11-26-project-kickoff.json) | 项目启动：阅读题目与数据、确定坐席助手方案、搭建仓库脚手架、在 issue 中写出开发计划、改为中文、导出会话 |
| [2026-11-26-foundation-stack.json](2026-11-26-foundation-stack.json) | 基础建设（#2）：仓库所有者评审技术栈（LLM 不绑定服务商、改用 React、存储预留迁移接口、测试策略、uv/pnpm 之外的备用路线），新开部署 issue #14 并调整 #1、#6、#8、#10；实现 D-010 与项目骨架、CI（PR #15）；`src/` 改名为 `backend/`；处理 CodeRabbit 的三条评审意见。会话开头是一次 `/clear`，之前的上下文不在本记录中 |
| [2026-11-26-contracts.json](2026-11-26-contracts.json) | 数据契约（#3）：实现共享 Pydantic 模型、JSON Schema 导出、合成测试数据与 `docs/architecture.md`（PR #16）；对照 #10–#14 检查前向兼容，补充版本演进规则；处理 CodeRabbit 的一条评审意见。同时起草了 #13 访问控制提案和 #14 部署（各自的本地分支，按仓库所有者要求暂缓推送）。会话开头是一次 `/clear`，之前的上下文不在本记录中；导出时隐去了 CodeRabbit 链接中的访问令牌 |
| [2026-11-26-ingest.json](2026-11-26-ingest.json) | 导入与清洗（#4）：先做了仅限本地的邮件线程查看器（`chore/4-thread-viewer`，未推送）；讨论知识库的目标与内容（整条回复为模板单位、一致事实与限时公告分开、模拟“当前时间”精确到分钟并附在查询末尾、按需检索的 ReAct 循环、时间可以未知或带标签的估计）；实现 `copilot ingest`（PR #17），在真实样本上用汇总数字核对并抽查 30 个线程；处理 CodeRabbit 的多轮评审；派子 Agent 做只读安全审计，修复 ReDoS、构造的 xlsx、异常和错误信息回显等问题，其余发现与提示注入防护记入 #18；与仓库所有者讨论后，案件状态改为只看最后一封邮件，以系统邮件结束时为 `undetermined`。会话开头是一次 `/clear`，之前的上下文不在本记录中；读取样本及其派生数据的命令输出整段省略，导出脚本为此补充了标记，并自动隐去 GitHub 令牌 |
| [2026-11-26-kb-design-contracts.json](2026-11-26-kb-design-contracts.json) | 知识库设计与契约扩展：撰写提案 0003 和 D-011（PR #20），开契约后续 issue #19，并更新 #5–#8、#12、#18；用汇总数字讨论角色证据和“不回复”（多数未回复的咨询是重复提交）；处理 CodeRabbit 的多轮评审（版本选择改为最新版本加有效期、`known_from`、重复出现只作辅助证据，仓库所有者确定 MVP 以其审核为信任根、生产需要元数据），并加入每层的对抗测试要求；按所有者要求清理分支和 worktree；实现 #19（PR #21）：知识库契约、带状态的时间、`Case.id` 字符限制、角色证据和新的状态规则（真实样本中 863 个线程从 `replied` 变为 `undetermined`）。会话开头是一次 `/clear`，之前的上下文不在本记录中；读取样本及其派生数据的命令输出按导出脚本的规则省略 |

## 格式

结构化 JSON，用户消息、Agent 回复和工具调用的边界一目了然：

```jsonc
{
  "schema_version": 1,
  "title": "…", "source": "Claude Code", "session_id": "…", "exported_at": "…",
  "redactions": ["…"],                 // 导出时做了哪些省略和隐去
  "turns": [                           // 每条用户消息开启一轮
    {
      "turn": 1,
      "timestamp": "…",
      "kind": "message",               // 或 "shell_command"：用户用 ! 前缀直接执行的命令
      "user": "用户消息原文",
      "steps": [                       // 该轮中依次发生的事
        { "type": "agent_message", "text": "…" },
        { "type": "tool_call", "tool": "Bash", "description": "…", "input": { "command": "…" },
          "result": { "is_error": false, "output": "…", "omitted_lines": 0 } },
        { "type": "skill_loaded" },
        { "type": "shell_output", "stdout": "…", "stderr": "…" },
        { "type": "harness_notice", "text": "…" }   // 运行环境消息（如 API 报错），不是 Agent 说的话
      ]
    }
  ]
}
```

快速浏览示例：

```sh
# 列出每一轮的用户消息
jq -r '.turns[] | "\(.turn) [\(.kind)] \(.user[0:80])"' agent-conversations/<文件>.json
# 只看第 3 轮中 Agent 说的话
jq -r '.turns[2].steps[] | select(.type=="agent_message") | .text' agent-conversations/<文件>.json
```

## 导出方式

Claude Code 会话用 [`scripts/export_claude_session.py`](../scripts/export_claude_session.py) 导出：

```sh
python3 -I scripts/export_claude_session.py \
  ~/.claude/projects/<项目目录名>/<session-id>.jsonl \
  agent-conversations/YYYY-MM-DD-<主题>.json "会话记录：<标题>"
```

脚本会：
- 保留用户消息、Agent 回复、工具调用（含输入）及其结果（截断）
- 省略模型的思考过程、系统提示、运行时元数据，以及 Write 写入的文件全文（以仓库中的文件为准）
- 隐去邮箱地址、本地用户名和目录、SSH 密钥信息、报错链接中的组织 ID；读取样本数据的命令输出整段省略

其他工具的会话请用该工具自带的导出功能，尽量转换为相同的结构，并按下面的要求处理。

## 提交前必须检查

1. 通读一遍，确认没有密钥、个人信息，也没有大段样本数据（[data/README.md](../data/README.md)）。
2. 不要改写对话内容本身。需要说明的地方，在本 README 的表格中补充。
3. 记录只反映导出那一刻为止的内容。会话在导出之后的部分不包含在内，最后一次工具调用的 `result` 可能为 `null`。
