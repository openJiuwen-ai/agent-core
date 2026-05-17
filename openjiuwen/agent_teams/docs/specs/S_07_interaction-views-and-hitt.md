# Interaction Views and HITT Runtime Surface

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/interaction/`、`openjiuwen/agent_teams/constants.py`、`openjiuwen/agent_teams/runtime/manager.py`（`_dispatch_payload`）、`openjiuwen/agent_teams/agent/coordination/handlers/message.py`（HITT inbound 钩子）|
| 最近一次修订日期 | 2026-05-15 |
| 关联 feature | F_13_human-agent-send-message.md |

## 范围 / 边界

本规约管 `agent_teams.interaction` 这一层：把"团队外部进来的所有声音"压成三种结构化视角（`GodViewMessage` / `OperatorMessage` / `HumanAgentMessage`），用 `UserInbox` / `HumanAgentInbox` 两条入站通道把它们落到团队内部状态（leader DeepAgent 的输入队列 或 团队消息总线 `TeamMessageManager`），并提供 HITT（Human in the Team）相关的可见运行时表面。

具体覆盖：

- 三视角 payload 结构（`payload.py`）。
- 字符串入口 `parse_interact_str` 与底层原语 `parse_mention`（`router.py`）。`@<member>` / `# ` / `$<name>` / `@all` / `@*` 五个前缀的语义都在这里收敛。
- `UserInbox` 三个入口：`deliver_to_leader` / `direct` / `broadcast`。
- `HumanAgentInbox.send`，含 sender 解析、avatar 驱动、广播、点对点四条分支。
- 保留名 `user` / `team_leader` / `human_agent`（`constants.py`）。
- 运行时 HITT 表面：分层开关、人类成员来源、一致性约束、运行约束。
- `HumanAgentInboundEvent` 通知钩子（`on_inbound`），也就是 team→user 反向通道的契约面。

不管：

- mention 字符串具体长什么样的版本演进、是否要支持 ``$<name> @all body``、广播是否要强类型化——这是 `router.py` 内部细节。
- `TeamMessageManager` 的总线落库逻辑（属于 `tools/`）。
- coordination 层把消息总线事件变成 wake-up 的 `EventMessage` 再分发给 handler 的部分（属于 `agent/coordination/`）。
- HITT prompt section 的具体文案（属于 `prompts/`）和 HITT 工具白名单（属于 `tools/`）。

边界线说一遍：interaction 层只做"形状转换 + 落到正确通道"，不做唤醒、不做调度、不做 LLM 决策。dispatcher / handler 永远拿到的是已经分类完成的 `InteractPayload` 或者已经写进总线的总线事件，绝不会自己再去 regex 一次原始字符串。

## 不变量

下面这些事实在任意时刻必须为真。任何一条破坏，要么是 bug，要么是规约改了忘同步。

1. **Mention/前缀解析有且只有一处**：`interaction/router.py:parse_interact_str` 是 str→`InteractPayload` 的唯一入口；`parse_mention` 是底层原语，只用来抽 `@target body`。dispatcher、handler、CLI、TeamAgent 任何位置都不允许再写一份 `@\S+` regex 或 `body.startswith("# ")` 判断。
2. **保留名集中声明**：`user` / `team_leader` / `human_agent` 在 `constants.RESERVED_MEMBER_NAMES` 集合定义。新增保留名只改这一个文件；所有 inbox / spec 校验路径都从这里读。
3. **保留名不可被普通成员注册**：`TeamAgentSpec.build()` 在 `_validate_reserved_names` 拒绝任何 `predefined_members[*]` 撞 `RESERVED_MEMBER_NAMES`。`HUMAN_AGENT` 角色用 `human_agent` 这个名是框架自己注入的特例，不走用户声明路径。
4. **`InteractPayload` 是 dispatcher 的唯一入参形状**：`runtime/manager.py:_dispatch_payload` 只 isinstance `GodViewMessage` / `OperatorMessage` / `HumanAgentMessage` 三种。新增视角 = 新增 dataclass + 在 `_dispatch_payload` 加一支；不允许 `dict` / `**kwargs` 形式漏过去。
5. **三视角到通道的映射唯一**：
   - `GodViewMessage` → `UserInbox.deliver_to_leader`，落到 leader DeepAgent 的 `deliver_input`，**不**写消息总线；
   - `OperatorMessage(target=None)` → `UserInbox.broadcast`；
   - `OperatorMessage(target=<name>)` → `UserInbox.direct`；
   - `HumanAgentMessage(target=None)` → `HumanAgentInbox._drive_agent`（驱动该 avatar 的 DeepAgent，不写总线）；
   - `HumanAgentMessage(target in {"all", "*"})` → `HumanAgentInbox` 经 `broadcast_message`；
   - `HumanAgentMessage(target=<name>)` → `HumanAgentInbox` 经 `deliver_direct`。
   不存在第二张映射表。
