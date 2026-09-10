# 三方 Agent Harness 接入开发指南

本文面向希望让自有 Python Agent、CLI Agent SDK 或 Jiuwen 后续 SDK 作为
OpenJiuwen Team 成员运行的开发者，说明如何实现
`openjiuwen.agent_teams.external.protocol` 4.0。

> 当前已提供通用 `ExternalHarnessMemberRuntime`，并以 DSH Python SDK 作为首个协议实现。
> DSH 目前只支持程序化装配；team spawn 尚未通过 provider registry 自动加载三方实现，现有
> Claude Code 与 Codex backend 也尚未迁移。

## 1. 接入层级

本协议定义完整 Harness 行为，而不是一次模型调用：

```text
OpenJiuwen Team
      |
ExternalHarnessMemberRuntime
      |
ExternalHarnessProtocol
      |
三方 Agent Harness
      |
模型 SDK / CLI / 远端 Agent 服务
```

适合直接实现协议的对象包括：

- 维护长期 conversation/session 的 Python Agent SDK；
- 支持多轮 query/receive、steer 和 interrupt 的 coding-agent SDK；
- 对远端 Agent 服务做连接管理的 Python client；
- 自己拥有 Turn 队列和状态机的 CLI wrapper。

如果三方 SDK 只提供一次性 `run(prompt)`，实现方仍需在 Harness 内补齐消息队列、
Turn 生命周期、事件转换、provider interaction、取消和 checkpoint 语义。

### 1.1 统一术语

```text
Session
└── Turn          一次外部输入 -> 一次稳定外部输出
    └── Iteration 一次 Agent Loop 控制循环
        └── Step  一次可观测原子执行动作
```

`Round` 只表示 multi-agent 协作或协议阶段，一个 Round 可以包含多个 Agent Turn。本接入协议属于
单 Agent Harness 边界，因此统一使用 `turn_id`、`TurnLifecycleEvent`、`TurnEventKind` 和
`turn_events()`。不要把一次 Agent Loop 循环称为 step；应使用 iteration，step 留给 model/tool/
memory/middleware 等原子执行动作。

## 2. 固定导入入口

只从公共包导入，不依赖 `external.cli_agent` 或 protocol 私有模块：

```python
from openjiuwen.agent_teams.external.protocol import (
    AbortMode,
    CheckpointReason,
    ContentBlock,
    DeliveryMode,
    EventBufferConfig,
    ExternalHarnessCard,
    ExternalHarnessContext,
    ExternalHarnessInput,
    ExternalHarnessProtocol,
    ExternalHarnessProtocolError,
    ExternalHarnessProvider,
    HarnessCapability,
    HostCapability,
    HarnessCheckpoint,
    HarnessEvent,
    HarnessEventCursor,
    InteractionCancelReason,
    MessageRole,
    MonetaryAmount,
    OutputEvent,
    OutputChannel,
    OutputKind,
    OutputOperation,
    SendReceipt,
    ToolApprovalRequest,
    ToolInvocation,
    TurnResult,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnMessage,
    TurnStatus,
    TurnTermination,
    TurnTerminationKind,
    TurnUsage,
    UnsupportedHarnessCapabilityError,
    event_retention,
    harness_event_from_dict,
    harness_event_to_dict,
    validate_interaction_response,
)
```

`@runtime_checkable` 只能检查成员是否存在，不能验证签名、事件次序、状态机、并发安全或
checkpoint 可序列化；生产 provider 必须另跑行为契约测试。

## 3. 生命周期和状态机

每个 Harness 实例只代表一个 team member：

```text
构造实例
   |
start(context)
   |
IDLE --send--> RUNNING --turn terminal--> IDLE
   |                    |
   |                    +--pause--> PAUSED --resume--> RUNNING
   |
stop()
   |
TERMINATED + events EOF
```

约束：

- `start()` 返回时必须可接收输入，状态为 `IDLE`；
- `send()` 只确认消息已接受，不等待模型完成；
- receipt 在接受时返回该输入关联的 `turn_id`；STEER 返回 active Turn ID；
- public command 允许从不同协程并发调用；
- Harness 内只有一个逻辑 state writer，例如 supervisor task；
- `stop()` 幂等，并使 `events()`/未完成的 `turn_events()` 最终结束；
- 每个 Turn 只有一个 `STARTED` 和一个 terminal Turn event；PAUSED/RESUMED 保持同一 `turn_id`，
  不终结 Turn；
- 未声明能力抛 `UnsupportedHarnessCapabilityError`。

### DeliveryMode

| 模式 | 语义 |
|---|---|
| `AUTO` | IDLE 时启动新 Turn；RUNNING 时排为 follow-up |
| `STEER` | 注入当前 Turn；要求 `HarnessCapability.STEER` |
| `FOLLOW_UP` | 当前 Turn 完成后启动后继 Turn |

### Capability negotiation

