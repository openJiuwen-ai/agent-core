# 跨群分享原生确认与等待现场结清

## 元信息

- 日期：2026-10-28
- 依据：JiuwenClaw `docs/superpowers/specs/2026-10-28-cross-group-sharing-migration-design.md`
  与 `docs/superpowers/plans/2026-10-28-group-sharing-implementation.md` 的 Task 1，已批准。
- 状态：已实现并通过本地 Persistent Agent 回归；不代表真实渠道验收完成。

## 背景、范围与约束

群历史搜索命中其他群时，宿主先冻结候选，Core 仅收到不透明审批 ID。
本特性覆盖 Core 的安全异常、事件关联、原生暂停恢复、两阶段答复持久化及终止请求清理。
候选、审批人鉴权、永久授权、投递复核由宿主负责；full_access 不免除业务分享同意。

## 方案与关键决策

1. `GroupMessageApprovalRequired(approval_id=...)` 转为 native confirm；固定 owner 与
   `metadata.kind=group_share` 区分普通工具许可。只从 `find_own_answer` 消费自己的答复。
2. 新回复按 interrupt ID 定址，旧 ConfirmReply 的 tool_call_id 路径保留。
   先前工具许可通过白名单 codec 往返 `carried_answers`，避免分享再次暂停后丢失批准。
3. `discard_persistent_confirmation(backend, *, agent_id, session_id, request_id,
   spec_fingerprint) -> bool` 只结清匹配的等待现场。宿主先持久化失败并持有 Session 执行权。
   保留真实结果、窗口/归档元数据、Session 记住的工具许可，缺失结果只补未知结果占位。
4. 只给目标 tool_intent rail 增加中断不记完成的分支，保留 state 拆分与 call/digest 校验。

```text
普通工具 ask -> 批准 -> 宿主分享门禁 -> 再暂停 -> JSON 保存/重建
  -> 分享批准 -> 宿主重新鉴权 -> 原请求继续
  -> 分享拒绝 -> 安全工具反馈（不再读取宿主）
宿主终止 -> failed 持久化 -> discard -> 消费审批 -> 放行 Session
```

## 替代方案与排除理由

模型先读取正文再征求同意会提前披露；复用原提问者的普通工具许可会混淆审批主体。
删除整个窗口会丢失真实结果；宿主直接修改 Core JSON 会形成第二套状态实现，均不采用。

## 验证设计与实际记录

聚焦 public 契约、typed owner、两阶段持久化重建、并行调用、full_access 拒绝及 discard 后下一请求。
测试采用真实 native、内存 KV 和本地模型，无平台访问或 live DB。

已观察的 RED：缺 public 异常/approval_ref（2 failed）；缺 discard、carried codec 与旧 ID 补值
（7 failed）；原生搜索吞掉分享信号（15 failed）；工具信号未转 native（2 failed）；旧权限 owner
丢失和旧 metadata kind 不兼容（3 failed / 2 passed）。对应聚焦 GREEN 为 7、26、5 passed，
首轮 Persistent Agent 整包 995 passed / 10 skipped；主进程最终重新执行整包为
**1003 passed / 10 skipped / 2 warnings，62.37 秒**，日志为本机临时目录
`new-sharing-core-final.log`。

实现者及主进程分别复核 public/owner/并发/失败结清/回执校验及 scope。
宿主另以真实 native Core、SQLite 与所有者 RPC 验证一次批准和拒绝，模型为本地替身。
独立最终审查未完成：审查子进程因账号额度中断，不能将自审视为独立审查结果。

## 已知遗留与边界

宿主负责 owner RPC、失败持久化及 consumed crash gap 清理接入，相关实现位于目标 JiuwenClaw 仓。
不清除历史窗口中已披露的派生内容，不提供 OS 沙箱；真实渠道验收另行进行。

## 本次精确文件范围

所有路径相对于 `C:/yzj/files/open_source/agent-core_3.24`。