6. **入站通道总带 sender**：经 `UserInbox` 写入的总线消息一律 `from_member_name="user"`；经 `HumanAgentInbox` 写入的总线消息 `from_member_name=<已校验的 human-agent member 名>`。`from_member_name` 不允许为 `None`、不允许从 body 文本反猜。
7. **HITT 启用是分层 AND**：`TeamAgentSpec.enable_hitt`（spec 层 capability ceiling）与 `build_team(enable_hitt=...)`（运行时实例 override）必须**两层都允许**，团队才有人类成员；spec=False 时任何运行时强行打开都被 fail-fast 拒绝。
8. **HITT 关时 inbox 直接拒**：`HumanAgentInbox.send` 在 `team.human_agent_names()` 为空时抛 `HumanAgentNotEnabledError`（`_dispatch_payload` 转 `DeliverResult.failure("human_agent_not_enabled")`）；HITT 开但 sender 不匹配抛 `UnknownHumanAgentError`，转 `DeliverResult.failure("unknown_human_agent")`。绝不允许 silent drop 或者 silent inject 一个新身份。
9. **保留名 `human_agent` 命中语义**：当 sender 省略时，`HumanAgentInbox._resolve_sender` 用 `HUMAN_AGENT_MEMBER_NAME` 作为优先默认；找不到则取 `sorted(names)[0]`。这是 backward-compat 兜底，不是 routing 含义。
10. **`HumanAgentInboundEvent` 是 team→user 反向通道的唯一类型**：人类成员收到的消息（点对点 / 广播）由 `MessageHandler.on_message_or_broadcast` 把消息总线 row 包成 `HumanAgentInboundEvent`，喂给注册在 `TeamBackend` 上的 `on_inbound` 回调。**人类成员的 LLM 不消费这些消息**——按 phase-2 设计，业务层（CLI / SDK / IM 适配器）拿到事件再决定怎么投给真实用户。
11. **interaction 层不感知 wake-up**：`UserInbox` / `HumanAgentInbox` 只调 `TeamMessageManager.send_message` / `broadcast_message` 与 `TeamAgent.deliver_input`。它们既不写 `EventBus`，也不阅读 `MemberStatus`。从消息总线到 dispatcher 的 wake-up 是 `messager` + coordination 的事，跟这一层无关。
12. **错误以 `DeliverResult` 为准，不抛业务态异常**：`UserInbox` / `HumanAgentInbox` 对外 contract 是同一个 `DeliverResult(ok, message_id, reason)`。HITT 相关的 `HumanAgentNotEnabledError` / `UnknownHumanAgentError` 是 inbox 内部状态信号，由 `_dispatch_payload` 在边界吃掉转换成 `failure(reason)`。SDK 用户**不需要** try/except 一个具体异常类。

## 接口契约

### 三视角 Payload（`interaction/payload.py`）

均 `@dataclass(frozen=True, slots=True)`，没有方法、没有继承。

```python
class GodViewMessage:
    body: str

class OperatorMessage:
    body: str
    target: Optional[str] = None  # None = team-wide broadcast

class HumanAgentMessage:
    body: str
    sender: str
    target: Optional[str] = None  # None = drive avatar; "all"/"*" = broadcast

InteractPayload = Union[GodViewMessage, OperatorMessage, HumanAgentMessage]
```

