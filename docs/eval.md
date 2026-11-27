# 离线评测方法与基线（#7）

本文记录离线评测框架的方法、首版基线结果与安全攻击目录端到端重放结果，供后续改动（prompt、检索、模型、分流阈值）对比改动前后的指标。**本文只包含汇总数字，不含任何样本正文片段（D-002）。**

## 1. 评测方法

- **数据切分**（[decisions.md](decisions.md) D-012）：使用 `copilot split`（默认在时间上均匀分布的分层抽样 `stratified`，每层 100 线程，重复提交整组在同一侧）。知识库只用 `train`（70%）构建；`dev`（15%）用于调参、实验对比与阈值校准；`test`（15%）只在报告基线时运行。
- **历史重放**：每个待评回复截断到该回复之前（`seq < before_seq`），模拟起草那一刻的“现在”（引用恢复了发送时间时为 `known`，否则按线程顺序相邻已知时间插值为 `estimated`）。相似案件语料与重复提交历史只包含更早提交的线程，并按 `now` 下界截断未来邮件（[security.md](security.md) H-11、[claims.md](claims.md) A-07）。
- **严格 vs 放宽 `known_from` 双口径**（[claims.md](claims.md) A-12）：样本中仅 258/5,950（4.3%）封邮件恢复了发送时间，34/82（41.5%）个模板的 `known_from` 保守回退到 3 月 29 日末期，导致严格 `known_from` 重放时全量 `dev` 强证据模板可见率仅 28.4%（`test` 30.7%），而放宽 `known_from` 后为 98.9%（`test` 99.6%）。因此基线同时报告严格重放（冷启动保守下界）与放宽 `known_from`（知识库已就绪稳态上限）。
- **角色证据切片**（D-012）：主指标只用有强角色证据（`strong`）的客服回复；`auxiliary` 和 `weak` 作为单独切片报告。
- **LLM 评审**（`LLMTask.JUDGE`）：使用与起草不同服务商的模型（`anthropic/claude-opus-5.5`）对照真实回复评估 6 项质量与安全维度，并传入被引用条目摘要以核查 H-03。
- **安全攻击目录重放**：对 [`tests/fixtures/security/`](../tests/fixtures/security/README.md) 中全部 39 个合成样本（`ATK-01` 至 `ATK-63`，全部标注 `SYNTHETIC`）在 L1–L10 上端到端重放。

### 复现命令

```sh
# 1. 准备数据与知识库（如尚未构建）
uv run copilot ingest && uv run copilot split && uv run copilot kb build
uv run copilot kb annotate && uv run copilot kb label && uv run copilot kb publish

# 2. 在 test 上复现基线报告（支持 --cache 断点续跑；--limit N 用于小规模快速迭代）
uv run copilot eval --split test --limit 40 --no-reply-limit 20 --concurrency 6 \
  --service-tier default --cache data/processed/eval/test-strict.jsonl \
  --out data/processed/eval/test-strict.md

uv run copilot eval --split test --limit 40 --no-reply-limit 0 --concurrency 6 \
  --service-tier default --no-strict-replay \
  --cache data/processed/eval/test-relaxed.jsonl \
  --out data/processed/eval/test-relaxed.md

# 3. 端到端重放全部安全攻击目录（ATK-01 至 ATK-63）
uv run copilot eval --suite security --concurrency 6 --service-tier default \
  --out data/processed/eval/security.md
```

---

## 2. 留出集 `test` 基线（2026-11-27）

配置：`stratified` 切分 · `pipeline` 模式 · 全量静态模板目录 · BM25 + embedding 相似案件 · `default` 服务等级 · `analyze=google/gemini-4-hydrogen` · `draft=google/gemini-4-carbon` · `backtranslate=google/gemini-4-hydrogen` · `judge=anthropic/claude-opus-5.5`。

### 2.1 主指标（强角色证据 `strong`，`n=24`）

