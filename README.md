# qingci-test：面向中文客服的日语客服坐席助手

一个客服坐席助手（Copilot），让**中文客服人员**能够快速、准确地回复手游『ぽちゃガチョ！』**日本玩家**的客服邮件，并使用得体的日语商务敬语。客服人员用得越多，它就越好用。

> **状态：** 已有项目骨架（后端、前端、CI），业务功能还在开发中。开发计划在 [GitHub Issues](https://github.com/NatsumeRyuhane/qingci-test/issues) 中跟踪，请从[路线图（#1）](https://github.com/NatsumeRyuhane/qingci-test/issues/1)开始看。

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
backend/copilot/   后端（Python + FastAPI，REST API /v1）
tests/             后端测试（只用合成数据）
frontend/          前端（React + TypeScript），只通过 /v1 访问后端
scripts/           辅助脚本
data/              仅限本地的数据目录（内容永不提交）
docs/              需求、数据概况、决策记录、设计提案
```

## 上手

1. 克隆仓库，然后阅读 [AGENTS.md](AGENTS.md)。
2. 向仓库所有者索取样本数据表格，放到 `data/raw/`（见 [data/README.md](data/README.md)）。
3. 准备环境。推荐用 [uv](https://docs.astral.sh/uv/) 和 [pnpm](https://pnpm.io/)：

   ```sh
   uv sync
   (cd frontend && pnpm install)
   ```

   不想装 uv 和 pnpm 时，只需要 Python 3.12 和 Node 22：

   ```sh
   python3.12 -m venv .venv && . .venv/bin/activate
   pip install -r requirements-dev.txt && pip install --no-deps -e .
   (cd frontend && npx pnpm@10.34.5 install)
   ```

4. 执行 `cp .env.example .env`，填写 LLM 端点（任意 OpenAI 兼容服务）等变量，说明见文件内注释。
5. 启动：

   ```sh
   uv run copilot db upgrade
   uv run copilot ingest              # 清洗样本表格，写出 data/interim/cases.jsonl
   uv run copilot split               # 切分为 train/dev/test，写出 data/processed/split.json
   uv run copilot kb build            # 只用 train 构建模板候选，写出 data/processed/kb/
   uv run copilot serve --reload      # 后端 http://127.0.0.1:8000
   (cd frontend && pnpm dev)          # 前端 http://localhost:5173
   ```

   备用路线下去掉 `uv run`（先激活 venv），把 `pnpm` 换成 `npx pnpm@10.34.5`。

检查命令和更多细节见 [AGENTS.md](AGENTS.md) 第 8 节。

## 文档

| 文档 | 用途 |
|---|---|
| [AGENTS.md](AGENTS.md) | 工作流程、分支保护规则、各项约定 |
| [docs/requirements.md](docs/requirements.md) | 原始测试题说明 |
| [docs/data.md](docs/data.md) | 样本数据的结构与发现 |
| [docs/architecture.md](docs/architecture.md) | 数据契约：各实体的作用、关系与约定 |
| [docs/decisions.md](docs/decisions.md) | 决策记录：选了什么、为什么 |
| [docs/proposals/](docs/proposals/) | MVP 之后工作的设计提案 |
