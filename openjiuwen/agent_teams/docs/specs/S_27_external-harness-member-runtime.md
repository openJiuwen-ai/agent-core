# S_27 External Harness Member Runtime

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/external/member_runtime.py`、`openjiuwen/agent_teams/external/cli_agent/spawn.py`、`openjiuwen/agent_teams/spawn/external_cli_spawn.py` |
| 最近一次修订日期 | 2026-10-22 |
| 关联 feature | F_95_dsh-external-harness-adapter.md、F_96_protocol-harness-providers-and-member-migration.md、F_113_external-harness-builtin-model-selection.md、F_114_swarmflow-thirdparty-agent.md、F_115_external-session-fork.md、F_116_swarmflow-thirdparty-model-source.md、F_117_third-party-harness-provider-members.md、harness F_08_provider-owned-model-config.md、harness F_09_auth-fallback-as-a-protocol-capability.md、F_125_team-expert-catalog-and-spawn.md |

## 范围 / 边界

本规约定义由宿主拥有生命周期的三方 Harness（`openjiuwen.harness_protocol.HarnessProtocol` 实现）
如何成为团队成员：`ExternalHarnessMemberRuntime` 的契约、它对 `harness_providers.HarnessIOAdapter`
的依赖、`build_cli_runtime` 对 claude / codex 与三方 provider 的分派，以及 `external_cli_spawn` 的绑定顺序。

不在范围内：协议本身（`openjiuwen/harness_protocol`）、provider 实现（`openjiuwen/harness_providers`，
见 harness `S_19`）、adapter 型子进程 CLI（`CliRuntimeBase`，见 F_22 / F_25）、
`ExternalTeamClient` 直连接入面（F_21 / F_26）。

## 不变量

1. **只消费一次 `events()`**。member runtime 通过 `HarnessIOAdapter` 持有唯一的 continuous
   consumer；宿主永远不直接读取 harness 事件流，也不并发使用 `turn_events()`。
2. **投影在 adapter，团队语义在 runtime**。`OutputSchema` chunk 的形状（`llm_output` /
   `llm_reasoning` / `tool_call` / `tool_result` / `__interaction__`）由 `HarnessIOAdapter` 决定；
   member runtime 不复制投影逻辑，只叠加 team session、可靠性、观测、fallback 持久化与 MCP 挂载。
3. **宿主服务在 start 时注入**。`_host_context` 用 `dataclasses.replace` 把成员 child AgentSession 的
   checkpoint / checkpoint sink、`bind_mcp_servers` 的 MCP、`bind_tools` 的 Native Tools、adapter 的
   interaction handler 与相应 `HostCapability` 合并进 provider `HarnessContext`；context factory 只描述
   provider-neutral 字段，不作为团队 Native Tools 的第二注入入口。
4. **checkpoint 落在成员自己的 AgentSession**。sink 写 `external_runtime = {backend: <card.name>,
   checkpoint: <envelope>}`；`resume_external_backend=True` 时必须读到同 backend 的 checkpoint，否则
   `HarnessStateError`，且以 `ResumePolicy.REQUIRE_RESUME` 启动；有 checkpoint 但非严格模式时用
   `RESUME_IF_AVAILABLE`。
   `require_checkpoint=True`（默认 `False`）与 `resume_external_backend` 联用：指针缺失时 `start()` 抛
   `HarnessProtocolError` 而非告警后新开会话——供已知成员有历史的宿主（swarmflow 冷续跑）使用；团队对话
   场景保持默认。`build_cli_workflow_runtime()`（`external/cli_agent/spawn.py`）是 `build_cli_runtime()` 的
   工作流兄弟：`HarnessContext` 直构、无 `OPENJIUWEN_TEAM_JOIN`、`inject_mcp=False`、无团队系统提示词，
   启动件取自 `ExternalCliAgentSpec` 条目（`F_114`）。
`seed_checkpoint=<provider data>` 在首次 `start()` 前把宿主产生的指针写成该成员自己的 checkpoint 信封
（`agent_id` / `host_session_id` 取自 context），供 fork 子会话以 `require_checkpoint` 续接；只在首启消费一次。
`HarnessCard.fork_modes` 声明 provider 可原生物化的分支模式（`F_115`）。
工作流成员在发射前经 `plan_external_model` 校验并解析三参数（`model` / `builtin_model` / `effort`，
`build_cli_workflow_runtime(model_config=...)` 接收结果），命中 pool → 端点配置、命中目录 → CLI 自身登录、
违反规则非重试失败、**绝不回退**（`F_116`）。
5. **可靠性只由结构化事件驱动**。`TurnLifecycleEvent(STARTED)` → `begin_attempt(phase="turn")`；
   `FAILED` → 用 `TurnResult.error`（`category` / `code` / `provider_data.sdk_error_type` /
   `http_status`）`finalize_failure` 一次；`DiagnosticEvent(data.kind == "retrying")` →
   `publish_retrying`；`start` 抛出的 `ProviderStartupError.error` → `finalize_failure` +
   `mark_member_error`。runtime 不解析 SDK 异常文本。
6. **`immediate=True` 是能力感知的**。IDLE 时为 AUTO；RUNNING 且 provider 声明 STEER 时为 STEER，
   否则 FOLLOW_UP；STEER 与 terminal 竞争失败且 provider 已 IDLE 时重试 AUTO。abort 同理：优先声明的
   模式，其次另一种，最后按 `stop_on_unsupported_force_abort` 停止整个 cycle 或抛
   `UnsupportedHarnessCapabilityError`。
7. **团队上下文搭车语义不变**。`send` 在投递前把 `TeamContextTracker.pending_text` 拼到正文最前，
   投递成功后才 `commit`；`announce_team_context` 单独投递；`InteractiveInput` 不搭车。
8. **轨迹观测只消费协议事件**。runtime 不接任何厂商观测通道；`bind_trajectory_recorder` 绑定
   `HarnessTrajectoryRecorder` 后，`_host_context` 声明 `HostCapability.MODEL_REQUEST_OBSERVATION`，
   每个 envelope 原样交给 recorder，`TurnLifecycleEvent(STARTED)` 前先经 `resolve_member_turn` 开成员
   turn 并 `record_turn_identity`，`send` 时 `record_input`，`stop` 时 `close`。recorder 异常只记
   debug 日志，不影响成员。Claude `transport_factory` 仍只在 `build_cli_runtime` 构造 harness 时注入。
   见 [[F_112_harness-protocol-trajectory-observation]]。
9. **legacy 回调名保留**。`harness.state`（`old` / `new` / `session_id`）与 `harness.round`
   （`kind` / `round_id` = 协议 `turn_id` / `result` = `TurnResult | None`）是 `StreamController`
   的兼容契约；不得反向把 `round` 写进公共协议。
10. **认证 fallback 先持久化再生效**。runtime 把自己作为 adapter 的 `provider_interaction_handler`
    注入（因此 context 总是带 `HostCapability.PROVIDER_INTERACTION`），只应答
    `request_type == "auth_fallback"`：`bind_fallback_promotion` 绑定的 `promote()` 返回 `True` →
    `COMPLETED`，返回 `False` 或抛异常 → `DECLINED`（provider 随即回退原生端点）；未绑定 promotion
    时直接 `COMPLETED`；其它 request type 一律 `DECLINED`。`ProviderEvent("auth_fallback_activated")`
    只作日志观测，不再触发持久化。认证 fallback 只挂给**原生端点**成员（`ExternalCliModelConfig`
    的 `api_base` 与 `provider` 都为空：未配模型，或选了内置模型）；已在端点上的成员不挂。能否挂由
    `backends.takes_auth_fallback(cli_agent)` 判定：按 backend 的 `provider`（claude → claudecode、codex →
    codex、三方即自身）解析 provider，要求它实现 `ModelConfigProvider` 且 card 同时声明 `AUTH_FALLBACK` 与
    `ENDPOINT_MODELS`（回退是池里的端点模型），adapter 恒假（harness
    [[F_09_auth-fallback-as-a-protocol-capability]]）；不按 CLI 名字写死。三方成员同样绑定 `bind_fallback_promotion`。
11. **内置模型先落库、再推活成员**（[[F_113_external-harness-builtin-model-selection]]）。
    `TeamMember.options.builtin_model` 是单一事实来源：`build_context_from_db` 把它放进
    `TeamRuntimeContext.builtin_model`，`external_cli_spawn` 按 **内置模型 > pool 分配 > 静态
    `model_config`** 取成员模型。运行中切换只经 `set_model_selection` → `HarnessModelControl.set_model`，
    正在跑的 turn 不换模型，下一个 turn 前生效；harness 未运行时返回 `False`，由下次启动读 options 生效。
    认证 fallback 被持久化（`promote_member_fallback_model`）时同时清掉 `builtin_model`。
12. **团队工具按 provider 的进程内通道注入**。Claude 通过 SDK in-process MCP server 注册工具；
    Codex 仅通过 `bind_tools` 延迟绑定 `ExternalTeamToolGateway`，runtime 启动时写入
    `HarnessContext.tools`，在
    `thread/start.dynamicTools` 注册顶层 function tools，并通过 `item/tool/call` server request
    回调执行。两条通道都在成员 `configure` 后基于真实 `TeamBackend` 调用
    `create_team_tools`，不经过 `ExternalTeamClient`、外部消息传输或 Gateway WebSocket。
    Codex gateway 固化父团队 session id，每次 `invoke` 用 `set_session_id` /
    `reset_session_id` 临时绑定；首次启动和 checkpoint 恢复都由 spawn 路径重新构造 gateway，
    因而动态表与消息 topic 始终使用当前父团队 session。
13. **三方 provider 成员只拿通用配置与 stdio 团队工具**（[[F_117_third-party-harness-provider-members]]）。
    `backend_for` 的查找顺序是 SDK backend → 子进程适配器 → 三方 provider（`available_providers()`
    中不属于内置 `PROVIDER_NAMES` 的名字），同名时前者永远优先。三方成员的 harness 由
    `resolve_provider(name).create(config)` 建出，`config` 只含 `cwd` / `env` / `inherit_process_env` /
    `skills` / `skill_conflict`，以及团队配置了的 `cli_path` / `system_prompt_mode`。card 声明
    `MCP_TOOLS` 时绑定 `mcp_server_command` 的 `McpServerConfig(STDIO)`（env 仅 `MCP_SERVER_ENV_VARS`），
    否则成员没有团队工具。认证 fallback 只交给声明 `AUTH_FALLBACK` 的三方 provider（spawn 的
    `_negotiate_provider_fallback` 丢弃其余并记日志）；`command_override` / `codex_bin` /
    `ssh_transport` / claude、codex 专属参数直接拒绝。
14. **成员模型一律经 provider 的 `model_config` 映射**（harness [[F_08_provider-owned-model-config]]）。
    团队把内置模型（`builtin_model`）、池分配与静态 `model_config` 都转成 `ModelChoice`
    （`cli_agent.spawn.model_choice`：无 `api_base` / `provider` 即内置），连同仅在内置模型上挂的认证
    fallback 交给 `harness_providers.factory.provider_model_config`，并入 provider 配置；claude / codex /
    三方没有分支，不能跑的选择即 `AGENT_TEAM_CONFIG_INVALID`。端点模型只交给声明 `ENDPOINT_MODELS`
    的三方 provider（`backends.provider_takes_endpoint_models`）：未声明时 spawn 丢弃并记日志，
    `_provider_filter_for_cli` 也不向 leader 列出池里的模型。运行中切换仍走 `set_model_selection`。
    `external_cli.fallback_model_name`（`spawn_teammate` / `spawn_expert` / 内联成员的同名对象）可选：省略 / `null` / 空白都是"不回退"；只有能回退的 CLI 才在
    存在兼容模型时被要求填写、才出现在 `compatible_fallback_models` 目录里，其余 CLI 给出模型名即报错并提示省略。
15. **成员启动配置的来源有三层**（`_external_cli_config_for_member`，F_125）：预定义外部成员自己的
    `external_cli` → 成员行持久化的配置（`spawn_expert` 与调度模式 `create_task` 的外部专家内联成员写入
    `options.agent_template.external_cli`，经
    `TeamRuntimeContext.template_external_cli` 传入）→ `TeamAgentSpec.external_cli_agents` 中同类型的声明。
    `spawn_expert` 写入的是 `merge_external_cli_config(声明配置, 模板配置)`：以声明的宿主级配置为底，叠加模板
    的技能（追加在声明技能之后）、`skill_conflict` 与非默认的 `runtime.config` 键；三条外部 CLI 登记路径
    （`spawn_teammate` 带 `cli_agent`、`spawn_expert`、内联成员）共用 `tool_member.register_external_cli_member`（声明检查 +
    模型规则 + 登记），CLI 类型未声明时不登记。

## 接口契约

```python
class ExternalHarnessMemberRuntime:
    def __init__(self, *, harness: HarnessProtocol, context: HarnessContext | ContextFactory,
                 team_context_tracker=None, stop_on_unsupported_force_abort=False,
                 resume_external_backend=False, agent_kind: str | None = None,
                 inject_mcp=False, mcp_server_name="openjiuwen-team") -> None
    # pre-start bindings
    def bind_team_context_tracker(tracker) / bind_mcp_servers(servers) / bind_tools(tools) / bind_trajectory_recorder(recorder)
    def bind_fallback_promotion(promote)   # promote: () -> Awaitable[bool]; answers the auth_fallback interaction
    def add_teardown_hook(hook)
    def bind_reliability_context(*, session_id, team_backend, leader_name, update_status_cb, messager)
    # MemberRuntime surface
    async start(*, team_session=None) / stop() / dispose(); state; session_id; outputs()
    async send(content, *, immediate=False) -> SendReceipt | None
    async announce_team_context() / abort(*, immediate=False) / pause() / resume(*, query=None)
    async set_model_selection(selection: ModelSelection) -> bool   # False: harness not running
                                                                   # raises if the card lacks MODEL_SELECTION
    async subscribe(*, on_state=None, on_round=None)
    has_pending_interrupt() / is_pending_interrupt_resume_valid(user_input)
    # read-only
    provider_name; reliability_agent_kind; trajectory_recorder; inject_mcp; mcp_server_name
