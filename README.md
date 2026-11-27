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

## 设计主张与假设

系统声称做到的每件事都写在 [docs/claims.md](docs/claims.md) 中，附上保证它的代码和守护它的测试（CI 会检查引用的测试都存在）；它依赖的前提也逐条列出，写明前提不成立时会怎样。最重要的几条：

- **安全相关的判断全部由代码执行，并假设模型已被攻破。** 不允许的承诺、时间窗口、可以提供给模型的知识、自动发送和“不回复”都由代码决定，对抗测试用完全听从注入的模型输出来证明这一点。
- **只有所有者批准的知识会提供给模型。** 所有者的审核是信任根；样本没有发件人元数据，角色只能从文本判断，所以知识库不会根据新的邮件线程自动更新。
- **自动发送需要 12 个关卡全部通过，一个不过全部不过。** 目前没有任何模板允许自动发送，要等评测（#7）校准阈值之后，由所有者决定。
- **时间没有就是没有。** 无法确定是否在事件窗口内时转人工审核，不猜测。

模型本身的质量（翻译、注入成功率）不是 CI 能证明的，由离线评测（#7）衡量。

安全按“瑞士奶酪”模型设计：每一层都有洞，一次攻击要同时穿过所有层才能造成损害。[docs/security.md](docs/security.md) 列出各层防线、带编号的攻击目录和已知漏洞，#7 按编号重放每一种攻击，结果需要改进时据此找到修补的位置。

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
   uv run copilot kb annotate         # 用 LLM 标注模板候选（有缓存，重复运行不重复调用）
   uv run copilot kb label            # 生成评测（#7）用的标准答案
   uv run copilot kb publish          # 把 knowledge/、模板和审核结果写入数据库
   uv run copilot draft <案件 ID>     # 为一个案件生成分析、草稿和分流（不发送邮件）；--tools 允许调用只读工具
   uv run copilot eval --split dev    # 在留出集上离线评测（--suite security 重放全部安全攻击目录）
   uv run copilot seed                # 从 dev 中挑选样本线程放进工作台，作为演示案件
   uv run copilot serve --reload      # 后端 http://127.0.0.1:8000
   (cd frontend && pnpm dev)          # 工作台 http://localhost:5173
   ```

   工作台（#8）：每个案件是一个三栏工作区，从左到右完成：左栏是摘要、分析和表单，中栏是邮件线程
   （最新的在底部），右栏是编辑器。客服用**中文**编辑回复，点“翻译为日语”由 LLM 译成日语并
   重新自检；直接改日语会先提醒，改过的日语以客服为准、不再发给 LLM。有未通过的自检时要确认后
   才能发送。底部的案件栏可以切换案件；发送、升级或“无需回复”后自动跳到下一个待处理的案件。

   **模拟邮箱**（`http://localhost:5173/#sim`）和**指标**（`#metrics`）是独立页面，演示时可以和
   工作台并排摆放：在模拟邮箱投递来信（有 26 封合成示例，含格式异常和恶意来信），它会出现在
   工作台；批准后回复进入发件箱，不会真正发出任何邮件。开启自动发送（`WORKBENCH_AUTO_SEND`，
   演示默认开启）时，分流为 `auto_send` 的草稿由系统直接发出，否则也由客服确认后发送。

   **演示**：运行 `scripts/demo.sh`。每次启动都会把演示专用的数据库（`data/processed/demo-workbench.db`，
   不是 `.env` 中的 `DATABASE_URL`）重置为初始状态：已发布的知识库加 3 个样本案件，然后启动后端和
   前端；Ctrl-C 同时停止两者。演示中的来信、草稿和发出的回复在下次启动时清空。演示配置按 D-017、
   D-018（DeepSeek v4.1 Flash、来信后自动起草、自动发送），都可以用同名环境变量覆盖；
   `scripts/demo.sh --reset` 只重置不启动。

   不想配置 LLM 时：设 `WORKBENCH_DRAFTER=stub`，草稿是标为“【占位】”的占位内容。

   备用路线下去掉 `uv run`（先激活 venv），把 `pnpm` 换成 `npx pnpm@10.34.5`。

检查命令和更多细节见 [AGENTS.md](AGENTS.md) 第 8 节。

## 文档

| 文档 | 用途 |
|---|---|
| [AGENTS.md](AGENTS.md) | 工作流程、分支保护规则、各项约定 |
| [docs/requirements.md](docs/requirements.md) | 原始测试题说明 |
| [docs/data.md](docs/data.md) | 样本数据的结构与发现 |
| [docs/architecture.md](docs/architecture.md) | 数据契约：各实体的作用、关系与约定；起草的检查与分流规则 |
| [docs/claims.md](docs/claims.md) | 设计主张与假设：每条主张由哪个测试守护 |
| [docs/security.md](docs/security.md) | 安全模型：各层防线、攻击目录、已知漏洞和修补位置 |
| [docs/eval.md](docs/eval.md) | 离线评测方法、基线指标、对比实验与安全攻击重放结果 |
| [docs/decisions.md](docs/decisions.md) | 决策记录：选了什么、为什么 |
| [docs/proposals/](docs/proposals/) | MVP 之后工作的设计提案 |