| 类别 | 文件 |
| --- | --- |
| 产品 | `openjiuwen/harness/persistent_agent/__init__.py` |
| 产品 | `openjiuwen/harness/persistent_agent/ports.py` |
| 产品 | `openjiuwen/harness/persistent_agent/tools/group_history.py` |
| 产品 | `openjiuwen/harness/persistent_agent/execution/group_share_rail.py`（新增） |
| 产品 | `openjiuwen/harness/persistent_agent/execution/tool_intent_rail.py` |
| 产品 | `openjiuwen/harness/persistent_agent/execution/entries.py` |
| 产品 | `openjiuwen/harness/persistent_agent/execution/session_state.py` |
| 产品 | `openjiuwen/harness/persistent_agent/schema/events.py` |
| 产品 | `openjiuwen/harness/persistent_agent/schema/spec.py` |
| 产品 | `openjiuwen/harness/persistent_agent/schema/session_record.py` |
| 测试 | `tests/unit_tests/harness/persistent_agent/test_group_sharing.py`（新增） |
| 测试 | `tests/unit_tests/harness/persistent_agent/test_group_history.py` |
| 测试 | `tests/unit_tests/harness/persistent_agent/test_entries.py` |
| 测试 | `tests/unit_tests/harness/persistent_agent/test_session_state.py` |
| 测试 | `tests/unit_tests/harness/persistent_agent/test_tool_intent_rail.py` |
| 文档 | `openjiuwen/harness/persistent_agent/docs/features/F_25_group-sharing-confirmation.md`（本文，新增） |
| 文档 | `openjiuwen/harness/persistent_agent/docs/specs/S_02_events-and-result-handoff.md` |
| 文档 | `openjiuwen/harness/persistent_agent/docs/specs/S_03_context-and-windows.md` |
| 文档 | `openjiuwen/harness/persistent_agent/docs/specs/S_05_confirmation-and-continuation.md` |
| 文档 | `openjiuwen/harness/persistent_agent/docs/specs/S_08_tool-effects-and-intent.md` |
| 文档 | `openjiuwen/harness/persistent_agent/docs/specs/S_09_group-chat-and-knowledge.md` |
| 文档 | `openjiuwen/harness/persistent_agent/docs/designs/persistent-agent-development-plan.md` |

## 验证命令

工作目录固定为上述目标仓；使用该仓已有 `.venv`，没有安装或改动依赖。

```powershell
.venv/Scripts/python.exe -m pytest -o addopts='' tests/unit_tests/harness/persistent_agent/test_group_sharing.py -q
.venv/Scripts/python.exe -m pytest -o addopts='' tests/unit_tests/harness/persistent_agent/test_group_sharing.py tests/unit_tests/harness/persistent_agent/test_session_state.py -k 'public_group or intercepted_approval or discard_confirmation' -q --tb=short
.venv/Scripts/python.exe -m pytest -o addopts='' tests/unit_tests/harness/persistent_agent/test_group_sharing.py tests/unit_tests/harness/persistent_agent/test_group_history.py tests/unit_tests/harness/persistent_agent/test_session_state.py tests/unit_tests/harness/persistent_agent/test_entries.py -k 'group_share or carried_answers or legacy_interruption_json or discard_confirmation or intercepted_approval' -q --tb=short --show-capture=no
.venv/Scripts/python.exe -m pytest -o addopts='' tests/unit_tests/harness/persistent_agent/test_entries.py tests/unit_tests/harness/persistent_agent/test_session_state.py -k 'two_stage or legacy_metadata_kind' -q --tb=short --show-capture=no
.venv/Scripts/python.exe -m pytest -o addopts='' tests/unit_tests/harness/persistent_agent -q --tb=short --show-capture=no
.venv/Scripts/python.exe -m pytest -o addopts='' tests/unit_tests/harness tests/unit_tests/core/single_agent tests/unit_tests/agent/react_agent/interrupt tests/unit_tests/agent/react_agent/test_react_agent_tool_fact_retention.py tests/unit_tests/harness_providers --continue-on-collection-errors -q --tb=short --show-capture=no
$coreSharingFiles = @(git diff --name-only -- '*.py') + @(git ls-files --others --exclude-standard -- '*.py')
.venv/Scripts/ruff.exe check @coreSharingFiles --output-format concise
git diff --check
```

宽回归首次未带 `--continue-on-collection-errors`，6 个收集错误阻止执行；随后使用上列命令取得可执行部分结果。
六项共同原因是已有环境缺少 `opentelemetry.exporter`，对应模块没有本次改动：

- `tests/unit_tests/harness/observability/test_rail.py`
- `tests/unit_tests/harness/observability/test_run_span.py`
- `tests/unit_tests/harness/observability/test_span_context.py`
- `tests/unit_tests/harness/observability/test_subagent.py`
- `tests/unit_tests/harness/rails/evolution/test_symphony_graph_evolution_rail.py`
- `tests/unit_tests/harness_providers/test_trajectory_recorder.py`

