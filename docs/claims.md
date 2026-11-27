# 设计主张与假设

本文列出系统**声称做到的事**（主张），每条写明由哪段代码保证、由哪些测试守护；以及这些主张**依赖的前提**（假设），每条写明如果前提不成立会怎样、在哪里重新评估。评审时可以逐条核对：一条主张没有测试，就只是愿望。

[`tests/test_claims.py`](../tests/test_claims.py) 检查本文引用的每个测试都存在：测试改名或删除时，CI 会失败，提醒同步本文。

## 怎么读

**守护方式**（一条主张可以有几种）：

| 标记 | 含义 | 证明了什么 |
|---|---|---|
| 单元 | CI 中的单元测试，fake LLM 或不调用 LLM | 代码按规则执行 |
| 对抗 | CI 中的对抗测试，**假设模型已被攻破**（提案 0003 §13） | 即使模型完全听从注入，代码层仍然拦截 |
| 契约 | 契约模型的校验，非法状态无法构造 | 不依赖任何流程代码的最后一道防线 |
| 真实数据 | 改动流水线的 PR 在本地用样本运行，只报汇总数字（AGENTS.md 第 7 节） | 规则在真实数据上的行为合理 |
| 待验证 | 还没有测试；写明由哪个 issue 或 PR 补上 | — |