```

`build_cli_runtime(ctx, ...)` 对 `ctx.cli_agent == "claude" | "codex"` 返回
`ExternalHarnessMemberRuntime`（provider 分别为 `ClaudeCodeHarness` / `CodexHarness`），对三方 provider
名字同样返回 `ExternalHarnessMemberRuntime`（harness 由 `resolve_provider` 建出，`agent_kind` 为 provider
名字），适配器 backend 返回 `CliRuntimeBase` 子类；返回类型别名 `MemberRuntimeLike`。claude 分支保留
`ExternalCliAgentSpec` 的 `cli_path` / `add_dirs` / `ssh_transport` / 模型与 fallback 语义，codex
分支保留 `codex_bin` / `bypass_approvals_and_sandbox` / `turn_idle_*` / `mcp_default_tools_approval_mode`
并在成员 `configure` 后把本地团队工具作为 `ExternalTeamToolGateway` 注入。

`external_cli_spawn` 的绑定顺序：`configure` → `bind_team_context_tracker` →
`_bind_protocol_member_team_tools`（claude-code 绑定 in-process MCP；codex 绑定 Dynamic Tools）→
`bind_reliability_context`
→ `Runner.run_agent_team(member=True)`；`finally` 调 `runtime.stop()`。

## 数据结构

| 项 | 位置 | 说明 |
|---|---|---|
| `external_runtime` state | 成员 child AgentSession | `{backend, checkpoint}`；`checkpoint` 是 `HarnessCheckpoint` 的 JSON 信封（`checkpoint_to_dict` / `checkpoint_from_dict`） |
| `_extra_mcp_servers` | runtime 内存 | start 前绑定的 `McpServerConfig` 列表，start 时并入 context |
| `builtin_model` | `TeamMember.options` | `{model, effort}`：成员在 CLI 自身登录上跑的内置模型；spawn / `set_member_model` 写入，fallback 提升时清除 |
| `_bound_tools` | runtime 内存 | 成员 configure 后绑定的本地 `ToolGateway`，start 时并入 context |
| `_round_seq` / `_current_round_id` | runtime 内存 | 可靠性 `round_id`（单调整数），与协议 `turn_id` 并存 |

## 与其它 spec 的关系

- `S_18_harness-interaction-contract.md`：`MemberRuntime` 表面；本 runtime 是其外部 harness 实现。
- `S_19_reliability-framework.md`：`RuntimeReliabilityContext` 的 delivery 语义；本规约只定义事件到
  该上下文的映射。
- harness `S_19_harness-providers.md`：provider 骨架、IO adapter 与 factory 的契约。