触发场景：

| Payload | 触发场景 |
|---|---|
| `GodViewMessage` | SDK / CLI 想"以神视角"对 leader DeepAgent 直接说话；旧 `TeamAgent.invoke` 等价物。`parse_interact_str` 在没识别出任何前缀时也兜底产此类型 |
| `OperatorMessage` | 外部用户以"team operator"身份对团队成员/全队下达指令；`@member body` / `@all body` |
| `HumanAgentMessage` | 真人扮演已注册的 `human_agent` 成员发声；`$<name> body` / `$<name> @<m> body` / `$<name> @all body`。`sender` 必须是已注册 human-agent member |

### `DeliverResult`

```python
@dataclass(frozen=True, slots=True)
class DeliverResult:
    ok: bool
    message_id: Optional[str] = None
    reason: Optional[str] = None

    @classmethod
    def success(cls, message_id: Optional[str] = None) -> "DeliverResult": ...
    @classmethod
    def failure(cls, reason: str) -> "DeliverResult": ...
```

`reason` 是稳定 token（snake_case，可带 `:<arg>` 后缀），用于 SDK 区分错因。当前已用：`send_failed:<target>` / `broadcast_failed` / `unknown_member:<target>` / `agent_unavailable` / `deliver_to_leader_failed:<exc>` / `human_agent_not_enabled` / `unknown_human_agent` / `not_active` / `gate_closed` / `no_team_backend` / `unknown_payload:<cls>`。新增 reason 必须 grep 现有用法保唯一，且仅由 interaction 或 runtime 层产出。

### `parse_mention`（router.py）

```python
def parse_mention(content: str) -> tuple[str, str] | None
```

唯一职责：把 `@<target> <body>`（必须有 trailing 空格）拆成 `(target, body)`；不匹配返回 `None`。底层原语，不做多收件人 fan-out，也不识别 `# ` / `$` 前缀。**只允许 router.py 自己用，dispatcher / handler 一律走 `parse_interact_str`**。

### `is_reserved_name`（router.py）

```python
def is_reserved_name(name: str) -> bool
```

字面 wraps `name in RESERVED_MEMBER_NAMES`。任何"是否保留名"的判断都走这个函数，禁止散落 `name == "user"` 之类硬编码。

### `parse_interact_str`（router.py）

```python
def parse_interact_str(body: str) -> list[InteractPayload]
```

把 `Runner.interact_agent_team(payload, ...)` 里的 bare `str` 翻成结构化 payload 列表。语法（出自模块 docstring）：

```
input := channel? recipients? body
channel := "# " | "$" name " "    # default "# " when omitted
recipients := ("@" name " ")*
body := remaining text
```

输出契约：

| 输入形状 | 输出 |
|---|---|
| 空 / 全空白 | `[]` |
| 任意有内容字符串无识别前缀 | `[GodViewMessage(body=<原文>)]` |
| `# body` | `[GodViewMessage(body)]` |
| `# @m1 @m2 body` | `[OperatorMessage(body, target="m1"), OperatorMessage(body, target="m2")]` |
| `# @all body` 或 `# @* body` | `[OperatorMessage(body, target=None)]`（broadcast 吞掉同时列出的具名收件人）|
| `$<name> body` | `[HumanAgentMessage(body, sender=<name>, target=None)]` |
| `$<name> @m body` | `[HumanAgentMessage(body, sender=<name>, target="m")]` |
| `$<name> @all body` / `$<name> @* body` | `[HumanAgentMessage(body, sender=<name>, target="*")]` |

行为铁律：

- broadcast 和具名收件人共存时，broadcast 吞掉所有具名（已经覆盖了）。
- 没有 trailing 空格的 `#hashtag` / `$variable` 是正文，不是 channel。
- 收件人存在性**不**在这里校验——syntax-only。具体校验在落地点（`UserInbox.direct` / `HumanAgentInbox`）做。

