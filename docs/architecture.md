# 架构：数据契约

各工作线（#4 导入、#5 知识库、#6 起草、#7 评测、#8 界面）通过一组共享的数据模型协作，代码在 [`backend/copilot/contracts/`](../backend/copilot/contracts/)（[#3](https://github.com/NatsumeRyuhane/qingci-test/issues/3)）。各条线只依赖这些模型，不依赖彼此的内部实现，所以可以基于合成数据（[`tests/fixtures/`](../tests/fixtures/README.md)）独立开发和测试。

字段的含义和来源以代码中的 docstring 为准，它们也会写进导出的 JSON Schema。本文只说明各实体的作用、相互关系和约定。

## 实体

```
               #4 导入              #6 分析                #6 起草              #8 界面
样本表格 ──► Case ─────────────► CaseAnalysis ─────────► Draft ─────────────► Feedback
             ├─ messages[]: Message    （针对一封玩家邮件）   ├─ template_refs ──► ReplyTemplate（#5）
             │   └─ role_evidence[]   └─ no_reply?        ├─ citations ─────► 知识库条目（#5）
             └─ form?: InquiryForm                        ├─ similar_case_refs ─► Case
                                                          ├─ trace[]（工具调用）
                                                          └─ time_context?: TimeContext

知识库（#5，KnowledgeEntry）：ReplyTemplate · Intent · Fact · Policy · GlossaryEntry · Notice
```

| 模型 | 作用 | 产生者 |
|---|---|---|
| `Message` | 线程中的一封邮件：原始正文、去除引用后的正文和被去除的引用；客服回复另外标出固定的问候语和结尾（`core` 是去掉两者后的部分）；客服回复和系统邮件记录判断角色的依据（`role_evidence`） | #4 |
| `InquiryForm` | 首封邮件中游戏内表单的字段，全部可选 | #4 |
| `Case` | 一个案件，对应一个邮件线程（`メールID`），包含邮件、表单、类别和状态 | #4 创建，#6、#8 更新状态 |
| `ReplyTemplate` | 知识库中的回复模板：一段完整的答复（`Message.core`），所属意图、适用和不适用的情况、日语正文与占位符、风险等级 | #5 |
| `Intent` | 意图目录（封闭列表）：类别、中文说明、需要向玩家索要的信息、默认风险、是否在自动发送允许清单中 | 人工编写（#5） |
| `Fact` | 游戏事实，起草时可以引用 | #5 或人工编写 |
| `Policy` | 不允许的承诺、升级触发条件，由代码检查（固定的日语短语、敏感标签） | 人工编写 |
| `GlossaryEntry` | 日中术语对照 | 人工编写 |
| `Notice` | 公告与事件（事故、维护、修复、政策变更）：事件时间、影响范围、链接的模板 | 人工编写 |
| `CaseAnalysis` | 对一封玩家邮件的分析：中文翻译与摘要、类别、意图、情绪、紧急程度、提取的事实、缺失的信息、敏感标签、“不回复”的提议 | #6 |
| `Draft` | 回复草稿：引用的模板、知识库条目和相似案例、日语正文、中文回译、自动检查结果、信心值和分流结果、生成方式与工具调用轨迹、时间上下文 | #6 |
| `Feedback` | 客服对草稿的处理（批准、修改、升级、拒绝），是迭代优化的信号（D-001） | #8 |

## 约定