`HarnessCapability` 描述 Harness 自身支持的命令，例如 STEER、PAUSE_RESUME；`HostCapability` 描述
实现依赖的宿主服务，例如 TOOL_APPROVAL、USER_INPUT、MCP_ELICITATION。Provider 在 Card 中分别声明
required/optional host capability 和 compatible protocol versions，并在 `start()` 创建 SDK client、
进程或网络连接前调用 `card.validate_host(...)`。required 缺失时 fail-fast，optional 缺失时对具体请求
安全 decline/fail，不能默认授权。

`FORCE` abort 只要求尽快停止，不承诺回滚已经发生的命令、文件或外部系统副作用。

## 4. 区分 events、interactions 和 hooks

三者方向和阻塞语义不同：

```text
events:       Harness -> consumer
              单向观测，不返回执行决策

interactions: Harness -> host handler -> response -> Harness
              SDK 主动请求，等待审批/输入/执行结果

hooks:        Harness -> lifecycle hook -> policy result -> Harness
              OpenJiuwen 生命周期策略，可阻断或修改执行
```

Claude 的消息流、Codex 的 turn notification stream 都由 provider adapter 内部消费；adapter 将
观测消息发布到协议 observation channel，将 server/control requests 转给 interactions。Hook
callback 则映射到 hooks。不要把三条通道合并成一个双向 event queue。

## 5. Event 信封和 TurnResult

Observation channel 提供两个消费视图：

| 方法 | 边界 | 适用场景 |
|---|---|---|
| `events()` | 从调用开始持续到 `stop`；跨越所有 Turn | MemberRuntime adapter、后台持续消费 |
| `turn_events(turn_id=None)` | 指定已接受 Turn 或下一个 Turn，从 STARTED 到 terminal（均包含） | 串行 query/response、单 Turn 测试 |

这与 Claude SDK 的 `receive_messages()` 和 `receive_response()` 分工一致。两者不是两份消息，也不是
两个订阅：它们消费同一个逻辑单消费者流，禁止并发迭代。简单调用方可以逐轮使用：

```python
first = await harness.send(ExternalHarnessInput("first task"))
first_turn = [event async for event in harness.turn_events(first.turn_id)]

second = await harness.send(ExternalHarnessInput("follow-up"))
second_turn = [event async for event in harness.turn_events(second.turn_id)]
```

`turn_events(turn_id)` 用 ID 校验下一个尚未消费的 Turn，不是乱序选择器，不得为寻找未来 Turn
丢弃中间完整 Turn；不传 ID 时选择下一个 Turn。并发 team runtime 应持续消费 `events()`，按
receipt 的 `turn_id` 聚合。找到 STARTED 后按全局顺序产出所有事件，并在相同 `turn_id` 的
FINISHED/ABORTED/FAILED 产出后
立即结束。PAUSED/RESUMED 是非终态，有限流必须跨越它们继续消费。Terminal event 不能被吞掉，因为
调用方需要从中读取完整 `TurnResult`。Cycle 在 STARTED 前关闭时可空结束；STARTED 后没有 terminal
就关闭必须抛 `ExternalHarnessProtocolError`。

实现必须为 observation channel 加 consumer lease；第二个 active iterator 立即抛
`ExternalHarnessStateError`，不能让两个 async generator 竞争同一个 queue。Iterator 正常结束或
被幂等 `aclose()` 后释放 lease，后续调用才能继续消费；因此公开返回类型使用
`HarnessEventCursor`，不是无法保证 close 的普通 `AsyncIterator`。

复杂 team runtime 应只启动一个长期 `events()` consumer，并自行按 `turn_id` 聚合，不应同时调用
`turn_events()`。每条 `HarnessEvent` 的 `sequence` 必须跨所有载荷类型严格递增；`timestamp`
使用 Unix seconds。session、turn、item、消息关联信息放在信封：

```python
event = HarnessEvent(
    sequence=next_sequence(),
    timestamp=time.time(),
    team_session_id=context.team_session_id,
    member_agent_id=context.member_agent_id,
    session_id=provider_session_id,
    turn_id=turn_id,
    correlation_id=accepted_message_id,
    causation_ids=(accepted_message_id, *steering_message_ids),
    event=OutputEvent(
        output_id="answer-1",
        kind=OutputKind.TEXT,
        content="partial answer",
        operation=OutputOperation.DELTA,
        channel=OutputChannel.ANSWER,
        content_index=0,
    ),
)
await event_queue.put(event)
```

`correlation_id` 用于把事件聚合到同一逻辑 trace，`causation_ids` 列出实际造成该事件的输入/request。
message/turn ID 在 member + team session 内唯一，item/call ID 在 Turn 内唯一，request ID 在 cycle 内
唯一。timestamp/deadline 使用有限 UTC Unix seconds；duration 使用 monotonic clock 计算的毫秒数。

公共载荷包括：