### 多意图入口前缀约定

调用方在 inbox 抽象上不要做"既是 a 又是 b"的隐式 dual-use。三套前缀（`# ` / `$ ` / `@ `）的契约：

- 想以神视角说话 → 显式 `# body` 或裸 `body`（默认走 GodView，但要求调用代码注释里写明这是默认路径，不要假设别的语义）。
- 想以 user 身份做点对点 / 广播 → `# @<m> body` / `# @all body`。"无前缀的 `@<m> body`"在历史上曾经被 dispatcher 解析过，**已下线**——必须显式声明 channel `# `。
- 想以人类成员身份说话 → `$<sender> body`，channel 名等于 sender 名，不存在"猜 sender"的路径。

写新入口时凡是文本可能落进 `Runner.interact_agent_team(str, ...)` 的，永远走 `parse_interact_str`，不要写新的解析分支。

### `UserInbox`（user_inbox.py）

```python
class UserInbox:
    def __init__(self, message_manager: TeamMessageManager): ...

    async def direct(self, target: str, body: str) -> DeliverResult: ...
    async def broadcast(self, body: str) -> DeliverResult: ...

    @staticmethod
    async def deliver_to_leader(
        deliver_input: Callable[[str], Awaitable[None]],
        body: str,
    ) -> DeliverResult: ...
```

支持的入站消息类型：`OperatorMessage`（`broadcast` / `direct`）+ `GodViewMessage`（`deliver_to_leader`）。`from_member_name` 一律 `USER_PSEUDO_MEMBER_NAME`（"user"）。`deliver_to_leader` 静态方法接 callable，避免 `TeamAgent` ↔ `UserInbox` 互相 import；`message_id` 永远 `None`。

错误语义：bus 拒收返 `failure("send_failed:<target>")` / `failure("broadcast_failed")`；leader 路径吞 `Exception` 转 `failure("deliver_to_leader_failed:<exc>")`。

### `HumanAgentInbox`（human_agent_inbox.py）

```python
class HumanAgentInbox:
    def __init__(
        self,
        team: TeamBackend,
        message_manager: TeamMessageManager,
        *,
        agent_lookup: Optional[Callable[[str], Optional[TeamAgent]]] = None,
        on_inbound: Optional[Callable[[HumanAgentInboundEvent], Awaitable[None]]] = None,
    ): ...

    @property
    def on_inbound(self) -> Optional[OnInbound]: ...

    async def send(
        self,
        body: str,
        to: Optional[str] = None,
        *,
        sender: Optional[str] = None,
    ) -> DeliverResult: ...
```

支持的入站消息类型：`HumanAgentMessage`。三条分支严格按 `to`：

- `to is None` → `_drive_agent`：用 `agent_lookup(sender)` 找 live `TeamAgent` 后调 `agent.deliver_input(body)`。`agent_lookup is None` 或返回 `None` 时 `failure("agent_unavailable")`。
- `to in BROADCAST_TARGETS`（`{"all", "*"}`）→ `message_manager.broadcast_message(content=body, from_member_name=sender)`。
- `to=<member>` → 复用 router 层的 `deliver_direct` 原语，先 `_member_exists` 再写总线。

`sender` 解析（`_resolve_sender`）：

1. `team.human_agent_names()` 空 → 抛 `HumanAgentNotEnabledError`。
2. `sender is None` → 优先 `HUMAN_AGENT_MEMBER_NAME`（保留名兜底），否则 `sorted(names)[0]`。
3. `sender not in names` → 抛 `UnknownHumanAgentError`。

`on_inbound` 不在 inbox 里被调用——构造参数在这里仅为 future-proofing；当前由 `TeamBackend.register_human_agent_inbound(member_name, callback)` 注册，由 `MessageHandler.on_message_or_broadcast` 在收到消息总线事件时拼出 `HumanAgentInboundEvent` 后调用。

### `HumanAgentInboundEvent`

