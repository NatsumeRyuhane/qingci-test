# agent-conversations/

与 AI Agent 协作开发本项目的会话记录，按会话导出，一个会话一个文件，命名为 `YYYY-MM-DD-<主题>.md`。这些原始记录是交付物 C（AI 协作过程）的素材；最终提交时如何整理，由 issue #9 决定（D-007、D-009）。

| 文件 | 内容 |
|---|---|
| [2026-11-26-project-kickoff.md](2026-11-26-project-kickoff.md) | 项目启动：阅读题目与数据、确定坐席助手方案、搭建仓库脚手架、在 issue 中写出开发计划、改为中文 |

## 导出方式

Claude Code 会话用 [`scripts/export_claude_session.py`](../scripts/export_claude_session.py) 导出：

```sh
python3 -I scripts/export_claude_session.py \
  ~/.claude/projects/<项目目录名>/<session-id>.jsonl \
  agent-conversations/YYYY-MM-DD-<主题>.md "会话记录：<标题>"
```

脚本会：
- 保留用户消息、助手回复、工具调用（简写）和工具结果（截断）
- 省略模型的思考过程、系统提示和运行时元数据
- 隐去邮箱地址、本地用户名和目录、SSH 密钥信息；读取样本数据的命令输出整段省略

其他工具的会话请用该工具自带的导出功能，并按下面的要求处理。

## 提交前必须检查

1. 通读一遍，确认没有密钥、个人信息，也没有大段样本数据（[data/README.md](../data/README.md)）。
2. 不要改写对话内容本身。需要说明的地方，在本 README 的表格中补充。
3. 记录只反映导出那一刻为止的内容。会话在导出之后的部分不包含在内。