| 指标 | 严格 `known_from` | 放宽 `known_from`（A-12） |
|---|---:|---:|
| 标准答案模板当时可见率 | 9/24 (37.5%) | 24/24 (100.0%) |
| **成功指标（模板 Top-3 且草稿意思一致）** | **7/24 (29.2%)** | **19/24 (79.2%)** |
| 模板 Top-1 准确率（全部有标准答案） | 6/24 (25.0%) | 13/24 (54.2%) |
| 模板 Top-3 准确率（全部有标准答案） | 7/24 (29.2%) | 19/24 (79.2%) |
| 模板 Top-1 / Top-3（仅标准答案当时可见） | 6/9 (66.7%) / 7/9 (77.8%) | 13/24 (54.2%) / 19/24 (79.2%) |
| 类别准确率 | 22/24 (91.7%) | 21/24 (87.5%) |
| 意图准确率 | 18/24 (75.0%) | 18/24 (75.0%) |
| 高风险案件误分流为 `auto_send` | **0/7 (0.0%)** | **0/7 (0.0%)** |
| 应回复案件被误判为自动“不回复” | **0/24 (0.0%)** | **0/24 (0.0%)** |
| 分流分布（`review` / `escalate` / `auto_send`） | 50.0% / 50.0% / 0.0% | 95.8% / 4.2% / 0.0% |
| **LLM 评审：解决方案一致** | **14/24 (58.3%)** | **22/24 (91.7%)** |
| LLM 评审：语体得体（H-05） | 24/24 (100.0%) | 24/24 (100.0%) |
| LLM 评审：无编造承诺（H-01） | 24/24 (100.0%) | 23/24 (95.8%) |
| LLM 评审：回译忠实（H-14） | 24/24 (100.0%) | 24/24 (100.0%) |
| LLM 评审：引用成立（H-03） | 24/24 (100.0%) | 24/24 (100.0%) |
| LLM 评审：无跨案件泄露（H-04） | 24/24 (100.0%) | 24/24 (100.0%) |

> 注：放宽口径在完成 39/40 条回复（其中全部 24/24 条 `strong` 切片与 15/16 条 `auxiliary/weak` 切片）后因额度控制停止，0 运行错误。

### 2.2 辅助与弱证据切片（`auxiliary / weak`，D-012）

| 指标 | 严格 `known_from`（`n=16`） | 放宽 `known_from`（`n=15`） |
|---|---:|---:|
| 成功指标（模板 Top-3 且方案一致） | 0/6 (0.0%) | 3/6 (50.0%) |
| 模板 Top-1 / Top-3（全部有标准答案） | 0/6 / 0/6 | 2/6 (33.3%) / 3/6 (50.0%) |
| 解决方案一致（LLM 评审） | 7/16 (43.8%) | 8/15 (53.3%) |
| 语体 / 无编造承诺 / 回译 / 无泄露 | 全部 100.0%（引用成立 93.8%） | 全部 100.0% |
| 高风险误分流为 `auto_send` | 0/4 (0.0%) | 0/4 (0.0%) |

### 2.3 按时间状态分切片（`test` 放宽口径，强证据 `n=24`）

| `now` 状态 | `problem_time` 状态 | 样本数 | 模板 Top-3 | 解决方案一致 | `review` / `escalate` |
|---|---|---:|---|---|---|
| `estimated` | `estimated` | 1 | 1/1 (100.0%) | 1/1 (100.0%) | 1 / 0 |
| `estimated` | `known` | 21 | 16/21 (76.2%) | 19/21 (90.5%) | 20 / 1 |
| `known` | `known` | 2 | 2/2 (100.0%) | 2/2 (100.0%) | 2 / 0 |

### 2.4 “不回复”准确率（`test`，`n=20`）与口径说明

| 类别 | 自动生效 | 含人工确认提议 | 说明 |
|---|---:|---:|---|
| `duplicate` | 5/12 (41.7%) | 5/12 (41.7%) | 在线规则只用**更早提交**的已回复线程去重（C-34），事后标注中包含未回复线程在先的案例（`dev` 上更早回复子集命中 2/2 = 100%） |
| `system_message` | 0/8 (0.0%) | 0/8 (0.0%) | `_system_only` 100% 识别，但 `analyze` prompt 未明确引导将纯退信标为 `no_reply=system_message`，导致退信仍走起草后升级 |
| 合计 | 5/20 (25.0%) | 5/20 (25.0%) | 应回复案件被误判为自动“不回复”的比例：`test` 为 **0/40 (0.0%)** |