```python
@dataclass(frozen=True, slots=True)
class HumanAgentInboundEvent:
    member_name: str
    sender: str
    body: str
    broadcast: bool
    message_id: str
    timestamp: int
```

唯一用途：`MessageHandler` 在 leader 进程把消息总线 row 翻成结构化事件，回调给 SDK / CLI / IM 适配器。`member_name` 是收件人（被代理的人类成员名）；`sender` 可能是普通 teammate 名，也可能是 `USER_PSEUDO_MEMBER_NAME` 表示来自 user-side broadcast。`message_id` / `timestamp` 直接来自 db row，便于去重和 read-state 对齐。

### 运行时入口契约（`runtime/manager.py:_dispatch_payload`）

interaction 层不直接被 SDK 用户调用。SDK 用户用 `Runner.interact_agent_team(payload, *, team_name, session_id)`，由 `TeamRuntimeManager.interact` 在 `InteractGate.admit` 里把 `InteractPayload | str` 喂给 `_dispatch_payload`：

```python
@staticmethod
async def _dispatch_payload(agent: TeamAgent, payload: InteractPayload) -> DeliverResult:
    backend = agent.team_backend
    if backend is None and not isinstance(payload, GodViewMessage):
        return DeliverResult.failure("no_team_backend")

    if isinstance(payload, GodViewMessage):
        return await UserInbox.deliver_to_leader(agent.deliver_input, payload.body)
    if isinstance(payload, OperatorMessage):
        inbox = UserInbox(backend.message_manager)
        if payload.target is None:
            return await inbox.broadcast(payload.body)
        return await inbox.direct(payload.target, payload.body)
    if isinstance(payload, HumanAgentMessage):
        try:
            inbox = HumanAgentInbox(
                backend, backend.message_manager,
                agent_lookup=agent.lookup_human_agent_runtime,
            )
            return await inbox.send(payload.body, to=payload.target, sender=payload.sender)
        except HumanAgentNotEnabledError:
            return DeliverResult.failure("human_agent_not_enabled")
        except UnknownHumanAgentError:
            return DeliverResult.failure("unknown_human_agent")
    return DeliverResult.failure(f"unknown_payload:{type(payload).__name__}")
```

字符串入口 `manager.interact(payload, ...)` 调 `parse_interact_str(payload)`：空 list 兜底 `[GodViewMessage(body=<原文>)]`；多 payload fan-out 在同一张 `AdmissionTicket` 下顺序执行，遇到第一个 `ok=False` 短路返回。

`TeamAgent.broadcast(content)` / `TeamAgent.human_agent_say(content, to=, sender=)` 是给 leader 进程内代码使用的便利方法，本质都是构造 inbox 直接调一次——同样产 `DeliverResult`。

### 三视角与 `EventMessage` 的关系

interaction 层**不**直接产 `EventMessage`。具体路径分两段：

1. `_dispatch_payload` 把 payload 分到 inbox。`UserInbox.broadcast` / `UserInbox.direct` / `HumanAgentInbox._send_*` 都调 `TeamMessageManager`，由 `message_manager` 写消息总线后通过 `messager.publish` 在 `TeamTopic.MESSAGE.build(session_id, team_name)` 上发 `EventMessage(MESSAGE)` / `EventMessage(BROADCAST)`。
2. 这个 `EventMessage` 被 `EventBus` 收下进 `EventDispatcher`，由 `MessageHandler.on_message_or_broadcast` 处理：(a) leader 端把面向 `user` 的消息变成 user-bound ack；(b) 命中人类成员的消息包成 `HumanAgentInboundEvent` 给 `on_inbound` 回调（团队→外部用户的反向通道）。

`GodViewMessage` 与 `HumanAgentMessage(target=None)` 不写消息总线，所以也不会产生 `EventMessage`——它们直接通过 `TeamAgent.deliver_input` 进对应 DeepAgent 的输入队列，由 coordination 层的 `USER_INPUT` 事件路径唤醒。换句话说，从入口角度看，三视角→后续运行时存在两条并行路径：**总线路径**（Operator + HumanAgent 写总线 → MESSAGE 事件）和 **DeepAgent 输入路径**（GodView + HumanAgent 驱 avatar 走 deliver_input → USER_INPUT 事件），interaction 层负责选哪一条。

