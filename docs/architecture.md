# 架构：数据契约

各工作线（#4 导入、#5 知识库、#6 起草、#7 评测、#8 界面）通过一组共享的数据模型协作，代码在 [`backend/copilot/contracts/`](../backend/copilot/contracts/)（[#3](https://github.com/NatsumeRyuhane/qingci-test/issues/3)）。各条线只依赖这些模型，不依赖彼此的内部实现，所以可以基于合成数据（[`tests/fixtures/`](../tests/fixtures/README.md)）独立开发和测试。

字段的含义和来源以代码中的 docstring 为准，它们也会写进导出的 JSON Schema。本文只说明各实体的作用、相互关系和约定。

## 实体

```
               #4 导入              #6 分析                #6 起草              #8 界面
样本表格 ──► Case ─────────────► CaseAnalysis ─────────► Draft ─────────────► Feedback
             ├─ messages[]: Message    （针对一封玩家邮件）   ├─ template_refs ──► ReplyTemplate（#5）
             └─ form?: InquiryForm                        └─ similar_case_refs ─► Case
```

| 模型 | 作用 | 产生者 |
|---|---|---|
| `Message` | 线程中的一封邮件：原始正文、去除引用后的正文和被去除的引用 | #4 |
| `InquiryForm` | 首封邮件中游戏内表单的字段，全部可选 | #4 |
| `Case` | 一个案件，对应一个邮件线程（`メールID`），包含邮件、表单、类别和状态 | #4 创建，#6、#8 更新状态 |
| `ReplyTemplate` | 知识库中的回复模板：适用和不适用的情况、日语正文与占位符、有效期、风险等级 | #5 |
| `CaseAnalysis` | 对一封玩家邮件的分析：中文翻译与摘要、类别、意图、情绪、紧急程度、提取的事实、缺失的信息、敏感标签 | #6 |
| `Draft` | 回复草稿：引用的模板和相似案例、日语正文、中文回译、自动检查结果、信心值和分流结果 | #6 |
| `Feedback` | 客服对草稿的处理（批准、修改、升级、拒绝），是迭代优化的信号（D-001） | #8 |

## 约定

- **版本号。** 会被持久化或对外导出的顶层对象（`Case`、`ReplyTemplate`、`CaseAnalysis`、`Draft`、`Feedback`）继承 `Document`，带 `schema_version`。改动不向后兼容时（删除或重命名字段、把可选字段改为必填、收紧取值范围、删除枚举值、改变字段或枚举值的含义），把版本号加 1，并为旧数据写迁移。读到其他版本的数据会直接报错，而不是被错误地解析。
- **同一版本内允许的演进：** 新增可选字段（带默认值）、给枚举新增取值。后续工作（例如访问控制 #13、对外 API #10 和 A2A #11、Agent 循环 #12）需要的新字段都应以这种方式加入，不升级版本。
- **对内严格，对外宽容。** 所有模型都设置了 `extra="forbid"`，我们自己的代码和测试数据中拼错的字段会直接报错，导出的 JSON Schema 也因此是 `additionalProperties: false`，描述的是系统**接受**什么样的输入。读取我们输出的一方（界面、#10 的外部系统、#11 的 A2A 客户端）则必须忽略不认识的字段、容忍不认识的枚举值，否则同一版本内的正常演进也会让它们出错。#10、#11 发布 schema 时要写明这一点。
- **时间必须带时区。** 所有时间字段都是带时区的 ISO 8601（例如 `2024-03-10T21:30:00+09:00`）。样本中的时间是日本时间，导入时要加上 `+09:00`。
- **邮件标识。** 一封邮件用 `<thread_id>:<seq>` 标识（`message_id()`、`Message.id`），例如模板的 `source_message_ids`。
- **模板占位符。** `ReplyTemplate.body_ja` 中的占位符写作 `{{slot_name}}`，必须与 `slots` 一一对应。模板修改时新建版本，草稿通过（`id`, `version`）引用模板，因此旧草稿引用的内容不会变。
- **安全不变量。** 只有 `risk` 为 `low` 的模板才能 `auto_send_allowed`；有未通过检查的草稿不能分流为 `auto_send`（D-001）。这两条在模型校验中强制执行。
- **类别。** `InquiryCategory` 对应表单的四个咨询类别，加上 `other`。更细的意图用 `CaseAnalysis.intent` 表示，取值由知识库（#5）定义。

## 为访问控制预留的字段

基于身份的访问控制（[#13](https://github.com/NatsumeRyuhane/qingci-test/issues/13)）是 MVP 之后的工作。为了以后加入时不产生破坏性变更，现在先预留以下字段。MVP 中它们只被记录，**不影响谁能看到什么**：

| 字段 | 含义 |
|---|---|
| `Case.queue` | 案件所属的队列，例如按游戏、地区或外包商划分 |
| `Case.sensitivity_tags` | 已确认的敏感标签（支付、账号安全、未成年人、法律、个人隐私） |
| `Case.assigned_to` | 负责的客服身份 |
| `CaseAnalysis.sensitivity_tags` | 分析步骤提议的敏感标签，经人工确认后写入 `Case` |
| `Feedback.actor` | 处理草稿的客服。MVP 中是界面上选择的名字，身份体系随 #13 引入 |

## JSON Schema

```sh
uv run copilot contracts export                     # 全部 schema 输出到标准输出
uv run copilot contracts export --out-dir schemas/  # 每个模型一个 <模型名>.schema.json
```

导出的是校验模式的 schema，即系统接受什么样的输入。REST API（#10）和 A2A（#11）据此对外发布。
