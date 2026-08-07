# Harness Session Goal 单元测试设计文档

对应提交（Refs: #1241）：

| 提交 | 说明 |
| --- | --- |
| `f1f09c346d373235f7a7d7d515e6445dcac20ffd` | feat(harness): add session goal domain model |
| `d966ec22631f096253cc3f48099997c44c2f0b62` | feat(harness): add persistent interaction supervisor |
| `613810dc5c8c8e6466522bafe21c120917d99bfd` | feat(harness): integrate goal attempts into task rail |

三个提交合入后对应 PR：`67822fa1`（Session-Scoped Supports Goal Execution）。

## 1. 背景与目标

本需求为 DeepAgent 增加 **session 级持久 Goal**：

1. 新增 Goal 领域模型、Session 持久化、完成度评估策略、Goal prompt 与 `submit_goal_report` 工具协议。
2. 新增 DeepAgent 长生命周期交互入口（`start` / `send_input` / `stop`），支持用户输入、goal round、连续 attempts、单消费者 stream 与结构化交互事件。
3. 把 Goal 生命周期并入 `TaskCompletionRail`：每轮替换 goal query、收集 report、按策略评估完成度、更新状态，并在需要时自动排下一轮。

本文档覆盖上述三个提交引入的确定性单元测试，路径在
`tests/unit_tests/harness/goal/` 以及 harness 层两处扩展文件。

目标：

- 保障 `GoalRecord` / `GoalAssessment` / `TokenUsage` 序列化与非法载荷拒绝契约；
- 保障 `SessionGoalStore` 在 session state 上的 save/load/clear 与脏数据丢弃；
- 保障 `GoalManager` 的 set/pause/resume/clear/begin_attempt/apply_assessment 状态机与事件排队；
- 保障 `GoalEvaluator` 的 AGENT_REPORT / TRANSCRIPT / HYBRID 策略与硬限制；
- 保障 Goal prompt、`GoalReportSink` / `SubmitGoalReportTool` 协议；
- 保障 `EventManager` 对 user/goal 工作项的优先级、去重与丢弃；
- 保障 `TaskCompletionRail` 在 goal round 中不提前 force-finish，并按策略调用 transcript assessor。

## 2. 测试范围

### 2.1 被测模块与对应用例文件

| 被测模块 | 提交引入 | 测试文件 |
| --- | --- | --- |
| Goal 领域模型 | `f1f09c34` | `tests/unit_tests/harness/goal/test_goal_schema.py` |
| SessionGoalStore | `f1f09c34` / `613810dc` | `tests/unit_tests/harness/goal/test_goal_store.py` |
| GoalManager | `f1f09c34` / `613810dc` | `tests/unit_tests/harness/goal/test_goal_manager.py` |
| GoalEvaluator | `f1f09c34` | `tests/unit_tests/harness/goal/test_goal_evaluation.py` |
| Goal prompt | `f1f09c34` | `tests/unit_tests/harness/goal/test_goal_prompts.py` |
| submit_goal_report / GoalReportSink | `f1f09c34` | `tests/unit_tests/harness/goal/test_goal_tool.py` |
| EventManager 工作调度 | `d966ec22` / `613810dc` | `tests/unit_tests/harness/goal/test_event_manager.py` |
| `_normalize_inputs` 保留 goal context | `613810dc` | `tests/unit_tests/harness/test_normalize_inputs.py` |
| TaskCompletionRail goal 集成 | `613810dc` | `tests/unit_tests/harness/test_task_completion_extensions.py` |

### 2.2 不在本轮「新增」统计范围

- 系统级 E2E（`tests/system_tests/`，依赖真实模型/网络）。
- DeepAgent `start` / `send_input` / `stop` 的端到端交互 supervisor 联调（三个提交未新增独立 UT，由 GoalManager + EventManager 间接覆盖调度契约）。
- 三个提交之后追加的计时/协议用例（例如 `test_goal_record_time_fields_round_trip`、`test_has_running_goal_ignores_queue_and_revision`、`test_goal_protocol_attachment.py`），不计入本次「新增」数量，执行时可作为回归补充。

## 3. 测试用例分级原则

与项目既有 CI gate 约定对齐：

- **Level 0（冒烟/基础正向路径）**：核心构造、默认配置下的成功路径、关键状态迁移、主要工具 happy path。任一条失败阻断合入。
- **Level 1（功能覆盖/分支与异常路径）**：参数组合、异常分支、非法载荷、策略交叉、生命周期边界。

三个提交引入的用例当时未打 `@pytest.mark.level0/level1`，本文档按上述原则分级（参数化用例按 pytest 收集条数计）。

## 4. 用例清单与分级统计

