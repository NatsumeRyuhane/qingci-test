# qingci-test：面向中文客服的日语客服坐席助手

一个客服坐席助手（Copilot），让**中文客服人员**能够快速、准确地回复手游『ぽちゃガチョ！』**日本玩家**的客服邮件，并使用得体的日语商务敬语。客服人员用得越多，它就越好用。

> **状态：** 目前只有项目脚手架，还没有应用代码。开发计划在 [GitHub Issues](https://github.com/NatsumeRyuhane/qingci-test/issues) 中跟踪，请从[路线图（#1）](https://github.com/NatsumeRyuhane/qingci-test/issues/1)开始看。

## 背景

本项目源自一道实践测试题（原始题目：[docs/requirements.md](docs/requirements.md)）：

- 一款在日本发行的游戏，客服由中国团队承担。
- **目标：** 解决语言障碍和日本客服的语言习惯问题，并基于由历史客服邮件构成的小型知识库，让回复尽可能做到**自动化、精准、可持续迭代**。
- **输入：** 一份脱敏样本，约 5,950 封客服邮件，约 2,870 个邮件线程（[docs/data.md](docs/data.md)）。
- **交付物：** (A) 可运行的源代码，附 README 和运行录屏；(B) 技术方案复盘；(C) 与 AI 协作开发的交互日志，这是考察重点。

我们把它当作一个真实、长期维护的项目来做：后续维护者需要的一切都写在本仓库和 issue 里，而不是留在某个人的聊天记录中。

## 方案（规划中）

**人工参与审核的客服坐席助手**，而不是全自动回复（原因见 [docs/decisions.md](docs/decisions.md) 中的 D-001）。

```
日语邮件 ──► 导入与清洗 ──► 分析 ──────────────► 起草 ────────────► 分流
                            · 中文翻译            · 检索回复模板       · 自动发送（低风险）
                            · 摘要与情绪          · 检索相似案例       · 人工审核
                            · 分类与字段提取      · 生成敬语回复       · 升级处理
                                                  · 中文回译
                                                         │
                       客服修改 / 批准 ◄─────────────────┘
                              │
                              └──► 反馈用于改进模板知识库，效果由离线评测衡量
```

| 阶段 | 范围 |
|---|---|
| **MVP** | (a) 导入与清洗 · (b) 模板知识库 · (c) 分析、检索、起草、分流 · (d) 离线评测与客服 Web 界面 |
| **后续** | (e) 用于集成的 REST API 和 A2A 端点 · 提案：Agent 循环核心、基于身份的访问控制 |

## 仓库结构

```
AGENTS.md          人类开发者与 AI Agent 共用的贡献指南（请先读）
CLAUDE.md          → 指向 AGENTS.md 的符号链接（供 Claude Code 使用）
.agents/skills/    共享的 Agent 技能（不绑定特定工具）
.claude/skills     → 指向 .agents/skills 的符号链接（供 Claude Code 使用）
data/              仅限本地的数据目录（内容永不提交）
docs/              需求、数据概况、决策记录、设计提案
```

## 上手

1. 克隆仓库，然后阅读 [AGENTS.md](AGENTS.md)。
2. 向仓库所有者索取样本数据表格，放到 `data/raw/`（见 [data/README.md](data/README.md)）。
3. 执行 `cp .env.example .env` 并填写各项值。
4. 工具链和运行说明将随基础建设 issue [#2](https://github.com/NatsumeRyuhane/qingci-test/issues/2)（技术栈选型、项目骨架、CI）一起补充。

## 文档

| 文档 | 用途 |
|---|---|
| [AGENTS.md](AGENTS.md) | 工作流程、分支保护规则、各项约定 |
| [docs/requirements.md](docs/requirements.md) | 原始测试题说明 |
| [docs/data.md](docs/data.md) | 样本数据的结构与发现 |
| [docs/decisions.md](docs/decisions.md) | 决策记录：选了什么、为什么 |
| [docs/proposals/](docs/proposals/) | MVP 之后工作的设计提案 |