| 载荷 | 用途 |
|---|---|
| `OutputEvent` | 稳定 block ID/index，TEXT/STRUCTURED 表示，ANSWER/REASONING/SYSTEM channel，DELTA/SNAPSHOT/FINAL operation |
| `ItemLifecycleEvent` | tool call、command、file change 等 provider item |
| `UsageUpdatedEvent` | 标准化 token usage |
| `StateChangedEvent` | Harness state 转换 |
| `TurnLifecycleEvent` | Turn start、pause/resume 和唯一 terminal |
| `HookObservedEvent` | hook 执行观测，不参与授权 |
| `DiagnosticEvent` | 已脱敏诊断 |
| `ProviderEvent` | 带命名空间和版本的 provider JSON 扩展 |

不要先转成 OpenJiuwen 内部 `OutputSchema`。现有 `ExternalHarnessMemberRuntime` 在团队运行时边界
负责兼容投影；协议入口仍应保留 SDK item、reasoning、usage 和扩展信息，公共事件才是权威表示。

Observation buffer 必须有正 capacity。retention 只能由 `event_retention(payload)` 推导：lifecycle、
delta、final、warning/error、provider/unknown event 是 REQUIRED；snapshot/cumulative usage 可合并；
debug/info diagnostic 和 hook observation 才可 best-effort drop。队列满且没有安全候选时必须阻塞
producer，禁止丢 terminal 或 delta。

跨进程传输只使用 `harness_event_to_dict` / `harness_event_from_dict`。codec 产生稳定
`event_type + schema_version`；新版本未知事件会成为 `UnknownEvent`，不得在 adapter 中静默删除。

Terminal Turn 必须携带结构化结果，并保证事件与状态匹配：

```python
result = TurnResult(
    status=TurnStatus.COMPLETED,
    messages=(
        TurnMessage(
            message_id="assistant-1",
            role=MessageRole.ASSISTANT,
            content=(ContentBlock(block_id="answer-1", kind="text", content="final answer"),),
        ),
    ),
    final_output="final answer",
    structured_output={"answer": "final answer"},
    usage=TurnUsage(input_tokens=120, output_tokens=48, total_tokens=168),
    cost=MonetaryAmount(micros=25_000, currency="USD"),
    duration_ms=1320,
    provider_data={"provider_stop_reason": "end_turn"},
)
payload = TurnLifecycleEvent(kind=TurnEventKind.FINISHED, result=result)
```

终态对应关系是 FINISHED/COMPLETED、ABORTED/INTERRUPTED、FAILED/FAILED。PAUSED 和 RESUMED 不带
`TurnResult`，并保持原 `turn_id`。失败结果必须携带 `TurnError`；不要把原始 exception、client
object 或可能包含 credential 的响应塞进事件。

`messages` 是标准化完整输出；`final_output`/`structured_output` 只是便捷投影。多个 Claude content
block 或 Codex item 不得挤进一个字符串。INTERRUPTED 必须携带 `TurnTermination` 说明 user abort、
timeout、policy、provider 或 harness stop；货币成本使用 micros + currency，禁止 float 累计。

## 6. Provider-initiated interactions

Provider SDK 需要宿主即时回答时，调用 `context.interactions.handle(request)`：

```python
request = ToolApprovalRequest(
    request_id=sdk_request.id,
    call_id=sdk_request.call_id,
    tool_name=sdk_request.tool_name,
    arguments=sdk_request.arguments,
    session_id=self.session_id,
    turn_id=self._active_turn_id,
    deadline_at=time.time() + 60,
    provider_data={"provider_method": sdk_request.method},
)
response = validate_interaction_response(
    request,
    await self._context.interactions.handle(request),
)
```

共享交互类型：

- `ToolApprovalRequest`：工具、命令、文件变更等执行授权；
- `UserInputRequest`：provider 在 Turn 内追问用户；
- `McpElicitationRequest`：MCP server 请求结构化输入；
- `DynamicToolCallRequest`：provider 请求 host 执行运行期工具；
- `ProviderInteractionRequest`：带 provider、request type 和 schema version 的 namespaced JSON 请求。

每个响应必须复制 request 的 `request_id`，且类型必须与 request 配对。`deadline_at` 使用 Unix
seconds；adapter 到期后应安全地 decline/fail，并以 `DEADLINE_EXCEEDED` 取消宿主等待。当 SDK 发出
cancel、Turn abort 或连接关闭时，adapter 对仍待处理的请求调用
`await context.interactions.cancel(request_id, reason=...)`；cancel 必须可重复。`abort()` 和
`stop()` 返回前必须取消其范围内的所有 pending request，避免 provider 等待泄漏。

如果 adapter 将某个 interaction `HostCapability` 声明为 required，但 host 未提供，必须在 `start`
拒绝；optional 能力缺失时把偶发请求明确映射为 deny/decline。禁止默认 allow，也禁止把请求发成
event 后无限等待。

### interactions 与 tools 的关系

`ExternalToolGateway` 是 host 预先向 provider 暴露工具的执行入口；`DynamicToolCallRequest` 是
provider 在 active SDK control protocol 中反向委托 host 的请求。实现可以让两者最终使用同一
team tool policy，但不能跳过权限和成员可见性规则。