以下仅统计三个提交**新增**的需求用例（`613810dc` 树上的测试函数；参数化按收集条数）。

| 测试文件 | 合计 | Level 0 | Level 1 |
| --- | ---: | ---: | ---: |
| `test_goal_schema.py` | 7 | 4 | 3 |
| `test_goal_store.py` | 2 | 1 | 1 |
| `test_goal_evaluation.py` | 6 | 3 | 3 |
| `test_goal_prompts.py` | 6 | 2 | 4 |
| `test_goal_tool.py` | 5 | 3 | 2 |
| `test_goal_manager.py` | 10 | 4 | 6 |
| `test_event_manager.py` | 4 | 2 | 2 |
| `test_normalize_inputs.py`（仅新增 1 条） | 1 | 1 | 0 |
| `test_task_completion_extensions.py`（仅新增 7 条） | 7 | 2 | 5 |
| **合计** | **48** | **22** | **26** |

`test_goal_schema.py` 中 `test_goal_record_rejects_invalid_persistence_payload` 在提交时有 2 组参数，计为 2 条 Level 1。

### 4.1 Level 0 清单

| 用例 | 覆盖点 |
| --- | --- |
| `test_token_usage_accumulates_and_round_trips` | TokenUsage 累加与 round-trip |
| `test_assessment_round_trip_and_invalid_status` | GoalAssessment 序列化；非法 status 回落 continue |
| `test_goal_record_round_trip_and_response_copy` | GoalRecord 持久化与响应副本隔离 |
| `test_stop_config_defaults` | GoalStopConfig 默认 HYBRID |
| `test_session_store_save_load_and_clear` | SessionGoalStore 读写清除 |
| `test_parse_assessment_json_accepts_plain_and_fenced_json` | 评估 JSON 解析 |
| `test_agent_report_strategy_uses_report_and_falls_back_when_absent` | AGENT_REPORT 策略 |
| `test_transcript_strategy_uses_verified_response` | TRANSCRIPT 策略 |
| `test_goal_task_query_first_attempt` | 首轮 goal query 构造 |
| `test_goal_current_instruction_uses_next_instruction` | 下一轮指令注入 |
| `test_begin_and_submit` / `test_consume` / `test_submit_goal_report_tool` | GoalReportSink 与工具 happy path |
| `test_set_persists_goal_and_queues_work_only_with_an_output_consumer` | set 持久化且仅在有消费者时入队 |
| `test_pause_then_resume_updates_state_and_requeues_goal_work` | pause/resume 状态与再入队 |
| `test_clear_removes_goal_work_cancels_active_round_and_emits_snapshot` | clear 取消轮次并发快照 |
| `test_attempt_usage_and_completion_are_written_for_current_generation` | attempt 用量与完成写入 |
| `test_user_work_has_priority_over_goal_work` | 用户工作优先于 goal |
| `test_work_items_copy_host_inputs_and_derive_query_from_them` | RoundWorkItem 输入拷贝 |
| `test_goal_context_fields_are_preserved_in_extra` | normalize 保留 goal_id/revision |
| `test_submit_goal_report_does_not_force_finish` | 接受 report 不提前结束轮次 |
| `test_tool_after_goal_report_is_allowed` | report 后仍允许其它工具 |

### 4.2 Level 1 清单

| 用例 | 覆盖点 |
| --- | --- |
| `test_goal_record_rejects_invalid_persistence_payload`（2 组） | 空 objective / 非法 status |
| `test_goal_operation_error_keeps_an_isolated_goal_copy` | GoalOperationError 隔离副本 |
| `test_session_store_drops_malformed_persistence_data` | 脏数据丢弃 |
| `test_hybrid_requires_transcript_for_terminal_agent_report` | HYBRID 终态必须 transcript |
| `test_hybrid_spot_check_can_override_continue_report` | 抽检可推翻 continue |
| `test_hard_limits_block_only_continue` | 次数/token 上限仅拦截 continue |
| `test_goal_task_query_with_assessment` / `test_goal_task_query_en` / `test_goal_task_query_budget_notice` | 评估回填、英文、预算提示 |
| `test_transcript_assessor_prompt_uses_attempt_context` | transcript assessor prompt |
| `test_begin_resets` / `test_submit_goal_report_invalid_status` | sink 重置、非法 status 回落 |
| `test_set_requires_a_non_empty_objective` | 空目标拒绝 |
| `test_goal_writes_are_committed_immediately` | 写路径立即 commit |
| `test_set_requires_confirmation_before_replacing_existing_goal` | 覆盖需确认 |
| `test_pause_and_resume_are_noops_without_a_goal` | 无 goal 时 pause/resume/clear 空操作 |
| `test_pause_keeps_revision_so_in_flight_assessment_can_commit` | pause 不打断 in-flight 评估 |
| `test_pause_then_complete_assessment_overrides_paused` | 完成后覆盖 paused |
| `test_goal_work_is_deduplicated_across_queued_dequeued_and_active_states` | goal 工作去重 |
| `test_discard_goal_work_only_removes_pending_matching_goal` | 按 goal_id 丢弃待处理项 |
| `test_goal_report_outside_goal_round_does_not_force_finish` | 非 goal 轮次不 force-finish |
| `test_rejected_goal_report_does_not_force_finish` | 拒绝 report 不提前结束 |
| `test_terminal_goal_report_invokes_transcript_assessor` | 终态调用 transcript |
| `test_continue_goal_report_does_not_invoke_transcript_by_default` | continue 默认不抽检 |
| `test_attempt_context_uses_latest_model_window_without_duplication` | 评估上下文取最新窗口 |

