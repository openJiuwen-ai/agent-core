# S_27 External Harness Member Runtime

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/external/member_runtime.py`、`openjiuwen/agent_teams/external/cli_agent/spawn.py`、`openjiuwen/agent_teams/spawn/external_cli_spawn.py` |
| 最近一次修订日期 | 2026-09-09 |
| 关联 feature | F_95_dsh-external-harness-adapter.md、F_96_protocol-harness-providers-and-member-migration.md |

## 范围 / 边界

本规约定义由宿主拥有生命周期的三方 Harness（`openjiuwen.harness_protocol.HarnessProtocol` 实现）
如何成为团队成员：`ExternalHarnessMemberRuntime` 的契约、它对 `harness_providers.HarnessIOAdapter`
的依赖、`build_cli_runtime` 对 claude / codex 的分派，以及 `external_cli_spawn` 的绑定顺序。

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
   checkpoint / checkpoint sink、`bind_mcp_servers` 的 MCP、adapter 的 interaction handler 与相应
   `HostCapability` 合并进 provider `HarnessContext`；context factory 只描述 provider-neutral 字段。
4. **checkpoint 落在成员自己的 AgentSession**。sink 写 `external_runtime = {backend: <card.name>,
   checkpoint: <envelope>}`；`resume_external_backend=True` 时必须读到同 backend 的 checkpoint，否则
   `HarnessStateError`，且以 `ResumePolicy.REQUIRE_RESUME` 启动；有 checkpoint 但非严格模式时用
   `RESUME_IF_AVAILABLE`。
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
8. **provider-private 观测接线不进公共协议**。Codex `notification_observer` 与 Claude
   `transport_factory` 只在 `build_cli_runtime` 构造 harness 时注入；span bridge 经
   `bind_span_bridge` 绑定，按 Protocol（`MemberSpanBridge` / `ChunkRecordingSpanBridge` /
   `NativeObservationSpanBridge`）而非 `hasattr` 分派。
9. **legacy 回调名保留**。`harness.state`（`old` / `new` / `session_id`）与 `harness.round`
   （`kind` / `round_id` = 协议 `turn_id` / `result` = `TurnResult | None`）是 `StreamController`
   的兼容契约；不得反向把 `round` 写进公共协议。
10. **认证 fallback 先持久化再生效**。runtime 把自己作为 adapter 的 `provider_interaction_handler`
    注入（因此 context 总是带 `HostCapability.PROVIDER_INTERACTION`），只应答
    `request_type == "auth_fallback"`：`bind_fallback_promotion` 绑定的 `promote()` 返回 `True` →
    `COMPLETED`，返回 `False` 或抛异常 → `DECLINED`（provider 随即回退原生端点）；未绑定 promotion
    时直接 `COMPLETED`；其它 request type 一律 `DECLINED`。`ProviderEvent("auth_fallback_activated")`
    只作日志观测，不再触发持久化。

## 接口契约

```python
class ExternalHarnessMemberRuntime:
    def __init__(self, *, harness: HarnessProtocol, context: HarnessContext | ContextFactory,
                 team_context_tracker=None, stop_on_unsupported_force_abort=False,
                 resume_external_backend=False, agent_kind: str | None = None,
                 inject_mcp=False, mcp_server_name="openjiuwen-team") -> None
    # pre-start bindings
    def bind_team_context_tracker(tracker) / bind_mcp_servers(servers) / bind_span_bridge(bridge)
    def bind_fallback_promotion(promote)   # promote: () -> Awaitable[bool]; answers the auth_fallback interaction
    def add_teardown_hook(hook)
    def bind_reliability_context(*, session_id, team_backend, leader_name, update_status_cb, messager)
    # MemberRuntime surface
    async start(*, team_session=None) / stop() / dispose(); state; session_id; outputs()
    async send(content, *, immediate=False) -> SendReceipt | None
    async announce_team_context() / abort(*, immediate=False) / pause() / resume(*, query=None)
    async subscribe(*, on_state=None, on_round=None)
    has_pending_interrupt() / is_pending_interrupt_resume_valid(user_input)
    # read-only
    provider_name; reliability_agent_kind; span_bridge; inject_mcp; mcp_server_name
```

`build_cli_runtime(ctx, ...)` 对 `ctx.cli_agent == "claude" | "codex"` 返回
`ExternalHarnessMemberRuntime`（provider 分别为 `ClaudeCodeHarness` / `CodexHarness`），其余 backend
返回 `CliRuntimeBase` 子类；返回类型别名 `MemberRuntimeLike`。claude 分支保留
`ExternalCliAgentSpec` 的 `cli_path` / `add_dirs` / `ssh_transport` / 模型与 fallback 语义，codex
分支保留 `codex_bin` / `bypass_approvals_and_sandbox` / `turn_idle_*` / `mcp_default_tools_approval_mode`
并把团队 MCP 作为 stdio `McpServerConfig` 预绑定。

`external_cli_spawn` 的绑定顺序：`configure` → `bind_team_context_tracker` →
`_bind_protocol_member_team_tools`（仅 claude-code 且 `inject_mcp`）→ `bind_reliability_context`
→ `Runner.run_agent_team(member=True)`；`finally` 调 `runtime.stop()`。

## 数据结构

| 项 | 位置 | 说明 |
|---|---|---|
| `external_runtime` state | 成员 child AgentSession | `{backend, checkpoint}`；`checkpoint` 是 `HarnessCheckpoint` 的 JSON 信封（`checkpoint_to_dict` / `checkpoint_from_dict`） |
| `_extra_mcp_servers` | runtime 内存 | start 前绑定的 `McpServerConfig` 列表，start 时并入 context |
| `_round_seq` / `_current_round_id` | runtime 内存 | 可靠性 `round_id`（单调整数），与协议 `turn_id` 并存 |

## 与其它 spec 的关系

- `S_18_harness-interaction-contract.md`：`MemberRuntime` 表面；本 runtime 是其外部 harness 实现。
- `S_19_reliability-framework.md`：`RuntimeReliabilityContext` 的 delivery 语义；本规约只定义事件到
  该上下文的映射。
- harness `S_19_harness-providers.md`：provider 骨架、IO adapter 与 factory 的契约。