- **版本号。** 会被持久化或对外导出的顶层对象（`Case`、各知识库条目、`CaseAnalysis`、`Draft`、`Feedback`）继承 `Document`，带 `schema_version`。当前 `Case` 和 `ReplyTemplate` 为 2（#19：收紧 `Case.id`，模板加入知识库的公共字段），其余为 1。改动不向后兼容时（删除或重命名字段、把可选字段改为必填、收紧取值范围、删除枚举值、改变字段或枚举值的含义），把版本号加 1，并为旧数据写迁移。读到其他版本的数据会直接报错，而不是被错误地解析。
- **同一版本内允许的演进：** 新增可选字段（带默认值）、给枚举新增取值。后续工作（例如访问控制 #13、对外 API #10 和 A2A #11、Agent 循环 #12）需要的新字段都应以这种方式加入，不升级版本。
- **对内严格，对外宽容。** 所有模型都设置了 `extra="forbid"`，我们自己的代码和测试数据中拼错的字段会直接报错，导出的 JSON Schema 也因此是 `additionalProperties: false`，描述的是系统**接受**什么样的输入。读取我们输出的一方（界面、#10 的外部系统、#11 的 A2A 客户端）则必须忽略不认识的字段、容忍不认识的枚举值，否则同一版本内的正常演进也会让它们出错。#10、#11 发布 schema 时要写明这一点。
- **时间必须带时区。** 所有时间字段都是带时区的 ISO 8601（例如 `2024-03-10T21:30:00+09:00`）。样本中的时间是日本时间，导入时要加上 `+09:00`。
- **带状态的时间。** 起草用到的“现在”和问题发生时间是 `TimePoint`：`known`（确切值）、`estimated`（必须有区间，只知道时刻时用 `time_of_day`）、`unavailable`（没有值，不能猜测填补）。两者合在一起是 `TimeContext`，记录在草稿上。见 [提案 0003](proposals/0003-knowledge-base.md) §9。
- **案件 ID。** `Case.id`（即线程 ID）只允许 ASCII 字母、数字、`_` 和 `-`，1 到 64 个（`CASE_ID_CHARS`），因为它会出现在 URL、文件路径和日志中（#18）。导入时不合规的 ID 报告为表格格式错误。
- **邮件标识。** 一封邮件用 `<thread_id>:<seq>` 标识（`message_id()`、`Message.id`），例如知识库条目的 `source_message_ids`。
- **角色证据。** 样本没有发件人元数据，角色只能从文本判断。`Message.role_evidence` 记录依据，强度为强（元数据、引用佐证、与有佐证的回复正文相同）、辅助（重复出现）或弱（问候语、结语）。以没有强证据的客服回复结束的线程为 `undetermined`；需要信任角色的下游按 `Message.role_strength` 判断。见提案 0003 §11。
- **知识库条目。** 模板、意图、事实、政策、术语表条目、公告与事件都继承 `KnowledgeEntry`，共用：有效期 `[valid_from, valid_to)`、`known_from`（运营方何时知道）、`recorded_at`（审计）、来源方式、审核状态、`source_message_ids`、`supersedes`。
- **版本选择。** （`id`, `version`）是编辑历史，每次修改都新建版本，行不可改写；草稿通过（`id`, `version`）引用，旧草稿引用的内容不变。每个条目以**最新版本**为准（`current_version`），最新版本为 `retired` 时整个条目下线，旧版本不会重新被选中；再由有效期决定在时间 T 是否适用（`select_valid`）。现实中的变化用一个新条目表示，记录 `supersedes`。历史重放（#7）只看 `known_from` 不晚于模拟的“现在”的版本。
- **只有已批准的条目提供给模型**（`KnowledgeEntry.model_visible`）。未审核的条目只作为客服的参考。
- **人工编写的条目**（意图、政策、公告与事件、术语表）以 YAML 放在 [`knowledge/`](../knowledge/README.md)，由 `copilot.kb.knowledge.load_knowledge` 读取并校验；固定的风格指南（`StyleGuide`）也在那里，它不是知识库条目，没有版本和有效期。
- **模板占位符。** `ReplyTemplate.body_ja` 中的占位符写作 `{{slot_name}}`，必须与 `slots` 一一对应。
- **安全不变量。** 只有已批准、`risk` 为 `low` 的模板才能 `auto_send_allowed`；只有 `default_risk` 为 `low` 的意图才能列入自动发送允许清单；有未通过检查的草稿不能分流为 `auto_send`（D-001）。这些在模型校验中强制执行。
- **政策由代码检查。** `Policy` 用 NFKC 规范化后的固定日语短语做子串匹配（`matching_phrases`），不用正则，避免构造的输入引起回溯（#18）。
- **公告的两个时间范围。** `Notice` 的有效期按“现在”判断；事件时间（`event_start`/`event_end`）与案件的问题发生时间匹配。
- **类别与意图。** `InquiryCategory` 对应表单的四个咨询类别，加上 `other`。更细的意图用 `CaseAnalysis.intent` 表示，取值是意图目录（`Intent.id`）中的值，由使用方按目录校验。
- **草稿的引用与轨迹。** 草稿中的说法通过 `citations` 引用知识库条目（类型、ID、版本）。`mode` 为 `pipeline_with_tools` 时，`trace` 记录每一步工具调用，工具结果只记录摘要。

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
