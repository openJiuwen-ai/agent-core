# Bridge Agent — 桥接外部独立 Agent

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-13 |
| 涉及 commit | `(待补)` |
| 范围 | `openjiuwen/agent_teams/{interaction,schema,agent,tools,prompts,rails}`；`tests/unit_tests/agent_teams/{schema,interaction,agent,tools,prompts}` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/` → 977 passed, 16 skipped |
| Refs | `#751` |

## 背景

需要让 jiuwen team 中的一个成员"持有"一个 jiuwen 之外的独立 agent（claudecode / codex / openclaw / hermes 等）作为实际工作产出方。已存在的 HITT 是把"真实人类"当 avatar 嵌入团队的解法；缺一个把"另一个独立 agent"当成员嵌入团队的解法。Bridge Agent 补这一条。

**关键边界**（来自反复对齐的需求澄清）：

- **双方独立实体**：jiuwen team 不认识三方 agent；三方只了解 bridge agent 自己的 persona + 团队上下文（作为参考）。
- **纯文本协议**：bridge agent 与远程之间仅交换文本，不传 model 接口对象（messages / tools / tool_call）。
- **bridge 是调度型 teammate**：本地 LLM 做**调度**决策（认领/完成任务、何时回应、给谁），不做内容创造。完整 teammate 工具集、按 teammate 走 mention / 任务 / mailbox。
- **远程是实际执行者，bridge 只做调度不做执行**：所有具体工作产出（代码、分析、回答内容）由远程完成；bridge 把远程的结果**原样**传达给团队，禁止改写或综合。
- **mailbox 自动转发**：框架在把团队消息 deliver 给 bridge avatar 前同步调协议把消息送给远程，拿到纯文本回复，**把"原消息 + 远程执行结果"组合后**注入 bridge avatar context；bridge avatar 决定调度动作后把远程结果原样回传。
- **通信只走 mailbox 自动转发这一条通道**：bridge avatar 本身无任何"咨询远程"工具；远程接入只发生在团队消息进入 bridge 的那一刻，由框架自动完成。
- **mailbox_inject_mode**：创建时确定 `PASSTHROUGH` / `REPHRASE`，控制**送给远程**的消息形态（不是控制进 bridge context 的形态）。
- **协议适配器留扩展**：本期只定义 `BridgeProtocolAdapter` Protocol（connect / relay / close），不实现任何具体 adapter（A2A / ACP / claudecode CLI 等）。
- **降级**：adapter 缺失时 bridge agent 仍能工作，自动转发用 `REMOTE_UNAVAILABLE_SENTINEL` 占位，bridge 退化为普通 teammate。

## 端到端使用

### 1. 启用 Bridge

```python
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.team import (
    BridgeMailboxInjectMode,
    BridgeMemberSpec,
)

spec = TeamAgentSpec(
    agents={"leader": DeepAgentSpec(...)},
    team_name="demo",
    enable_bridge=True,                        # ① spec 层能力总开关
    predefined_members=[                       # ② 声明 bridge 成员
        BridgeMemberSpec(
            member_name="codex",
            display_name="Codex Bridge",
            persona="senior python reviewer",  # ③ 同时作为远程的 connect 简介
            mailbox_inject_mode=BridgeMailboxInjectMode.PASSTHROUGH,
            protocol="codex",                  # ④ 适配器标识（Phase-1 仅 metadata）
            adapter_config={"endpoint": "stdio://codex"},
        ),
    ],
)
```

- `enable_bridge=True` 是 spec 层 capability ceiling，与 `enable_hitt` 平行。spec 关时运行时任何强行打开都被 fail-fast 拒绝。
- 也可以不预声明，运行时由 leader 通过 `spawn_member(role_type='bridge_agent', ...)` 动态创建（与 HITT 的动态 spawn 路径对齐）。

### 2. 注入协议适配器（SDK 侧）

```python
class MyAdapter:  # 满足 BridgeProtocolAdapter 结构
    async def connect(self, *, member_name, adapter_config,
                      bridge_persona, team_overview): ...
    async def relay(self, *, member_name, text) -> str: ...
    async def close(self): ...

backend.set_bridge_adapter("codex", MyAdapter())
```

未注入 adapter 时所有 relay 走 `REMOTE_UNAVAILABLE_SENTINEL` 路径，bridge 行为退化为普通 teammate（其 LLM 看到 sentinel 文本，可以自然决策"继续等待"或"作为 teammate 完成工作"）。

### 3. 远程视角

远程 agent 通过 `adapter.connect(bridge_persona=..., team_overview=...)` 拿到：
- **bridge_persona**：包含本 bridge member 的 persona + "你是实际执行者，回复会被原样转交给团队，请直接给最终结果"等约束；
- **team_overview**：团队名 + 成员摘要（name / role / 简短 persona），用于参考；
- 之后每次 `adapter.relay(text=...)` 一段文本进、一段文本出。远程**永远拿不到** jiuwen 的 system prompt、tool schema、ToolCall 对象。

## 行为规则与实现细节

### 入站消息处理（_bridge_deliverable_for）

`MessageHandler._process_unread_messages` 路径上：当 recipient 的 role 是 `BRIDGE_AGENT` 时调 `_bridge_deliverable_for` 替换 `_format_message`，流程：

1. 从 backend 取 `BridgeMemberSpec.mailbox_inject_mode`；
2. 用 `wrap_outbound_to_remote(...)` 拼 outbound 文本（PASSTHROUGH = 极简头 + body；REPHRASE = 完整 sender 上下文 + body + 可选 task_hint）；
3. 调 `adapter.relay(text=...)` 拿远程 reply；adapter 缺失或异常时用 `REMOTE_UNAVAILABLE_SENTINEL`；
4. 用 `compose_bridge_inbound(...)` 把 `[原消息]` + `[远程回复]` + 调度契约文案组合成最终 deliverable；
5. 走标准 teammate `deliver_input` 路径进 avatar 的 DeepAgent context。