## 7. Hooks

`HarnessHookDispatcher` 提供 before-prompt、before-tool、after-tool 和 on-stop 生命周期策略。
例如 adapter 真正执行 team tool 前 await `before_tool`，使用 `ToolDecision` 拒绝或改写参数。

Tool decision 语义固定为：ALLOW 直接执行；DENY 拒绝；REWRITE 使用必填 `updated_arguments`；ASK
转为 `ToolApprovalRequest` 并 await interaction handler；PROVIDER_POLICY 交给明确配置的 provider
原生权限策略。ASK/PROVIDER_POLICY 都不能通过 observation event 等待回写。

`HookObservedEvent` 只能表示 hook 开始或结束。对 Claude Agent SDK 一类同时提供 callback hooks
和 hook event message 的 SDK：callback 映射到 dispatcher，event message 映射到 observation。

Provider approval 和 before-tool hook 可以串联：前者回答 SDK control request，后者执行
OpenJiuwen 统一策略。二者不要用同一个未经区分的回调类型。

## 8. Context、工具和 MCP

`ExternalHarnessContext` 由 host 在 `start()` 时提供：

- team/member/session 身份和 system prompt；
- cwd 和环境变量；
- resume policy、versioned checkpoint 和 checkpoint sink；
- native tool gateway 和 MCP server 配置；
- hook dispatcher、interaction handler 与 telemetry handle。

如果三方 SDK 接受 Python tool callback，使用 `context.tools`：

```python
definitions = await context.tools.definitions()


async def execute_tool(call_id: str, name: str, arguments: dict):
    return await context.tools.invoke(
        ToolInvocation(call_id=call_id, name=name, arguments=arguments)
    )
```

如果三方 Agent 使用 MCP，从 `context.mcp_servers` 读取配置，按 `McpTransport` 转成厂商 SDK
options。command、url、instance 必须恰好一个；stdio env 与 HTTP headers 分开处理。不要把
Claude/Codex/Jiuwen 的配置对象写回公共模型。

## 9. Checkpoint 和恢复

Checkpoint 是完整、版本化的 provider envelope：

```python
def _current_checkpoint(self) -> HarnessCheckpoint:
    self._checkpoint_sequence += 1
    return HarnessCheckpoint(
        provider="acme-code-agent",
        schema_version="2",
        member_agent_id=self._context.member_agent_id,
        team_session_id=self._context.team_session_id,
        checkpoint_id=str(uuid.uuid4()),
        sequence=self._checkpoint_sequence,
        session_id=self._session_id,
        revision=self._provider_revision,
        data={"conversation_id": self._conversation_id},
    )
```

在可恢复状态发生变化时主动保存：

```python
async def _publish_checkpoint(self, reason: CheckpointReason) -> None:
    checkpoint = self._current_checkpoint()
    self._latest_checkpoint = checkpoint
    if self._context.checkpoint_sink is not None:
        receipt = await self._context.checkpoint_sink.save(
            checkpoint,
            reason=reason,
            expected_storage_revision=self._checkpoint_storage_revision,
        )
        self._checkpoint_storage_revision = receipt.storage_revision
```

最少在以下时机考虑 save：获得 session/thread id、turn 完成、provider revision 变化、周期刷新和
provider 发出 checkpoint 通知。相同 `checkpoint_id` 用于 retry；每次新快照使用递增 sequence。
`save` 返回 `CheckpointSaveReceipt` 表示宿主持久化完成；stale/CAS 冲突会抛
`CheckpointConflictError`。实现需决定失败时重试还是让 Turn 失败，不能静默声称已持久化。

`export_checkpoint()` 返回 `_latest_checkpoint`，用于按需快照和停机兜底，但不能作为唯一保存
机制。恢复时先验证 `provider`、`member_agent_id` 和 `schema_version`：`REQUIRE_RESUME` 下缺失、
错配或不可迁移必须失败，不能悄悄创建新 session。

`data` 必须 JSON-safe，不能包含 token、完整 env、SDK client、event loop、文件句柄或任意 Python
对象，且 checkpoint data 不得超过 4 MiB。所有 JSON 字段在构造时递归复制并冻结，NaN/Infinity
立即失败；Provider 自己负责旧 schema 的兼容和迁移。

## 10. 核心实现骨架

以下省略 supervisor queue、follow-up 合并和具体 SDK 解析，但展示 P0 契约如何接线：