## 数据结构

### 保留名（`constants.py`）

```python
HUMAN_AGENT_MEMBER_NAME: str   = "human_agent"
USER_PSEUDO_MEMBER_NAME: str   = "user"
DEFAULT_LEADER_MEMBER_NAME: str = "team_leader"

RESERVED_MEMBER_NAMES: frozenset[str] = frozenset({
    HUMAN_AGENT_MEMBER_NAME,
    USER_PSEUDO_MEMBER_NAME,
    DEFAULT_LEADER_MEMBER_NAME,
})
```

生命周期：进程启动时即冻结。spec 校验、router 校验、message_manager `from_member_name` 比对全部读这一处。

| 名字 | 语义 | 由谁创建 |
|---|---|---|
| `team_leader` | leader 成员的默认 `member_name`，可被 spec 显式覆盖 | spec 路径默认 |
| `user` | 伪成员（pseudo-member），代表外部调用者；任何 `UserInbox` 写入的 bus 消息 `from_member_name="user"` | runtime 隐式 |
| `human_agent` | HITT 默认人类成员名；动态 `spawn_member(role_type='human_agent')` 不传 `member_name` 时使用 | runtime 在 enable_hitt=True 时按需注入 |

### HITT 的开关与人类成员来源

| 表面 | 类型 | 含义 |
|---|---|---|
| `TeamAgentSpec.enable_hitt` | `bool` | 能力天花板。`False` 时所有 human-agent 创建路径被 `TeamAgentSpec.build()` fail-fast 拒绝；`True` 才允许 |
| `build_team(enable_hitt=...)` | `Optional[bool]` | 运行时实例 override：`None` 继承 spec；`True` 显式启用（要求 spec=True，否则报错）；`False` 显式禁用（即使 spec=True 也覆盖，跳过预配的 HUMAN_AGENT 并 warning）|
| `TeamAgentSpec.predefined_members[*].role_type=HUMAN_AGENT` | 静态声明 | 团队启动即注入的人类成员；自定 `member_name` 可多人。框架不再隐式注入默认 `human_agent` |
| `leader.spawn_member(role_type='human_agent', member_name=, display_name=, desc=)` | 动态声明 | 已建团后由 leader 拉新人类成员加入；禁止传 `model_name` / `prompt`，由内置模板托管 |
| `team.human_agent_names() -> set[str]` | 运行时查询 | inbox 用来判 `enable_hitt` 实际是否生效（roster 里有人） |
| `backend.hitt_enabled() -> bool` | 运行时查询 | TeamPolicyRail 用来决定是否注入 `team_hitt` section |
| `TeamBackend.register_human_agent_inbound(member_name, callback)` | 注册接口 | SDK 通过 `TeamRuntimeManager.register_human_agent_inbound` 把 team→user 通知回调挂上来；callback 形如 `(HumanAgentInboundEvent) -> Awaitable[None] \| None` |

### 一致性约束（`TeamAgentSpec.build()` 时 fail-fast）

| 条件 | 结果 |
|---|---|
| `enable_hitt=False` 且 `predefined_members` 含 `HUMAN_AGENT` 角色 | `AGENT_TEAM_CONFIG_INVALID`（特性禁了但预配了人）|
| `enable_hitt=True` 且 `predefined_members` 无 `HUMAN_AGENT` | 允许（动态 spawn 路径）|
| `predefined_members[*].member_name in RESERVED_MEMBER_NAMES` 且不是 `HUMAN_AGENT` | `AGENT_TEAM_CONFIG_INVALID` |

### 运行约束（代码层 + Prompt 层双重保证）

