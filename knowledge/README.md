# knowledge/

人工编写、**不含客户原文**的知识，提交到 git（提案 0003 §12）。由客户数据派生的内容（模板候选、标注、标准答案、审核文件）不在这里，而在 `data/processed/kb/`，永不提交（D-002）。

| 文件 | 内容 | 契约 |
|---|---|---|
| `intents.yaml` | 意图目录：封闭列表，`CaseAnalysis.intent` 只能取这里的值 | `Intent` |
| `policies.yaml` | 不允许的承诺、升级触发条件，由代码检查 | `Policy` |
| `notices.yaml` | 公告与事件（事故等），带事件时间 | `Notice` |
| `glossary.yaml` | 游戏内公开术语的日中对照 | `GlossaryEntry` |
| `style_guide.yaml` | 回复的固定结构与措辞，不做版本和有效期 | `StyleGuide`（`copilot/kb/knowledge.py`） |

## 规则

- **不含客户原文。** 只写运营方的固定措辞、游戏的公开名称和中文说明，不写玩家写的内容。提交前运行 `uv run copilot kb knowledge --check-leaks`：它报告与玩家邮件或表单内容有连续 20 个字符相同的条目（只报告条目，不输出内容）。
- **只看 train 的数据**（[D-012](../docs/decisions.md)）。编写和修改这里的内容时只参考 train 部分（例如 `copilot kb build` 的候选），不看 dev 和 test，否则评测集的信息会通过人进入知识库。
- **评审即审核。** 这里的条目以 `review_status: approved` 提交，PR 的评审就是所有者的审核（提案 0003 §12：运营内容要像代码一样评审）。未经评审的改动不能合并。
- **修改要新建版本。** 已合并的条目不改写：改措辞、修正内容时新增一个 `version` 加 1 的条目（同一个 `id`）；现实中的变化（例如政策变化）用新条目表示并写 `supersedes`（见 [docs/architecture.md](../docs/architecture.md) 的“版本选择”）。
- **`known_from` 必须是上界。** 历史重放（#7）只看 `known_from` 不晚于模拟的“现在”的版本。从数据中观察到的事件取能证明运营方已经知道的最早时间，不确定时宁晚勿早。

## 格式

```yaml
defaults:          # 可选：合并到每个条目中，条目中的字段优先
  version: 1
  origin: manual
entries:
  - id: ...
```

校验：`uv run copilot kb knowledge`（CI 中由 `tests/kb/test_knowledge.py` 校验）。