```python
from __future__ import annotations

import asyncio
import time
import uuid

from openjiuwen.agent_teams.external.protocol import (
    AbortMode,
    CheckpointReason,
    DeliveryMode,
    ExternalHarnessCard,
    ExternalHarnessContext,
    ExternalHarnessInput,
    ExternalHarnessProtocolError,
    ExternalHarnessStateError,
    HarnessCapability,
    HarnessCheckpoint,
    HarnessEvent,
    HostCapability,
    OutputChannel,
    OutputEvent,
    OutputKind,
    OutputOperation,
    SendReceipt,
    StateChangedEvent,
    TERMINAL_TURN_EVENT_KINDS,
    TurnError,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnResult,
    TurnStatus,
    TurnTermination,
    TurnTerminationKind,
    UnsupportedHarnessCapabilityError,
    validate_interaction_response,
)
from openjiuwen.agent_teams.harness import HarnessState


_END = object()


class AcmeHarness:
    event_buffer_config = EventBufferConfig(capacity=1024)
    card = ExternalHarnessCard(
        name="acme-code-agent",
        implementation_version="1.0.0",
        capabilities=frozenset(
            {
                HarnessCapability.STEER,
                HarnessCapability.GRACEFUL_ABORT,
                HarnessCapability.PERSISTENT_SESSION,
                HarnessCapability.CHECKPOINT,
            }
        ),
        required_host_capabilities=frozenset({HostCapability.TOOL_APPROVAL}),
    )

    def __init__(self, sdk_client):
        self._client = sdk_client
        self._context = None
        self._state = HarnessState.IDLE
        self._session_id = None
        self._latest_checkpoint = None
        self._events = asyncio.Queue()
        self._sequence = 0
        self._turn_task = None
        self._abort_requested = False
        self._active_turn_id = None
        self._checkpoint_sequence = 0
        self._checkpoint_storage_revision = None
        self._pending_interaction_ids = set()
        self._lock = asyncio.Lock()
        self._consumer_lock = asyncio.Lock()

    @property
    def state(self):
        return self._state

    @property
    def session_id(self):
        return self._session_id

    async def start(self, context: ExternalHarnessContext):
        self._context = context
        self.card.validate_host(
            protocol_version=context.protocol_version,
            capabilities=context.host_capabilities,
        )
        restored = self._validate_checkpoint(context.checkpoint)
        self._session_id = await self._client.connect(
            checkpoint=restored,
            system_prompt=context.system_prompt,
            cwd=context.cwd,
        )
        await self._transition(HarnessState.IDLE)
        await self._publish_checkpoint(CheckpointReason.SESSION_ACTIVATED)

    async def send(self, content, *, mode=DeliveryMode.AUTO):
        message_id = str(uuid.uuid4())
        async with self._lock:
            if self._state is HarnessState.RUNNING:
                if mode is not DeliveryMode.STEER:
                    raise NotImplementedError("enqueue as follow-up in production")
                await self._client.steer(content.content)
                return SendReceipt(message_id, self._active_turn_id, DeliveryMode.STEER)
            if self._state is not HarnessState.IDLE:
                raise ExternalHarnessStateError(f"cannot send while {self._state}")
            if mode is DeliveryMode.STEER:
                raise ExternalHarnessStateError("there is no active turn")
            turn_id = str(uuid.uuid4())
            self._active_turn_id = turn_id
            await self._transition(HarnessState.RUNNING)
            self._turn_task = asyncio.create_task(self._run_turn(content, message_id, turn_id))
        return SendReceipt(message_id, turn_id, mode)

    async def _run_turn(self, content: ExternalHarnessInput, message_id: str, turn_id: str):
        await self._emit(
            TurnLifecycleEvent(kind=TurnEventKind.STARTED),
            turn_id=turn_id,
            correlation_id=message_id,
        )
        try:
            final_output = None
            async for sdk_message in self._client.run(content.content):
                if self._is_server_request(sdk_message):
                    await self._handle_interaction(sdk_message, turn_id)
                elif text := self._text_delta(sdk_message):
                    final_output = text
                    await self._emit(
                        OutputEvent(
                            output_id="answer-1",
                            kind=OutputKind.TEXT,
                            content=text,
                            operation=OutputOperation.DELTA,
                            channel=OutputChannel.ANSWER,
                        ),
                        turn_id=turn_id,
                        correlation_id=message_id,
                    )
            result = TurnResult(status=TurnStatus.COMPLETED, final_output=final_output)
            terminal = TurnEventKind.FINISHED
        except Exception as exc:
            if self._abort_requested:
                result = TurnResult(
                    status=TurnStatus.INTERRUPTED,
                    termination=TurnTermination(kind=TurnTerminationKind.USER_ABORT),
                )
                terminal = TurnEventKind.ABORTED
            else:
                result = TurnResult(
                    status=TurnStatus.FAILED,
                    error=TurnError(message=self._safe_error_message(exc)),
                )
                terminal = TurnEventKind.FAILED

        await self._emit(
            TurnLifecycleEvent(kind=terminal, result=result),
            turn_id=turn_id,
            correlation_id=message_id,
        )
        await self._publish_checkpoint(CheckpointReason.TURN_COMPLETED)
        self._active_turn_id = None
        await self._transition(HarnessState.IDLE)

    async def _handle_interaction(self, sdk_request, turn_id):
        if self._context.interactions is None:
            await self._client.decline(sdk_request.id, "host interaction unavailable")
            return
        request = self._normalize_interaction(sdk_request, turn_id)
        self._pending_interaction_ids.add(request.request_id)
        try:
            response = validate_interaction_response(
                request,
                await self._context.interactions.handle(request),
            )
            await self._client.respond(sdk_request.id, response)
        finally:
            self._pending_interaction_ids.discard(request.request_id)

    async def abort(self, *, mode=AbortMode.GRACEFUL):
        if mode is AbortMode.FORCE:
            raise UnsupportedHarnessCapabilityError("force abort is not supported")
        self._abort_requested = True
        await self._cancel_pending_interactions(InteractionCancelReason.TURN_ABORTED)
        await self._client.interrupt()

    async def events(self):
        if self._consumer_lock.locked():
            raise ExternalHarnessStateError("observation stream already has a consumer")
        async with self._consumer_lock:
            while (event := await self._events.get()) is not _END:
                yield event

    async def turn_events(self, turn_id=None):
        selected_turn_id = None
        async for event in self.events():
            payload = event.event
            if selected_turn_id is None:
                if not (
                    isinstance(payload, TurnLifecycleEvent)
                    and payload.kind is TurnEventKind.STARTED
                    and (turn_id is None or event.turn_id == turn_id)
                ):
                    continue
                selected_turn_id = event.turn_id

            yield event
            if (
                event.turn_id == selected_turn_id
                and isinstance(payload, TurnLifecycleEvent)
                and payload.kind in TERMINAL_TURN_EVENT_KINDS
            ):
                return

        if selected_turn_id is not None:
            raise ExternalHarnessProtocolError(
                f"event stream closed before turn {selected_turn_id} terminated"
            )

    async def stop(self):
        if self._state is HarnessState.TERMINATED:
            return
        if self._turn_task is not None:
            await self.abort()
            await self._turn_task
        await self._cancel_pending_interactions(InteractionCancelReason.HARNESS_STOPPED)
        await self._client.close()
        await self._transition(HarnessState.TERMINATED)
        await self._events.put(_END)

    async def pause(self):
        raise UnsupportedHarnessCapabilityError("pause/resume is not supported")

    async def resume(self, *, query=None):
        raise UnsupportedHarnessCapabilityError("pause/resume is not supported")

    async def export_checkpoint(self):
        return self._latest_checkpoint

    async def _emit(self, payload, **correlation):
        self._sequence += 1
        await self._events.put(
            HarnessEvent(
                sequence=self._sequence,
                timestamp=time.time(),
                team_session_id=self._context.team_session_id,
                member_agent_id=self._context.member_agent_id,
                session_id=self._session_id,
                event=payload,
                **correlation,
            )
        )

    async def _transition(self, new_state):
        old_state, self._state = self._state, new_state
        await self._emit(StateChangedEvent(old=old_state, new=new_state))

    async def _publish_checkpoint(self, reason):
        self._checkpoint_sequence += 1
        self._latest_checkpoint = HarnessCheckpoint(
            provider=self.card.name,
            schema_version="1",
            member_agent_id=self._context.member_agent_id,
            team_session_id=self._context.team_session_id,
            checkpoint_id=str(uuid.uuid4()),
            sequence=self._checkpoint_sequence,
            session_id=self._session_id,
        )
        if self._context.checkpoint_sink is not None:
            receipt = await self._context.checkpoint_sink.save(
                self._latest_checkpoint,
                reason=reason,
                expected_storage_revision=self._checkpoint_storage_revision,
            )
            self._checkpoint_storage_revision = receipt.storage_revision

    # _validate_checkpoint/_normalize_interaction/_text_delta/
    # _cancel_pending_interactions/_is_server_request/_safe_error_message are
    # provider-specific.
```