1. `human_agent` 是保留成员名，作动态 spawn 的默认人类成员名；自定 HUMAN_AGENT 成员名可避开此保留名。普通 teammate 的 predefined 成员仍然不允许撞保留名（`_validate_reserved_names`）。
2. human-agent 走标准 `UNSTARTED → spawn` 流程（与 teammate 一致），工具集为 `HUMAN_AGENT_TOOLS` = `view_task` + `member_complete_task` + `send_message`；rail 装配会剥离 `FirstIterationGate` / `TeamToolApprovalRail`。其中 `send_message` 是**用户驱动的转发通道**：能否调用、给谁、说什么完全由用户在 Inbox 输入里的明确指令决定。约束写在 `team_hitt` prompt section 里（不在 `invoke()` 里加 caller-role 分支）——选择 prompt 而非代码强约束是因为「该不该转发」是语义判断，最适合让 LLM 在 prompt 引导下判断；如果未来发现 LLM 越权滥用，再加 tool-level 静态护栏（如 multicast/broadcast 拒收）。
3. 一旦 `task.assignee` 指向某个 human-agent 且状态 CLAIMED，`UpdateTaskTool` 拒绝 reassign 和 cancel；批量 cancel 链路也跳过。
4. 发送给 human-agent 的点对点消息 `is_read=True`；广播后 human-agent 的 `read_at` 立即跟进。
5. `TeamPolicyRail` 注入 `team_hitt` section（priority=12），按 role 给 leader / teammate / human_agent 下达角色特定的行为约束。section 注入条件来自 `backend.hitt_enabled()`——反映运行时 effective flag，不依赖 roster 是否已 spawn。
6. `_resolve_team_mode` 只把**非 HUMAN_AGENT** 的 predefined member 计入 `hybrid` 派生——所以纯 HITT 团队（仅声明人类成员）仍然是 `default` 模式，leader 保留 `spawn_member` 工具。

## 与其它 spec 的关系

- **runtime 子系统（`S_06_runtime-pool-dispatch`）**：`TeamRuntimeManager.interact / register_human_agent_inbound / pause_team / stop_team / release_session / delete_team` 以本规约定义的 payload / inbox / `DeliverResult` 为契约。`_dispatch_payload` 依赖三视角与 inbox 的映射不变；`InteractGate` 不感知具体 channel，只控制并发。新视角加进来时必须在 `_dispatch_payload` 显式补一支，runtime 不会自动 fallback。
- **coordination 子系统（`S_03_coordination-protocol`）**：dispatcher / handler 永远拿到的是 `EventMessage` 或 `InnerEventMessage`，不是 `InteractPayload`。本规约的不变量 1（解析唯一性）和不变量 11（interaction 不感知 wake-up）配合 coordination 的铁律 1（不做决策）共同保证 mention regex 不会再次出现在 dispatcher。`MessageHandler.on_message_or_broadcast` 是消息总线 → `HumanAgentInboundEvent` 的唯一桥梁。
- **prompts 子系统（`S_09_prompts-and-rails`）**：`team_hitt` section 的内容由 prompts 维护，本规约仅约束注入条件（`backend.hitt_enabled()`）和 priority。
- **tools 子系统（`S_08_team-tools-contract`）**：`HUMAN_AGENT_TOOLS` 白名单（`view_task` + `member_complete_task` + `send_message`）由 tools 定义；本规约仅约束 human-agent rail 装配剥离 `FirstIterationGate` / `TeamToolApprovalRail` 这条不变量，以及 `send_message` 在 human-agent 角色下「用户驱动转发」的语义约束（由 prompt 强制）。
- **schema 层（`S_12_schema-data-models`）**：`TeamRole.HUMAN_AGENT`、`TeamMemberSpec.role_type` 是本规约依赖的输入；本规约不在 schema 里加字段，新增视角应在 `interaction/payload.py` 加 dataclass，不要污染 schema。
- **CLI 子系统（`S_15_cli-tui`）**：`run_team_cli` 文本入口透传给 `Runner.interact_agent_team`，唯一一次解析在 `parse_interact_str`；CLI 不做二次解析、不读 mention regex。`HumanAgentInboundEvent` 的反向通道由 CLI `inbox_sink` 渲染。
