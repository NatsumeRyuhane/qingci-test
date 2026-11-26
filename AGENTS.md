# AGENTS.md

本仓库的贡献指南，**人类开发者与 AI Agent 共用**。如果你是 AI Agent，请在第一次改动前通读全文。本文刻意保持简短。

## 1. 项目速览

- **做什么：** 一个客服坐席助手（Copilot），帮助中文客服人员回复游戏『ぽちゃガチョ！』日本玩家的邮件。详见 [README.md](README.md)。
- **为什么这样设计：** 见 [docs/decisions.md](docs/decisions.md)。提出架构调整前请先阅读。
- **数据：** [docs/data.md](docs/data.md)（数据长什么样）和 [data/README.md](data/README.md)（数据放哪里、如何处理）。
- **做哪些事：** 以 GitHub Issues 为准，除此之外没有其他任务清单（D-003）。

工具链、运行与检查命令：**尚未确定**，将由基础建设 issue（[#2](https://github.com/NatsumeRyuhane/qingci-test/issues/2)）补充到第 8 节。在此之前没有构建步骤。

## 2. Agent 配置：指令与技能

本仓库不绑定任何特定工具（D-004）。规范文件如下：

| 内容 | 规范位置 |
|---|---|
| 指令 | `AGENTS.md`（本文件） |
| 技能（skill） | `.agents/skills/<skill-name>/SKILL.md`（frontmatter 含 `name` 和 `description`，其后为说明） |

很多 Agent 原生支持读取 `AGENTS.md`。**如果你使用的工具从其他位置读取指令或技能，请先查阅该工具的文档确认位置，再用符号链接把该位置指向规范文件。** 不要复制文件，副本迟早会和原件不一致。

仓库中已有的链接：

| 工具 | 链接 | 目标 |
|---|---|---|
| Claude Code | `CLAUDE.md` | `AGENTS.md` |
| Claude Code | `.claude/skills` | `.agents/skills` |

为其他工具添加链接（用一个小 PR 提交，方便后来使用同一工具的人直接用上）：

```sh
# macOS / Linux / WSL。链接目标是相对于链接自身所在目录的路径。
ln -s AGENTS.md GEMINI.md                    # 示例：指令文件
mkdir -p .sometool && ln -s ../.agents/skills .sometool/skills   # 示例：技能目录
```

### 符号链接只是尽力而为

部分 Windows 环境不支持符号链接。这时 Git 会把每个链接检出为一个只包含目标路径的小文本文件（例如 `CLAUDE.md` 的内容只有 `AGENTS.md` 一行）。遇到这种情况：

1. 推荐：开启 Windows 开发者模式，执行 `git config core.symlinks true`，再执行 `git checkout -- CLAUDE.md .claude/skills` 重建真正的链接。
2. 否则：忽略这些失效的链接，每次会话开始时直接读取 `AGENTS.md` 和 `.agents/skills/`。**不要提交**失效的链接文件，也不要用副本替换它们。

### 本仓库的技能

| 技能 | 适用场景 |
|---|---|
| [`github-workflow`](.agents/skills/github-workflow/SKILL.md) | 认领 issue、建分支、开 PR、处理评审讨论 |
| [`sample-data`](.agents/skills/sample-data/SKILL.md) | 读取、引用或处理客服邮件样本数据 |
| [`project-docs`](.agents/skills/project-docs/SKILL.md) | 记录决策、撰写提案、更新文档 |

## 3. 基本规则

1. **为没有上下文的读者写作**（D-006）。每个 issue、PR、提交信息和文档，都要让没看过你聊天会话的人也能看懂。写清楚*为什么*，而不只是*做了什么*，并附上相关 issue 链接。
2. **使用中文**（D-008）。文档、Issue、PR、代码注释和评审回复一律使用简体中文。
   - 保持原样：代码标识符、命令、文件路径、配置键名，以及业内通用的英文术语（API、PR、commit、CI 等）。
   - 日语样本数据、日语回复模板和日语示例保持日语原文。
   - 提交信息和 PR 标题：Conventional Commits 的 `type` 和 `scope` 保持英文，描述和正文使用中文，例如 `feat(kb): 从历史回复构建模板知识库`。PR 标题会成为 squash 合并后的提交信息，所以两者规则一致。
3. **样本数据永远不进 git**（D-002）：原始表格、派生文件都不行，issue、PR 和日志里也不能大段引用。测试只使用小规模的合成测试数据（fixture）。`*.csv` 和 `*.xlsx` 已被全局忽略，所以这两种格式的合成测试数据需要用 `git add -f` 添加，并在 PR 中注明是合成数据。
4. **密钥只放在 `.env` 中**（D-005），代码从环境变量读取。每新增一个变量，都要在 `.env.example` 中加上该变量及注释，不填值。不要在日志、测试或 PR 文本中输出密钥。一旦密钥被提交，就视为已泄露：先轮换，再清理。
5. **不要越界。** 只做 issue 要求的事，发现的其他问题另开 issue。
6. **不该你拍板的事就去问。** 架构、范围和取舍由仓库所有者决定，请在 issue 中提出。

## 4. 工作流程

```
issue ──► 基于 origin/main 建分支 ──► 改动 → 检查 → 提交（循环）──► push ──► PR ──► 评审 ──► squash 合并
```

1. **从 issue 开始。** 用 `gh issue view <n> --comments` 完整阅读 issue，包括评论。如果还没有对应的 issue，先开一个（纯错字修正除外）。用 `gh issue edit <n> --add-assignee @me` 认领，让其他人知道已有人在做。
2. **建分支**，基于 `origin/main`：`git fetch && git switch -c <type>/<issue#>-<slug> --no-track origin/main`（例如 `feat/4-template-kb`）。`<type>` 是 Conventional Commits 的类型（第 6 节）。
3. **改动 → 检查 → 提交。** 每次只做一个聚焦的改动，运行检查（第 8 节），提交，然后再做下一个。不要先堆出一大坨 diff 再事后拆分：事后拆出的提交从未被单独测试过。
4. **每次提交和 push 前**，确认 `git branch --show-current` 和 `pwd` 符合预期，并且 diff 里只有你自己的改动。
5. **开 PR**（第 7 节）：工作可评审时即可开。想尽早获得反馈可以先开 draft PR。
6. **AI Agent：** push 和开 PR 之前要先征得你的人类协作者同意，除非对方已明确表示这项任务可以直接进行。

### 并行工作

如果你要使用的检出目录里有别人未提交的改动、有进行中的 git 操作（`.git/index.lock`、`MERGE_HEAD`、`rebase-merge/`），或者处在另一个有未推送提交的分支上，**不要碰它**，另建自己的 worktree：

```sh
git worktree list
git worktree add ../qingci-test-<slug> -b <type>/<issue#>-<slug> --no-track origin/main
```

不要复用他人（或其他 Agent）的分支或 worktree。合并后用 `git worktree remove <path>` 删除自己的 worktree。

## 5. 分支保护：`main` 的规则

`main` 是受保护分支。**无论 GitHub 当前是否强制执行，以下规则对所有贡献者（人类或 Agent）都有约束力。**（GitHub 免费版不会对私有仓库强制执行分支保护，见 D-002。）

| 规则 | 对你意味着什么 |
|---|---|
| 禁止直接 push | 所有改动都通过 PR 进入 `main`。永远不要 `git push origin main`。 |
| 禁止强制 push 和删除 | 永远不要改写 `main` 的历史。评审开始前，可以强制 push 自己的功能分支；评审开始后尽量避免。 |
| PR 需 1 个批准 | 由仓库所有者或指定的评审人批准。作者不能批准自己的 PR。 |
| 旧批准自动失效 | 批准后再 push 新提交，需要重新批准。 |
| 必需检查须通过 | CI（lint 和测试，由基础建设 issue [#2](https://github.com/NatsumeRyuhane/qingci-test/issues/2) 定义）通过后才能合并。 |
| 所有评审讨论须已解决 | 由**评审人**标记解决，而不是作者（第 7 节）。 |
| 必须签名提交 | 每个提交都要签名（SSH 或 GPG）：`git config commit.gpgsign true`。未签名的提交会被拒绝。签名失败时停下来修好，不要绕过。 |
| 线性历史，只允许 squash 合并 | PR 标题会成为 squash 提交信息，因此必须是合法的 Conventional Commit（第 6 节）。 |
| 合并前分支须为最新 | 如果分支落后，先 rebase 到 `origin/main` 或把它合并进来。 |
| 合并 | 只由仓库所有者在 GitHub 上合并。**永远不要在本地合并**到 `main`。 |

致仓库所有者：与上表对应的 GitHub 侧配置在基础建设 issue 中跟踪。两者必须保持一致，改了一边就在同一个 PR 里改另一边。

## 6. 提交

遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)：

```
<type>[(scope)][!]: <描述（中文）>

[正文（中文）：为什么做这个改动，评审人需要知道什么]

[脚注，例如 Refs: #12]
```

- 类型：`feat`、`fix`、`docs`、`refactor`、`test`、`build`、`chore`、`perf`、`ci`、`style`、`revert`。
- scope 与代码模块对应：`ingest`、`kb`、`draft`、`eval`、`ui`、`api`、`a2a`、`auth`、`docs`。
- 破坏性变更：在冒号前加 `!`，或添加 `BREAKING CHANGE:` 脚注。
- 每个提交只包含一个逻辑改动，并且每个提交都必须能单独通过检查。

## 7. Pull Request 与评审

**PR 必须包含：**
- 符合 Conventional Commits 的标题（它会成为 squash 提交信息）。
- 描述中写明 `Closes #<n>`（部分完成则用 `Refs #<n>`）。
- 描述包括：改了**什么**、**为什么**改、**如何测试**（命令和结果），以及需要评审人重点关注的地方。默认评审人没看过你的会话。
- 行为、配置或决策变化时，在同一个 PR 里更新文档（见 `project-docs` 技能）。
- 改动规模要小。大的工作拆成一系列可以单独评审的 PR。

**评审讨论：**
- 每条讨论都要回复。**不要自己标记讨论为已解决**，由评审人判断问题是否已处理。
- 采纳：修改、push，然后回复改了什么以及对应的提交 SHA。
- 不同意：回复你的理由，讨论保持打开。
- 如果仓库所有者决定某条意见不修改：回复理由，注明这是所有者的决定，然后标记为已解决。
- 反馈不明确或涉及架构取舍：先问仓库所有者再动手。
- `gh pr view --comments` 看不到行内评审讨论，也看不到是否已解决。请使用 `github-workflow` 技能中的 GraphQL 查询。

## 8. 检查与命令

_由基础建设 issue [#2](https://github.com/NatsumeRyuhane/qingci-test/issues/2)（技术栈、项目骨架、CI）定义。_ 在此之前，文档改动的检查方式是确认所有相对链接都能正确解析。

## 9. 仓库结构

```
AGENTS.md            本指南（CLAUDE.md → AGENTS.md）
README.md            项目概览与上手指南
.agents/skills/      共享的 Agent 技能（.claude/skills → .agents/skills）
.env.example         应用读取的全部环境变量，不含值
data/                仅限本地的数据目录（内容被 git 忽略），见 data/README.md
agent-conversations/ 与 AI Agent 协作的会话记录（D-009）
scripts/             辅助脚本（例如会话导出脚本）
docs/
  requirements.md    原始测试题说明
  data.md            样本数据概况与发现
  decisions.md       决策记录（D-NNN）
  proposals/         MVP 之后工作的设计提案
```