整条链路是同步等待远程的（默认 30s 由 `adapter_config["relay_timeout_s"]` 配，由适配器自行实现超时），简化模型；后续可改异步。

### 工具集

`create_team_tools(role="bridge_agent", ...)` 直接走 `MEMBER_TOOLS`（与 teammate 完全一致）。**无任何 bridge 专属工具**——通信只通过 mailbox 自动转发，bridge avatar 不感知 adapter。

### Prompts

`build_team_bridge_section` 三视角，priority=12（与 HITT 同位）：

- LEADER / TEAMMATE 视角：bridge 是普通 teammate；
- BRIDGE_AGENT 视角：你是调度员；外部执行者会被自动调用；不要改写或综合远程输出；遇到 `REMOTE_UNAVAILABLE_SENTINEL` 时退化为 teammate 行为。

### Spawn 入口

`SpawnMemberTool` 加 `role_type='bridge_agent'` 分支：
- `bridge_enabled()=False` 时返回 `ToolOutput.failure`；
- `desc`（= persona）必填（远程接 connect 需要）；
- 可选 `mailbox_inject_mode` / `protocol` / `adapter_config` / `model_name`。

### 一致性约束（`TeamAgentSpec.build()` 时 fail-fast）

- `enable_bridge=False` 且 `predefined_members` 含 `BRIDGE_AGENT` → `AGENT_TEAM_CONFIG_INVALID`；
- `enable_bridge=True` 且无 `BRIDGE_AGENT` predefined → 允许（动态 spawn 路径）。
- `build_team(enable_bridge=True)` 不能超 spec ceiling，否则 fail-fast。

### `_resolve_team_mode`

`HUMAN_AGENT` 与 `BRIDGE_AGENT` 都是 avatar-roster 类声明，不应让 leader 失去 `spawn_member` 工具。`_resolve_team_mode` 只把"非 avatar predefined"计入 predefined 派生。

## 接口契约

```python
# schema/team.py
class BridgeMailboxInjectMode(str, Enum):
    PASSTHROUGH = "passthrough"
    REPHRASE = "rephrase"

class BridgeMemberSpec(TeamMemberSpec):
    role_type: Literal[TeamRole.BRIDGE_AGENT] = TeamRole.BRIDGE_AGENT
    mailbox_inject_mode: BridgeMailboxInjectMode = BridgeMailboxInjectMode.PASSTHROUGH
    protocol: str = ""
    adapter_config: dict[str, Any] = Field(default_factory=dict)

# interaction/bridge_protocol.py
REMOTE_UNAVAILABLE_SENTINEL: str

@runtime_checkable
class BridgeProtocolAdapter(Protocol):
    async def connect(self, *, member_name, adapter_config,
                      bridge_persona, team_overview) -> None: ...
    async def relay(self, *, member_name, text: str) -> str: ...
    async def close(self) -> None: ...

# tools/team.py — TeamBackend 新增方法
def bridge_enabled(self) -> bool
def is_bridge_agent(self, name: Optional[str]) -> bool
def bridge_agent_names(self) -> frozenset[str]
def get_bridge_member_spec(self, name: str) -> Optional[BridgeMemberSpec]
def set_bridge_adapter(self, name: str, adapter: Optional[BridgeProtocolAdapter]) -> None
def get_bridge_adapter(self, name: str) -> Optional[BridgeProtocolAdapter]
async def spawn_bridge_agent(self, *, member_name, display_name, persona,
                              desc=None, model_name=None,
                              mailbox_inject_mode=PASSTHROUGH,
                              protocol="", adapter_config=None) -> MemberOpResult
```

## 拒绝方案 / 设计取舍

| 选项 | 取舍 |
|---|---|
| 把 BridgeProtocolAdapter 当作 jiuwen Model 接口（远程返回 tool_call） | **拒绝**。远程是独立实体，不应感知 jiuwen 内部 tool 体系；纯文本协议更稳健。|
| 给 bridge avatar 加 `consult_external_agent` 工具 | **拒绝**。通信通道单一更可控；mailbox 自动转发已经覆盖；多一条工具路径会让 bridge LLM 出现"主动咨询循环"风险。|
| 进 bridge context 的形态可配 PASSTHROUGH / REPHRASE | **改义**。这一配置实际控制**送给远程**的格式（出站），进 bridge context 的格式统一为"原消息 + 远程回复 + 调度契约文案"。两个层次分清。|
| bridge avatar 工具集白名单（仿 HITT） | **拒绝**。bridge 是完整 teammate，调度需要全部工具（claim_task / send_message / member_complete_task / ...）。|
| bridge avatar LLM 综合远程回复后改写再回团队 | **拒绝**。bridge 是调度员不是内容创造者；改写违背"双方独立"原则，且容易让 bridge 变成第二个 LLM 重写器。原样转发即可。|

## 已知遗留

- A2A / ACP / claudecode CLI 的具体 `BridgeProtocolAdapter` 实现尚未提供；适配器查找注册表（按 `protocol` 字符串映射）也未做，Phase-2 跟进。
- subprocess spawn 模式下 bridge avatar 进程的 backend 没有 leader 端 `set_bridge_adapter` 注入的 adapter；inprocess 共享 backend 时正常。后续可走 messager 通道镜像同步。
- HITT 的 mailbox 仍走 callback 不进 avatar context（保持原行为）；如果未来要让 HITT 也用"消息进 context"，可参考 `compose_bridge_inbound` 设计。