## 5. 重点场景覆盖说明

- **领域模型**（`f1f09c34`）：`GoalRecord` / `GoalAssessment` / `TokenUsage` 的 dict 往返、非法持久化载荷拒绝、`GoalOperationError` 隔离副本。
- **评估策略**（`f1f09c34`）：默认 HYBRID 对 complete/blocked 做 transcript 校验；continue 走低成本路径，可配置 `verification_interval` 抽检；次数/token 硬限制只把 continue 抬成 blocked。
- **交互调度**（`d966ec22`）：`EventManager` 保证 user 优先于 goal，同一 goal 在 queued/dequeued/active 三态不去重入队，`discard_goal_work` 只删匹配的 pending 项。
- **Rail 集成**（`613810dc`）：`submit_goal_report` 本身不 force-finish，评估发生在 `after_task_iteration`；goal context（`goal_id` / `revision` / `session_id`）经 `_normalize_inputs` 进入 `RunContext.extra`。

## 6. 执行方式

```bash
# Windows：使用仓库虚拟环境
.\.venv\Scripts\python.exe -m pytest ^
  tests/unit_tests/harness/goal/test_goal_schema.py ^
  tests/unit_tests/harness/goal/test_goal_store.py ^
  tests/unit_tests/harness/goal/test_goal_evaluation.py ^
  tests/unit_tests/harness/goal/test_goal_prompts.py ^
  tests/unit_tests/harness/goal/test_goal_tool.py ^
  tests/unit_tests/harness/goal/test_goal_manager.py ^
  tests/unit_tests/harness/goal/test_event_manager.py ^
  tests/unit_tests/harness/test_normalize_inputs.py::TestNormalizeInputsRawQuery::test_goal_context_fields_are_preserved_in_extra ^
  tests/unit_tests/harness/test_task_completion_extensions.py::test_submit_goal_report_does_not_force_finish ^
  tests/unit_tests/harness/test_task_completion_extensions.py::test_tool_after_goal_report_is_allowed ^
  tests/unit_tests/harness/test_task_completion_extensions.py::test_goal_report_outside_goal_round_does_not_force_finish ^
  tests/unit_tests/harness/test_task_completion_extensions.py::test_rejected_goal_report_does_not_force_finish ^
  tests/unit_tests/harness/test_task_completion_extensions.py::test_terminal_goal_report_invokes_transcript_assessor ^
  tests/unit_tests/harness/test_task_completion_extensions.py::test_continue_goal_report_does_not_invoke_transcript_by_default ^
  tests/unit_tests/harness/test_task_completion_extensions.py::test_attempt_context_uses_latest_model_window_without_duplication
```

## 7. 开发代码量（不含测试）

统计范围：`f1f09c34^..613810dc` 对 `openjiuwen/` 的 diff（不含 `tests/`）。

| 模块 | 新增 | 删除 |
| --- | ---: | ---: |
| `openjiuwen/harness/goal/` | 1036 | 0 |
| `openjiuwen/harness/prompts/`（goal section/tool） | 522 | 0 |
| `openjiuwen/harness/tools/goal.py` | 197 | 0 |
| `openjiuwen/harness/schema/interaction.py` | 366 | 0 |
| `openjiuwen/harness/deep_agent.py` | 647 | 4 |
| `openjiuwen/harness/rails/task_completion_rail.py` | 583 | 32 |
| `openjiuwen/harness/task_loop/` | 148 | 1 |
| `openjiuwen/core/single_agent/rail/base.py` | 10 | 0 |
| **合计** | **3509** | **37** |

开发新增+修改代码量 **3546** 行（3509 新增 + 37 删除，不含测试用例代码）。

## 8. 自验结论

占位：执行第 6 节命令后回填通过率。