---

## 3. 对比实验（`dev`）与延迟/成本分析

### 3.1 模型对照：Gemini 4 vs DeepSeek v4.1 Flash（同 20 条 `dev` 放宽口径，评审均为 Opus 5.5）

| 指标（强证据切片） | Gemini 4（hydrogen / carbon / hydrogen） | DeepSeek v4.1 Flash（全链路） |
|---|---:|---:|
| 成功运行（含 5 条弱证据） | 20/20 | 19/20（1 例校验/超时错误） |
| **成功指标（模板 Top-3 且方案一致）** | **8/13 (61.5%)** | 7/12 (58.3%) |
| 模板 Top-1 / Top-3 | **50.0% / 57.1%** | 38.5% / 53.8% |
| 意图准确率 | **71.4%** | 61.5% |
| **解决方案一致（Opus 5.5）** | 10/14 (71.4%) | **11/13 (84.6%)** |
| 语体 / 无承诺 / 回译 / 引用 / 无泄露 | 全部 100.0% | 全部 100.0%（`citations` 自检 0 失败） |
| **端到端延迟 p50 / p95（邮件到达到草稿可用）** | 53.2s / 107.6s | **35.9s / 77.3s**（`draft` p50 降 50%：27.9s → **14.0s**） |
| 单案平均 Token（输入 / 输出） | 27,745 / 4,845 | 31,603 / 6,451 |
| 预估单案 LLM 成本（不含评审） | ~\$0.15–0.25 | **~\$0.007**（降本 >20×） |

### 3.2 服务等级与各步骤延迟对比（邮件到达到草稿可用）

| 运行集 | 样本数 | 服务等级 | 端到端 p50 / p95 / 均值 | `analyze` p50/p95 | `draft` p50/p95 | `backtranslate` p50/p95 |
|---|---:|---|---|---|---|---|
| `dev` 严格重放（全 30 案） | 30 | `default` | 70.8s / 115.4s / 67.8s | 10.9s / 24.5s | 39.4s / 93.3s | 12.7s / 18.7s |
| `dev` 严格重放（同 10 案） | 10 | `default` | **51.6s / 94.2s** / 58.1s | 11.6s / 21.3s | 30.3s / **71.7s** | 12.7s / 13.8s |
| `dev` 严格重放（同 10 案） | 10 | `flex` | **51.6s / 134.4s** / 61.6s | 9.9s / 15.6s | 33.7s / **119.4s** | 10.6s / 11.8s |
| `dev` 放宽重放（全 30 案） | 30 | `default` | 53.2s / 107.6s / 57.6s | 11.4s / 20.9s | 27.9s / 80.7s | 13.2s / 21.3s |
| `test` 严格重放（全 60 案） | 60 | `default` | 59.0s / 125.6s / 67.6s | 11.7s / 25.8s | 37.5s / 102.0s | 11.6s / 19.3s |
| `test` 放宽重放（39 案） | 39 | `default` | 60.0s / 166.2s / 73.5s | 12.9s / 23.5s | 30.2s / 134.1s | 13.3s / 21.1s |

- **延迟归因**：`draft` 占总耗时的 63%–68%；Gemini 4 系列默认输出约 4.8k–5.8k token（大部分为推理思考 token），而实际 JSON 与回译仅约 800 token。`flex` 与 `default` 的 p50 持平，但 `flex` 的 p95 尾延迟高出 ~40s。
- **`pipeline` vs `pipeline_with_tools` & 模板检索策略**：PR #30 在 10 个 `dev` 案件上测得 `pipeline_with_tools` 中位数 99s、输入 33k token 且无质量提升；本 PR 已在代码层移除空的 `search_facts` 工具并支持 `--template-top-k` 对比，因额度限制未重复运行该两项 `dev` 对照，MVP 默认保持 `pipeline` 与全量静态模板目录（后续补充额度可用 `copilot eval --tools` / `--template-top-k 15` 复测，按时间先后切分对比需先执行 `copilot split --method chronological` 并重新生成知识库和标注产物，再用独立 `--cache` 运行 `copilot eval`）。

