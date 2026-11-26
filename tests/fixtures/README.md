# tests/fixtures/

这里的数据**全部是合成的**（SYNTHETIC），不来自客户的样本表格（D-002）。日语正文中带有 `【合成データ】` 标记，便于识别。

## contracts/

每个契约模型（[docs/architecture.md](../../docs/architecture.md)）一个文件，供 #4–#8 的测试复用：

| 文件 | 模型 | 覆盖的情况 |
|---|---|---|
| `message.json` | `Message` | 带表单的首次咨询、客服回复、带引用的玩家追问 |
| `inquiry_form.json` | `InquiryForm` | 字段齐全的表单、空表单 |
| `case.json` | `Case` | 完整线程（含预留字段）、只有一封空邮件的最小案件 |
| `reply_template.json` | `ReplyTemplate` | 带占位符的模板、限定事故期间的模板、允许自动发送的低风险模板 |
| `case_analysis.json` | `CaseAnalysis` | 对玩家追问的分析 |
| `draft.json` | `Draft` | 需要审核的草稿、自动发送的草稿 |
| `feedback.json` | `Feedback` | 批准、修改、升级 |

文件格式：

```json
{
  "_synthetic": "SYNTHETIC：……",
  "items": [ <模型的 JSON>, ... ]
}
```

在测试中读取：

```python
from copilot.contracts import Case
from tests.fixtures import load_items, load_models

cases = load_models(Case, "case")  # 已校验的模型
raw = load_items("case")  # 原始 JSON 对象，便于改字段后测试校验失败的情况
```

文件之间的引用是一致的：`feedback.json` 的 `draft_id` 都在 `draft.json` 中，`draft.json` 引用的模板（ID 和版本）都在 `reply_template.json` 中，`case_analysis.json` 分析的邮件都在 `case.json` 中。`tests/contracts/` 会检查这些引用，修改时请保持一致。相似案例（`similar_case_refs`）可以指向不在 `case.json` 中的案件。