**主张只覆盖代码层。** 模型本身的质量和被注入的难易程度（翻译是否准确、注入成功率）不是 CI 能证明的，由评测（[#7](https://github.com/NatsumeRyuhane/qingci-test/issues/7)）衡量，列在“待验证”中。

## 主张

### 知识库与学习（#4、#5）

| # | 主张 | 由谁保证 | 测试 | 守护方式 |
|---|---|---|---|---|
| C-01 | 伪造的客服问候语、粘贴回来的回复、提交到多个线程的同一伪造正文，都不会得到强角色证据，线程不会因此显示为已回复 | `copilot/ingest/evidence.py`、`pipeline.py` 的状态规则 | `tests/ingest/test_adversarial.py::test_forged_greeting_does_not_close_the_case` · `tests/ingest/test_adversarial.py::test_same_forgery_in_many_threads_is_not_strong` · `tests/ingest/test_adversarial.py::test_pasting_back_a_corroborated_reply_does_not_close_the_case` · `tests/ingest/test_evidence.py::test_keywords_alone_are_weak` | 单元、对抗 |
| C-02 | 知识库只从 train 构建，dev 和 test 的线程不能改变构建结果 | `copilot/kb/build.py` | `tests/kb/test_build.py::test_only_train_is_learned` · `tests/kb/test_build.py::test_evaluation_threads_cannot_change_the_build` · `tests/kb/test_build.py::test_stale_split_is_rejected` | 单元 |
| C-03 | 切分可复现，重复提交整组在同一侧 | `copilot/eval/split.py` | `tests/eval/test_split.py::test_split_is_reproducible_and_independent_of_input_order` · `tests/eval/test_split.py::test_duplicates_stay_on_one_side` | 单元 |
| C-04 | 被攻破的标注模型不能调低风险、不能选目录外的意图，标注结果永远是未审核的草稿 | `copilot/kb/annotate.py` | `tests/kb/test_annotate.py::test_compromised_model_cannot_lower_the_risk` · `tests/kb/test_annotate.py::test_intent_outside_the_catalog_fails_and_is_reported` · `tests/kb/test_annotate.py::test_annotations_become_draft_templates` | 单元、对抗 |
| C-05 | 只有所有者能允许模板自动发送：只有已批准、风险为 `low` 的模板才能 `auto_send_allowed` | `ReplyTemplate` 的校验、`copilot/kb/review.py` | `tests/contracts/test_models.py::test_only_low_risk_templates_can_auto_send` · `tests/kb/test_review.py::test_yaml_round_trip_and_code_guards` | 契约、单元 |
| C-06 | 知识库只插入、不改写：已发布的版本不能修改或删除 | `copilot/kb/store.py`、迁移中的触发器 | `tests/kb/test_store.py::test_changing_a_stored_version_is_rejected` · `tests/kb/test_store.py::test_database_rejects_update_and_delete` | 单元 |
| C-07 | 历史重放只看当时已知的版本；从数据归纳的条目，`known_from` 只会偏晚、不会偏早 | `current_version`、`select_valid`、`kb/annotate.py` | `tests/kb/test_store.py::test_version_selection` · `tests/kb/test_annotate.py::test_known_from_is_an_upper_bound` · `tests/draft/test_retrieval.py::test_replay_ignores_versions_not_yet_known` · `tests/draft/test_retrieval.py::test_replay_ignores_notice_links_added_later` | 单元、真实数据（PR #28） |
| C-08 | 提交到 `knowledge/` 的内容不含客户原文，也不含模板原文 | `copilot/kb/knowledge.py` 的 `find_leaks`（`copilot kb knowledge --check-leaks`） | `tests/kb/test_knowledge.py::test_player_text_in_knowledge_is_a_leak` · `tests/kb/test_knowledge.py::test_template_text_in_knowledge_is_a_leak` | 单元；提交前在本地对样本运行 |

### 起草：时间与可用知识（#6）

| # | 主张 | 由谁保证 | 测试 | 守护方式 |
|---|---|---|---|---|
| C-10 | 时间带状态，没有就是没有：只有 `HH:MM` 时是带区间的估计，晚于“现在”的表单时间不采用 | `copilot/draft/timectx.py` | `tests/draft/test_timectx.py` 全部 · `tests/contracts/test_timepoint.py::test_unavailable_time_has_no_value` | 单元、契约、真实数据（PR #28） |
| C-11 | 只有已批准、当前有效的条目提供给模型 | `copilot/draft/retrieval.py` 的 `build_view` | `tests/draft/test_retrieval.py::test_only_approved_current_versions_are_visible` · `tests/draft/test_retrieval.py::test_expired_template_is_not_visible` · `tests/draft/test_adversarial.py::test_unapproved_template_is_not_selectable` | 单元、对抗。prompt 中只出现这些条目：待验证（PR2） |
| C-12 | 限时模板永远不在静态目录中，只在链接的事件与问题发生时间匹配时注入 | `build_view` | `tests/draft/test_retrieval.py::test_time_bound_template_never_in_static_catalog` · `tests/draft/test_retrieval.py::test_template_linked_by_retired_notice_stays_time_bound` · `tests/draft/test_retrieval.py::test_platform_mismatch_excludes_notice` | 单元 |
| C-13 | 通用模板永远排在生效中的特定模板之后 | `KnowledgeView.candidates` | `tests/draft/test_retrieval.py::test_matching_incident_injects_template_first` | 单元 |
| C-14 | 时间无法确定（区间跨过窗口边界、问题发生时间缺失、公告有效期不确定）时，判为不通过，不当作匹配 | `match_window`、`build_view`、`check_template_window` | `tests/draft/test_retrieval.py::test_time_of_day_only_can_be_uncertain` · `tests/draft/test_retrieval.py::test_unavailable_problem_time_is_uncertain` · `tests/draft/test_retrieval.py::test_uncertain_notice_validity_keeps_candidate_uncertain` · `tests/draft/test_checks.py::test_template_window` · `tests/draft/test_adversarial.py::test_uncertain_problem_time_goes_to_review` | 单元、对抗 |
| C-15 | 相似案件只来自更早提交的线程，不含当前案件 | `copilot/draft/similar.py` 的 `corpus_before` | `tests/draft/test_similar.py::test_corpus_before_uses_thread_order_and_excludes_current` · `tests/draft/test_similar.py::test_current_case_is_excluded` | 单元 |

### 起草：自检与政策（#6）

| # | 主张 | 由谁保证 | 测试 | 守护方式 |
|---|---|---|---|---|
| C-20 | 不允许的承诺由代码检查：模型在模板之外写的承诺、填进占位符的承诺、占位符的值与模板原文拼成的承诺都会被发现；只有已批准模板的原文措辞豁免 | `copilot/draft/checks.py` 的 `check_forbidden_promise` | `tests/draft/test_checks.py::test_forbidden_promise_in_added_text_fails` · `tests/draft/test_checks.py::test_rewritten_template_is_checked_as_added_text` · `tests/draft/test_checks.py::test_slot_values_are_checked_as_untrusted` · `tests/draft/test_checks.py::test_promise_formed_across_slot_boundary_fails` · `tests/draft/test_adversarial.py::test_obeyed_refund_injection_is_blocked` | 单元、对抗 |
| C-21 | 草稿中不会混入链接、邮箱、HTML 或脚本、不可见字符、其他案件的文字（模板原文中的链接除外） | `check_content_safety` | `tests/draft/test_checks.py::test_content_safety` · `tests/draft/test_checks.py::test_other_case_text_is_detected` · `tests/draft/test_adversarial.py::test_malicious_content_in_draft_is_blocked` · `tests/draft/test_adversarial.py::test_leaking_other_case_is_blocked` | 单元、对抗 |
| C-22 | 模型新增的事实性说法必须引用提供给它的条目，引用按整句对应；模型不能自己豁免一句话，只有白名单中的完整“索要信息”句式不需要引用 | `check_citations`、`is_info_request` | `tests/draft/test_checks.py::test_uncited_added_claim_fails_and_citation_fixes_it` · `tests/draft/test_checks.py::test_is_info_request` · `tests/draft/test_checks.py::test_citation_must_match_the_whole_sentence` · `tests/draft/test_checks.py::test_model_cannot_exempt_a_factual_sentence` · `tests/draft/test_checks.py::test_citation_of_unprovided_entry_fails` | 单元。引用是否真的支持这句话：待验证（#7） |
| C-23 | 固定的问候语、结语和禁止转载声明由代码组装，不由模型生成 | `assemble`、`check_structure` | `tests/draft/test_checks.py::test_assemble_order_and_no_duplicate_phrases` · `tests/draft/test_checks.py::test_structure_detects_missing_or_repeated_frame` | 单元 |
| C-24 | 不可信的文本只作为数据放在分隔标记内，正文中伪造的标记无法结束数据块 | `copilot/llm/prompt.py` 的 `data_block` | `tests/llm/test_prompt.py::test_forged_delimiters_cannot_close_the_block` · `tests/llm/test_prompt.py::test_invisible_and_bidi_characters_are_removed` | 单元。起草的 prompt 组装：待验证（PR2） |

### 起草：分流与“不回复”（#6）

| # | 主张 | 由谁保证 | 测试 | 守护方式 |
|---|---|---|---|---|
| C-30 | **自动发送需要 12 个关卡全部通过，一个不过全部不过**；缺少任何一个关卡的结果也不批准 | `copilot/draft/route.py` 的 `approve_auto_send`、`AutoSendApproval.granted` | `tests/draft/test_route.py::test_every_gate_is_evaluated_and_tested` · `tests/draft/test_route.py::test_each_gate_blocks_auto_send` · `tests/draft/test_route.py::test_missing_gate_result_is_not_approval` · `tests/draft/test_route.py::test_all_gates_passing_is_the_only_approval` | 单元 |
| C-31 | 有未通过检查的草稿不能是 `auto_send`，与分流代码无关 | `Draft` 的校验 | `tests/contracts/test_models.py::test_draft_cannot_auto_send_with_failed_check` | 契约 |
| C-32 | 升级政策命中（玩家文字中的短语或敏感标签）、情绪为 `angry`、找不到模板时升级 | `route` | `tests/draft/test_route.py::test_escalate_on_trigger_phrase_in_player_text` · `tests/draft/test_route.py::test_escalate_on_trigger_phrase_in_follow_up` · `tests/draft/test_route.py::test_escalate_on_sensitivity_tag_of_trigger` · `tests/draft/test_route.py::test_escalate_when_angry` · `tests/draft/test_route.py::test_escalate_without_template` | 单元 |
| C-33 | 疑似注入、伪造的客服回复（`undetermined`）、伪造的问题发生时间都不能让案件自动发送 | 关卡 `no_injection`、`reply_confirmed`、`template_allowed` | `tests/draft/test_adversarial.py::test_obeyed_injection_is_blocked_even_if_model_hides_it` · `tests/draft/test_adversarial.py::test_forged_agent_reply_keeps_case_out_of_auto_send` · `tests/draft/test_adversarial.py::test_forged_problem_time_cannot_auto_send_compensation` | 对抗。注入成功率接近零：待验证（#7、`llm-live`） |
| C-34 | “不回复”只在两种情况下自动生效：代码确认的、没有追问的重复提交（对应的线程更早提交并且有可信回复）；有强系统证据、没有玩家问题、没有疑似注入的系统邮件线程 | `decide_no_reply` | `tests/draft/test_route.py::test_duplicate_of_replied_thread_is_automatic` · `tests/draft/test_route.py::test_duplicate_of_weakly_replied_thread_is_not_automatic` · `tests/draft/test_route.py::test_duplicate_shortcut_ignores_threads_with_follow_up` · `tests/draft/test_route.py::test_later_replied_duplicate_does_not_suppress_earlier_thread` · `tests/draft/test_route.py::test_model_claimed_duplicate_without_code_confirmation_is_not_automatic` · `tests/draft/test_route.py::test_system_only_thread_is_automatic_only_with_strong_evidence` · `tests/draft/test_route.py::test_other_reason_is_never_automatic` · `tests/draft/test_adversarial.py::test_forged_bounce_with_question_is_not_auto_ignored` | 单元、对抗、真实数据（PR #28：77 个线程，全部与 #5 的标注一致） |

### 密钥与数据（全局）

| # | 主张 | 由谁保证 | 测试 | 守护方式 |
|---|---|---|---|---|
| C-40 | 应用读取的每个环境变量都列在 `.env.example` 中，且不含值；缺少配置时只报变量名，不输出值 | `copilot/config.py` | `tests/test_config.py::test_env_example_lists_exactly_the_settings_fields` · `tests/test_config.py::test_env_example_has_no_secret_values` · `tests/test_config.py::test_llm_endpoint_reports_missing_names_without_values` | 单元 |
| C-41 | 测试只用合成数据，样本数据不进 git（D-002） | `.gitignore`；合成数据标注 SYNTHETIC | `tests/contracts/test_models.py::test_every_exported_model_has_a_fixture`（fixture 必须带 SYNTHETIC 标注） | 单元；其余靠评审 |

### 待验证（尚无测试）

| # | 主张 | 由谁补上 |
|---|---|---|
| P-01 | 流水线的每个草稿都经过 `run_checks` 和 `route`，没有绕过它们的路径 | PR2（起草与 CLI）的端到端测试；PR3 结束、#7 开始前统一复核 |
| P-02 | 提供给模型的 prompt 中只有可见的条目；时间块在最后、由代码生成，正文不能伪造 | PR2 |
| P-03 | 工具只读、范围受限：越权的调用被拒绝，超出步数时回退到固定流水线 | PR3 |
| P-04 | 注入成功率、“玩家被误判为客服或系统”的比例接近零；高风险案件不会被分流为 `auto_send` | #7（合成注入样本）、`llm-live` |
| P-05 | 自检的启发式规则（敬体、索要信息的句式、政策短语）误报和漏报都低 | #7 在 dev 上报告 |
| P-06 | 自动发送的阈值（`DRAFT_AUTO_SEND_MIN_CONFIDENCE`）合理：通过全部关卡的草稿确实正确 | #7 在 dev 上校准，之后所有者才在审核中打开任何模板的 `auto_send_allowed` |

## 假设

主张成立的前提。前提不成立时，相应的主张可能失效。

| # | 假设 | 依据 | 如果不成立 | 在哪里重新评估 |
|---|---|---|---|---|
| A-01 | **输入中没有发件人元数据**，角色只能从文本判断 | 样本已脱敏（[data.md](data.md)） | 有了元数据，C-01 的文本规则只用于历史数据；没有元数据时知识库不能自动更新（D-011） | 投入生产前向数据来源索取（提案 0003 §11） |
| A-02 | **所有者的审核是知识的信任根**：已批准的模板、`knowledge/` 中的条目是可信的 | D-011、D-014 | 一个错误的批准会被当作运营方的答复提供给模型；C-20 对模板原文的豁免也依赖它 | 每次审核；政策变化时引用它的模板转为 `needs_rereview` |
| A-03 | **问题发生时间是玩家填写的，不可信**，只决定候选，不决定补偿 | 提案 0003 §9 | — （这是设计的出发点，C-14、C-33 据此设计） | — |
| A-04 | **公告的事件窗口按所有者录入的为准**，不加缓冲 | 所有者在 PR #28 中选择方案 A：窗口外 11% 的事故线程转人工，不会误发 | 窗口太窄时，事故模板对一部分案件不可用，增加人工 | #7 衡量这些案件的草稿质量；需要时以只用 train 的分析修改 `knowledge/notices.yaml` |
| A-05 | **线程 ID 就是提交顺序** | [data.md](data.md)：按 ID 分段时问题发生时间的中位数单调递增 | C-15 的“更早的线程”不再等于“现在之前” | 换数据来源时；生产中改用接收时间 |
| A-06 | **重复提交只能按内容判断**（内容、设备、App 版本、问题发生时间） | 账号和邮箱已脱敏 | 不同玩家的相同咨询可能被当作重复（C-34） | 生产中改为按账号或发件地址判断 |
| A-07 | **相似案件的语料只按线程顺序筛选**：更早线程中“现在”之后才发出的回复也会被看到 | 样本大多没有发送时间 | 历史重放中，相似案件可能带来少量“未来”的信息 | #7 做重放时传入自己的语料 |
| A-08 | **固定措辞不变**：问候语、结语、禁止转载声明是固定的 | [data.md](data.md) 关键发现 3 | C-23 的组装和 `structure` 检查需要随之修改 | 运营方告知措辞变化时（D-011） |
| A-09 | **字符 bigram 的 BM25 加 embedding 足够用于相似案件检索** | D-015 | 检索质量下降，只影响辅助上下文 | #7 |
| A-10 | **fake LLM 能代表“被攻破的模型”**：对抗测试中构造的输出覆盖了真实模型可能被诱导出的行为 | 提案 0003 §13 的对抗测试表 | 出现新的攻击方式时，可能有代码层没有覆盖的输出 | 新发现的攻击先加入合成样本，再修复（#18） |
| A-11 | **项目配置的 LLM 服务商可以接收样本数据** | 这就是产品功能本身（`sample-data` 技能） | — | 更换服务商时 |