生产实现建议用 supervisor/control queue 统一处理 `send/abort/stop`，避免锁内调用 provider SDK
造成重入，并解决完成、abort 与 stop 同时发生时的双 terminal event 竞态。示例中的 follow-up 队列、
pending interaction cancel、bounded event backpressure 和 checkpoint retry 都需要在生产代码补齐。

## 11. Provider 实现

Provider 只负责配置验证和构造未启动 Harness：

```python
class AcmeProvider:
    card = AcmeHarness.card

    def create(self, config):
        validated = AcmeConfig.model_validate(dict(config))
        return AcmeHarness(
            AcmeSdkClient(endpoint=validated.endpoint, model=validated.model)
        )
```

构造阶段不得连接网络、启动 subprocess 或绑定 event loop；这些操作放到 `start()`。

## 12. DSH 的程序化接入

仓库内 `external.dsh` 是 DeepSeek Harness Python SDK 的首个协议实现。SDK 保持 optional：本地
开发可先安装 DSH checkout 中的 Python package，公共 OpenJiuwen import 不依赖它：

```bash
uv pip install -e /path/to/deepseek-harness/python/sdk
```

当前尚无声明式 registry/spawn 接线，必须显式按 provider -> harness -> member runtime 组装：

```python
import asyncio

from openjiuwen.agent_teams.external import ExternalHarnessMemberRuntime
from openjiuwen.agent_teams.external.dsh import DshHarnessProvider
from openjiuwen.agent_teams.external.protocol import ExternalHarnessContext


async def consume_outputs(runtime: ExternalHarnessMemberRuntime) -> None:
    async for chunk in runtime.outputs():
        print(chunk)


async def main() -> None:
    provider = DshHarnessProvider()
    harness = provider.create(
        {
            "provider": "deepseek-official",
            "model": "deepseek-v4-flash",
            "cwd": "/path/to/member-worktree",
            "cordis": "/path/to/custom-cordis.yml",
            "system_prompt_env_var": "DSH_SYSTEM_PROMPT",
        }
    )
    context = ExternalHarnessContext(
        team_name="research-team",
        member_name="dsh-worker",
        member_agent_id="agent-dsh-worker",
        team_session_id="team-session-1",
        system_prompt="You are the research teammate.",
    )
    runtime = ExternalHarnessMemberRuntime(
        harness=harness,
        context=context,
        stop_on_unsupported_force_abort=True,
    )

    terminal = asyncio.Event()

    async def on_turn(kind: str, **_: object) -> None:
        if kind in {"finished", "aborted", "failed"}:
            terminal.set()

    await runtime.subscribe(on_round=on_turn)
    await runtime.start(team_session=None)
    output_task = asyncio.create_task(consume_outputs(runtime))
    receipt = await runtime.send("Inspect the repository and summarize the result.")
    print("accepted external turn:", receipt.turn_id)

    await terminal.wait()
    await runtime.stop()
    await output_task


asyncio.run(main())
```