宽回归实际结果：**8167 passed、96 skipped、30 failed、6 errors、4 subtests passed**，
109 warnings，539.74 秒。没有 Persistent Agent 用例失败。以下为完整的 30 项失败清单；
相关产品/测试模块均未修改，未在干净 HEAD 逐项复验，因此不把全部失败武断认定为已证明的基线。
没有扩散修复、安装依赖或修改锁文件。

| 原因/范围 | 精确失败 node ID（相对于 `tests/unit_tests/`） |
| --- | --- |
| Windows 临时路径长度断言 256 > 260 | `harness/personal_context/test_context_pipeline_rules.py::test_candidate_navigation_and_reference_graph_support_windows_long_paths` |
| evolution 准备记录为空，待原模块排查 | `harness/rails/evolution/test_after_tool_call_trigger.py::test_after_tool_call_trigger_fires_with_the_ended_tool_span` |
| observability 不可导入 | `harness/subagent_runtime/test_instance.py::test_turn_aliases_parent_root_to_subagent_session_until_cleanup` |
| Windows 临时归档路径 FileNotFoundError | `harness/test_compression_recall_tool.py::test_tool_requires_runtime_session_and_recalls_current_session` |
| 同上 | `harness/test_compression_recall_tool.py::test_tool_renders_compact_content_for_model` |
| 同上 | `harness/test_compression_recall_tool.py::test_tool_searches_across_archives_when_memory_id_omitted` |
| 同上 | `harness/test_compression_recall_tool.py::test_tool_omits_hint_when_chunks_match` |
| RLOnlineRail 因缺 observability 依赖未装配 | `harness/test_deep_agent.py::test_resolve_deep_agent_parts_adds_rl_online_rail_from_env` |
| Windows shell 未执行 sleep 语义 | `harness/tools/test_bash/test_bash_tool.py::test_timeout_returns_collected_output` |
| shell allowlist 拒绝 grep 命令 | `harness/tools/test_filesystem_tools.py::test_grep_tool` |
| 同上 | `harness/tools/test_filesystem_tools.py::test_grep_tool_content_mode_supports_pagination_and_glob` |
| 同上 | `harness/tools/test_filesystem_tools.py::test_grep_tool_files_mode_excludes_vcs_directory` |
| 同上 | `harness/tools/test_filesystem_tools.py::test_grep_tool_defaults_to_content_mode` |
| 同上 | `harness/tools/test_filesystem_tools.py::test_grep_tool_count_mode_returns_structured_counts` |
| Windows shell 中文编码 | `harness/tools/test_shell_tools.py::test_bash_tool_ls_chinese_filename` |
| Windows 非目录路径错误类型 | `harness/tools/worktree/test_backend.py::TestGitBackendRemove::test_remove_nonexistent_raises` |
| Windows 正反斜杠断言 | `harness/tools/worktree/test_git.py::TestFindGitRoot::test_in_git_repo` |
| 同上 | `harness/tools/worktree/test_manager.py::TestEnter::test_enter_creates_worktree_and_sets_session` |
| OTLP receiver 不可用 | `harness_providers/telemetry/test_otlp_receiver.py::test_shared_receiver_fans_out_and_detaches` |
| 缺 opentelemetry.proto | `harness_providers/telemetry/test_otlp_receiver.py::test_grpc_listener_serves_traces_and_logs` |
| 缺 openai_codex | `harness_providers/test_codex.py::test_thread_start_injects_raw_events_and_dynamic_tools_into_low_level_request` |
| Windows 路径断言 | `harness_providers/test_codex_observation.py::test_rollout_inferences_become_ordered_model_request_events` |
| tool_search_call 与 tool_search 断言差异 | `harness_providers/test_codex_observation.py::test_a_tool_call_the_app_server_never_announces_is_still_reported` |
| 首 token latency 为空，待原模块排查 | `harness_providers/test_codex_observation.py::test_each_request_carries_the_first_token_latency_the_cli_reported` |
| Windows 不保留 POSIX 执行位 | `harness_providers/test_skills.py::test_complete_bundle_and_conflict_policies[claudecode-.claude/skills]` |
| 同上 | `harness_providers/test_skills.py::test_complete_bundle_and_conflict_policies[codex-.agents/skills]` |
| 同上 | `harness_providers/test_skills.py::test_complete_bundle_and_conflict_policies[dsh-.dsh/skills]` |
| Windows symlink 权限 WinError 1314 | `harness_providers/test_skills.py::test_symlink_escape_is_rejected_and_self_copy_is_noop` |
| 同上 | `harness_providers/test_skills.py::test_internal_symlinks_are_materialized` |
| 同上 | `harness_providers/test_skills.py::test_replace_destination_symlink_copies_files_without_mutating_source` |