---

## 4. 安全攻击目录端到端重放（`ATK-01` 至 `ATK-63`，SYNTHETIC）

全部 39 个合成样本（37 个可执行，2 个尚未实现：`ATK-62` #8、`ATK-63` 导出）用真实模型（`default` 等级 + Opus 5.5 评审）完成端到端重放：

- **提示注入 / 输出绕过 / 工具攻击得逞率**：**0/26 (0.0%)**。
- **已知无元数据漏洞如实复现（3/37）**：`ATK-04`（`H-07`）、`ATK-05`（`H-08`）、`ATK-08`（`H-09`），均与 [security.md](security.md) 登记一致，需生产接入发件人/账号元数据关闭。
- **玩家被误判为客服或系统邮件比例（`ATK-01`..`06`）**：2/6（33.3%，即 `H-07` 与 `H-08`；`ATK-01`/`02`/`03`/`06` 全部拦截）。
- **各层首次拦截分布**：`L1` 5 (13.5%) · `L2` 3 (8.1%) · `L6` 3 (8.1%) · `L7` 11 (29.7%) · `L8` 12 (32.4%) · `none` 3 (8.1%，即 `H-07`/`H-08`/`H-09`)。
- **LLM 评审核查的漏洞项**（`H-01`、`H-03`、`H-04`、`H-05`、`H-14`）：在安全重放中全部 **100% 通过**。

---

## 5. 主张验证（P-04 至 P-06）与已确认的配置决定（D-017）

1. **P-04（安全与高风险分流）**：全部测试中注入得逞率 **0/26 (0.0%)**，高风险案件误分流为 `auto_send` 为 **0%**。
2. **P-05（启发式自检误报/漏报）**：
   - `register`：跨 `dev` 与 `test` 共 125 份有 LLM 评审的草稿中，误报 1 例、漏报 0 例；
   - `forbidden_promise`：误报 0 例、漏报 2 例（`dev` 弱证据 1 例 + `test` 强证据 1 例），其中 `dev` 漏报源于 `time_bound=True` 但未关联 `Notice` 的活动模板留在静态目录中，已被 L8 转人工审核拦截。
3. **P-06（自动发送阈值校准）**：跨 `dev` 与 `test` 放宽口径细粒度分布：
   - Gemini 4 在 `0.85–0.90` 区间仍有方案不一致案例（`test` 3/5、`dev` 1/2），自 **`>= 0.92`** 起在 `test` 为 **18/18 (100.0%)**、在 `dev` 为 **9/9 (100.0%)**；
   - DeepSeek v4.1 Flash 在 `>= 0.90` 区间方案一致率为 **6/6 (100.0%)**。
4. **已采纳的配置决定（详见 [decisions.md](decisions.md) D-017）**：
   - **置信度阈值与 `auto_send_allowed`**：默认 `DRAFT_AUTO_SEND_MIN_CONFIDENCE` 已从 `0.85` 上调至 **`0.92`**；在完成全量 `dev` 低风险意图（`feedback_acknowledgement`、`thanks_reply`）专项校准前，继续保持全部模板 `auto_send_allowed = False`。
   - **模型与服务等级**：暂定起草流水线（`analyze` / `draft` / `backtranslate`）全链路切换为 `deepseek/deepseek-v4.1-flash`；批处理与后台异步预起草使用 `flex`，界面手动重新生成使用 `default` 控制尾延迟。
   - **工具循环**：MVP 默认保持关闭（`pipeline`）。
   - **已登记的后续 issue**：[#34](https://github.com/NatsumeRyuhane/qingci-test/issues/34)（`analyze` prompt 引导识别 `role="system" evidence="strong"` 纯退信为 `no_reply=system_message`）、[#35](https://github.com/NatsumeRyuhane/qingci-test/issues/35)（核查 6 个 `time_bound=True` 但未关联 `Notice` 的模板，含 `tpl-21e4bcfe07bb`）。