真实 Team 装配时由宿主提供实际 `team_session` 和 `ExternalHarnessContext`；也可给 runtime 传入
context factory，在 start 时根据 team session 构造上下文。示例使用现有 legacy `on_round` callback
等待 terminal，只是内部 `StreamController` 兼容面；三方协议本身仍使用 Turn。

`stop_on_unsupported_force_abort=True` 是 MemberRuntime hard-cancel 的兼容策略：当上层请求
`abort(immediate=True)`、而 DSH 没有 FORCE_ABORT capability 时，runtime 会停止整个 Harness cycle。
它不是 DSH 的 turn force-abort，也不会让 `DshHarness.card` 声明 FORCE_ABORT。默认值为 `False`，
严格模式下这类请求抛 `UnsupportedHarnessCapabilityError`；graceful abort 仍不受该开关支持。

### 12.1 DSH Turn 边界

Adapter 串行调用 `Session.run()`。一个 OpenJiuwen Turn 从 adapter 派发已接受输入并发布 STARTED，
经过 DSH prompt durable receipt（SDK 从这里开始收集通知），直到 whole-agent idle；前一 interval 未
idle 时，后续 AUTO 输入以 FOLLOW_UP 接受并排队，队列耗尽前 Harness 状态保持 RUNNING。DSH native
turn/step 只描述内部 Agent Loop：

| DSH 数据 | 协议映射 |
|---|---|
| native `turn/start` / `turn/end` | namespaced `ProviderEvent` |
| native `step/start` / `step/end` | `item_type="iteration"` 的 `ItemLifecycleEvent` |
| assistant text/reasoning chunk | 稳定 output ID 的 `OutputEvent` DELTA |
| assistant message | FINAL output 与终态 `TurnMessage` |
| tool call/result | tool `ItemLifecycleEvent` |
| usage | `UsageUpdatedEvent` 与 terminal usage |
| subagent started/finished | subagent `ItemLifecycleEvent` |
| 未识别 notification | JSON-safe `ProviderEvent` |

external STARTED/terminal 由 adapter 每个已接受输入各发布一次，不使用 DSH native turn 作为外部
Turn 边界。whole-agent idle 时若没有任何 native `turn/end`，该外部 Turn 以
`DSH_MISSING_TURN_END` 失败；native `turn/end` error 的 message 在 terminal 和 ProviderEvent 两条
路径都会脱敏。

### 12.2 DSH 首版限制

`DshHarness.card.capabilities` 为空。当前不支持：

- STEER、graceful/force abort、pause/resume；
- checkpoint export/restore 或跨 runtime 的持久恢复；
- 将 `ExternalHarnessContext.mcp_servers` 动态安装进 DSH；
- 在 bundled Cordis 配置中自动注入 system prompt。

`system_prompt_env_var="DSH_SYSTEM_PROMPT"` 只把 system prompt 放入 runtime env。custom Cordis
composition 必须显式消费该变量（例如从 `process.env.DSH_SYSTEM_PROMPT` 取得 persona）；没有这个
消费配置时 prompt 不会生效。DSH 可在 custom Cordis 中静态装配 MCP，但这不等于协议的动态 MCP
capability。

公开的 protocol event buffer 使用正容量和 BLOCK 策略，能把 adapter 侧 backpressure 传回
notification callback。不过 DSH SDK 内部的 subscription queue 当前仍是无界队列；adapter 无法把
它改造成有界队列。因此首版只保证 OpenJiuwen 公开 protocol event buffer 有界，上游 SDK 在消费者
长期跟不上时仍可能积压，这是 DSH SDK 本身的限制。

BLOCK 也覆盖 stop 期间的 REQUIRED terminal/state event。宿主应先启动唯一的 `events()` consumer，
调用 `stop()` 时继续读取，直到 stream EOF；若先等待 stop、再读取一个已经填满的 buffer，stop 会按
背压语义等待容量。`ExternalHarnessMemberRuntime` 已内置持续 event pump。若产品要求无 consumer 时
stop 仍无条件完成，需要引入 durable event journal/sink，而不能丢 terminal、提前 close 或改用无界
内存队列。

## 13. Claude Code 和 Codex 的参考映射

| 协议语义 | Claude Agent SDK | Codex Python SDK |
|---|---|---|
| provider session | client connect + session id | thread start/resume + thread id |
| `send` | `query()` | thread turn |
| 持续 `events()` | `receive_messages()` 归一化 | thread/turn notification router |
| 单 Turn `turn_events()` | `receive_response()` 到 `ResultMessage`（含） | 到 turn terminal notification（含） |
| terminal `TurnResult` | `ResultMessage` 归一化 | turn completion/result 归一化 |
| tool approval | `can_use_tool` control request | command/file approval server request |
| hook policy | SDK hook callback | adapter/host hook interception |
| hook observation | hook event message | provider event/diagnostic |
| MCP/dynamic request | control/MCP message | server request/tool call |
| abort | `interrupt()` + cancel pending request | turn interrupt + cancel pending request |
| checkpoint | session id/state 主动 save | thread id/state 主动 save |

表中是 adapter 内部映射，不是公共协议的一部分。Provider 原始对象只允许先转成 JSON-safe
`provider_data`/`ProviderEvent`；不能把 SDK class 暴露给公共消费者。

## 14. 契约测试清单

三方项目至少覆盖：

1. 实例满足 `isinstance(harness, ExternalHarnessProtocol)`；
2. `start` 后为 IDLE，`stop` 后为 TERMINATED，重复 stop 不报错；
3. `events()` 在多个 Turn 之间不结束，stop 后正常 EOF；
4. `turn_events(receipt.turn_id)` 包含 STARTED 和 terminal，跨 PAUSED/RESUMED，terminal 后 EOF；
5. 两种流视图不能并发消费，交替调用不会重复或重排 event；
6. event sequence 跨所有 payload 严格递增；output block 可按 ID/index/channel/operation 无损重建；
7. 每轮恰好一个 STARTED 和一个状态匹配的 terminal `TurnResult`，messages 保留多 block 输出；
8. AUTO/FOLLOW_UP/STEER 在 IDLE/RUNNING 下符合定义；
9. abort、stop 和正常完成竞争时不产生两个 terminal event；
10. harness capability 与真实行为一致；required/optional host capability 和 protocol version 正确协商；
11. interaction request/response 的 id 和类型一致，deadline 生效，abort/stop cancel 全部 pending；
12. 缺 interaction handler 时不会默认授权或永久等待；
13. hook deny 阻止执行，rewrite/ask/provider-policy 路径明确，hook event consumer 不参与授权；
14. checkpoint envelope JSON round-trip、member/provider/version/id/sequence 校验和恢复失败路径；
15. session 激活和 turn 完成主动调用 sink，retry 幂等，stale/CAS 冲突不覆盖新状态；
16. event consumer 慢时背压有界，不会无限占用内存；
17. SDK 鉴权、限流、崩溃和超时落为 FAILED + `TurnError`，并且诊断不泄露凭据；
18. 未安装 Claude/Codex/DSH 等可选 SDK 时，protocol 包仍可导入。
19. MCP command/url/instance 缺失、错配或多填时都在启动 provider 前失败。
20. cursor 提前 `aclose()` 后释放 lease，后续 consumer 可继续；
21. JSON 字段无可变别名，NaN/Infinity/object/超大 checkpoint 被拒绝；
22. event codec JSON round-trip，未知 type/version decode/re-encode 不丢 payload；
23. required event 在背压下不丢，只有 derived coalescible/best-effort 类别按 policy 处理；
24. ID scope、correlation/causation 与 Unix/monotonic 时间语义符合 spec。

## 15. 当前限制与后续接线

当前已有通用 `ExternalHarnessMemberRuntime` 和程序化 DSH adapter，但仍没有：

- 修改 `ExternalCliAgentSpec`；
- 注册 Python entry point group；
- 改造 `build_cli_runtime`；
- 迁移 Claude Code/Codex runtime。

这些接线完成前，协议实现不能仅靠 TeamAgentSpec/YAML 声明自动成为 team member，需要像 DSH
示例一样程序化构造 provider、harness、context 和 runtime。请把其它 provider 与 harness 实现放在
独立、可测试的模块中，避免依赖当前 CLI spawn 内部结构，以便后续直接接入 registry。
