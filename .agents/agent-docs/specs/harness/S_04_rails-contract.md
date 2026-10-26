# S_04 Rails 契约

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/rails/`（70 文件，7 个子目录） |
| 最近一次修订日期 | 2026-10-15 |
| 关联 feature | `F_01_ask-user-otel-events.md`、`F_02_genai-semconv-tool-spans.md`、`F_21_cua-desktop-subagent.md`、`F_22_cli-jiuwen-memory-rail-mount-and-switch.md` |

## 范围 / 边界

本规约定义 `DeepAgent` 的 rail（行为约束层）的**跨子模块契约**：三层基类、
事件命名空间路由、priority 梯队、挂载生命周期、两套平行拦截体系、rail 的编写清单。
`rails/AGENTS.md` 是子模块本地规则（含每个坑的展开），本 spec 是它的**规约版**——
跨子模块引用 `DeepAgent` / `core` 的边界在此钉死。

具体覆盖：

- 三层 rail 基类：`AgentRail`（core）→ `DeepAgentRail` → `BaseInterruptRail` /
  `BaseSecurityRail`。
- 事件双命名空间路由：`_BRIDGE_EVENTS` / `_OUTER_ONLY_EVENTS` / `_DEEP_EVENTS`
  （在 `deep_agent.py` 的 `_register_rail_selective`）。
- priority 梯队与注册动线（init 顺序 + 回调链顺序由一个数决定）。
- 挂载生命周期：`init` 由 core `init_rail` 调用，`uninit` 撤销；rail 按对象身份管理。
- 两套平行拦截体系（`BaseInterruptRail` vs `BaseSecurityRail`）与决策类型。
- `rails/__init__.py` 的公开导出面（`__all__`）。

不在本规约范围内：
- 具体 rail 的内部实现 —— 见各 rail 的 docstring 与 `rails/AGENTS.md`。
- 事件枚举 `AgentCallbackEvent` / `EVENT_METHOD_MAP` 本身 —— 在
  `openjiuwen/core/single_agent/rail/base.py`（core 规约管辖）。
- 安全引擎的 permission 求值（`security/`）—— 见 `S_08`。

## 不变量

1. **三层基类不变**：`AgentRail`（core）是根；`DeepAgentRail` 增加
   `before_task_iteration` / `after_task_iteration` 两个外层 task-loop 钩子 +
   `set_workspace` / `set_sys_operation` 注入；`BaseInterruptRail` 只拦
   `before_tool_call`；`BaseSecurityRail` 按 `supported_events` 声明任意事件。
2. **新增 `AgentCallbackEvent` 成员必须三选一放入一个集合**：`_BRIDGE_EVENTS`（内层
   ReActAgent）、`_OUTER_ONLY_EVENTS` / `_DEEP_EVENTS`（外层 DeepAgent）。漏放不报错——
   兜底注册到外层并打 `logger.warning`；若事件实际由内层 `ctx.fire`，rail 回调**一次都不会跑**
   （命名空间 key 是 `f"{event_namespace}_{event}"`，每个 agent 实例一份 `_instance_id`）。
   `tests/unit_tests/harness/test_deep_agent_rail_event_routing.py` 强制三个集合完全覆盖
   `set(AgentCallbackEvent)` 且互不相交。
3. **priority 数值越大越先**；排序稳定，同 priority 保持注册顺序。`register_callback`
   默认 priority 是 **100**。同一个数决定 init 顺序和回调链顺序。唯一的例外入口是
   `AgentRail.callback_priority(event)`：默认返回 `priority`，rail 可只为某一个 hook 换位置
   而不改变 init 顺序。当前仅 `EvolutionRail` 用它：其 `after_tool_call` 以
   `priority - 1_000_000` 注册，排在该链所有回调之后——它读取的 tool span 由观测 rail（10）
   在同一链中结束，且记录的是其它回调可能改写后的结果。
4. **priority 梯队是契约**（当前梯队，改它必须先 grep 是否有别的 rail 在注释里点名）：
   `100 SysOperationRail` → `95 McpRail / SkillUseRail / SubagentRail` →
   `90 TaskPlanningRail / ProgressiveToolRail / BaseInterruptRail / PermissionInterruptRail /
   BaseSecurityRail / VerificationRail` → `85 AgentModeRail / SafetyPromptRail /
   ContextAssembleRail / Context*ProcessorRail / MultimodalContextSummarizerRail / Skill*CreateRail` →
   `80 MemoryRail / CodingMemoryRail / HeartbeatRail / Skill*EvolutionRail` →
   `75 ExternalMemoryRail / JiuwenMemoryRail`（外接记忆 rail，在 MemoryRail(80) 之后、
   LLMRetry(70) 之前） →
   `70 LLMRetryRail / ToolCallResilienceRail` → `60 EvolutionRail / LspRail` →
   `50 core 默认`（含 `tools/cua/rails.py` 的 Cua*Rail） → `10 TrajectoryRail / TaskCompletionRail`。
5. **`init` 从不被回调框架调用**，只由 core 模块级 `init_rail(rail, agent)` 调用（>0.1s 升
   INFO）。三个调用点全在 `deep_agent.py`：`_ensure_initialized`（主路径，按 priority 降序）、
   `register_rail`（运行时单个挂载）、`start()`（仅对 `TaskCompletionRail` 二次 init，
   **写 rail 必须幂等**）。
6. **rail 是有状态的长生命周期实例，按对象身份管理**：`_pending_rails` / `_registered_rails` /
   `_stale_rails` 三列表 + `_hot_reload_rails` 按类型局部替换。需要跨重建存活的状态，
   注入复用对象、由每轮新建的 rail 包装。
7. **`uninit` 必须撤干净**：移工具前先校验 card 身份——`ability_manager.get(name) is card`
   才算自己加的（`MemoryRail` / `ProgressiveToolRail` 为标准姿势）。
8. **两套拦截体系互不混用**：
   - `BaseInterruptRail`：只挂 `before_tool_call`；子类实现 `resolve_interrupt(...) ->
     InterruptDecision`；决策 `approve` / `reject` / `interrupt`。
     `user_input is None` = 首次进入，非 None = 中断 resume 回来。
   - `BaseSecurityRail`：`supported_events` 声明的任意事件；**完全覆写 `get_callbacks()`**
     只按 `supported_events` 生成；决策 `SecurityAllow` / `Reject` / `Interrupt` / `Alert`。
     忘声明 `supported_events` → 一个回调都不注册；`SecurityInterrupt` 在 model 事件上
     被**静默降级成 Reject**（带 WARNING），只有 tool 事件才真中断。
9. **自定义 `__init__` 必须调 `super().__init__()`**——`DeepAgentRail.__init__` 是唯一给
   `self.sys_operation` / `self.workspace` 赋初值的地方。
10. **`DeepAgentRail` 子类必然多注册两个 no-op 回调**（`_is_base_method` 拿
    `self.__class__.<m>` 与 `AgentRail.<m>` 比）；功能无害，排查回调链时可见。
11. **ask_user 跨中断不持有 Span**：首次中断前在当前短生命周期 Span 写入
    `ask_user.requested` OTel event，恢复并得到终态答案后在新 Span 写入
    `ask_user.resolved`；两个 Span 均走既有生命周期正常闭合。两条事件以
    `session_id + execution_subject_id + interaction_id` 关联，前端不得从 LLM 输出或下一次
    请求反推 ask_user 生命周期。
12. **工具包装层必须保持中断透明**：`ToolInterruptException` 是交互控制流，不是工具失败；
    deferred 工具经 `tool_search` / `tool_call` 执行时，`ProgressiveToolRail`、包装工具和
    `AbilityManager` 必须将其原样传回既有中断处理路径，不得包装进 `ToolOutput` 或转换成
    `AbilityExecutionError`。若 target 工具在包装层内中断，中断请求保留 target call ID，
    但恢复执行必须从原 outer wrapper tool call 重新进入，以保持授权链和模型上下文中的
    tool-call ID 配对；不得把 target 当作新的 deferred 工具直调，也不得放宽普通直调限制。

12. **观测 rail 不定义第二套 GenAI 字段**：工具 span 只写共享 semconv 中的
    `gen_ai.tool.call.id/arguments/result` 与 `gen_ai.tool.name`，资源标识放入
    `openjiuwen.tool.resource_id`。Evolution rail 只清洗这套标准字段；历史输入兼容属于
    trajectory 读取边界，不属于 rail producer。
13. **多模态截图归档 rail 是共享实现**：`rails/multimodal_context_summarizer_rail.py` 的
    `MultimodalContextSummarizerRail(screenshots_to_keep=3, *, protected_user_message_names=())`
    在 `before_model_call` 中把较早的带 `image_url` 的 user 消息图像替换为
    `ARCHIVED_SCREEN_PLACEHOLDER` 文本，保留最近 `screenshots_to_keep` 条；`name` 在
    `protected_user_message_names` 中的消息不参与计数也不被替换。cua_agent 始终使用它
    （`S_18`）；`tools/mobile_gui/rails/multimodal_context_summarizer_rail.py` 保留原导入路径，
    是预置 mobile 受保护消息名的子类。
14. **中断带 id、类型和发起者，答复只给发起它的 rail**：
    - 每个中断请求有 `interrupt_id`（中断处理器收集暂停的调用时分配，`after_tool_call` 里还没有；
      同一调用上的后一次中断得到新 id，只因同一 rail 的上一个中断未被回答而再次发起的中断沿用
      原 id，即使问题变了）、`interrupt_kind`（`InterruptKind`，空串 = 未声明；只供宿主渲染和决定答复形状）和
      `interrupt_owner`（发起它的 rail 的键）。暂停结果里每个 `ToolCallInterruptRequest` 都带这三项
      和 `tool_call_id`。
    - **答复按发起者路由，不按类型**：rail 在 `_raise_interrupt` / `_raise_tool_interrupt` 中调
      `stamp_interrupt` 写入 owner（默认类路径，可按实例设置）和 kind（请求未自带时），
      `after_tool_call` 里已可见；id 只由中断处理器分配。恢复时
      `_get_user_input` 只返回本 rail 发起的中断的答复，没有则为 `None`（= 首次进入）。答复按
      （调用, owner）存放，所以一个 rail 在一个调用上同时只有一个待答的中断；**同一 agent 上挂两个
      同类 rail 时，必须给其中一个设置不同的 `interrupt_owner`**（框架不检查）。
    - **agent 内部只按 `interrupt_id` 认答复**：宿主用 `InteractiveInput.answer(interrupt_id, value)`。
      `update(tool_call_id, value)`、纯文本与 dict 只在恢复开始时换算一次：`update` 给该调用当前等待
      的中断；键含暂停调用 id 的 dict 只回答这些调用；其他输入给每个
      调用当前等待的中断。原始恢复输入仍放在 `RESUME_USER_INPUT_KEY`（或请求
      `metadata["resume_user_input_key"]` 指定的键）下，供宿主自己的 rail 读取。
    - 升级前持久化的中断没有 id：加载时按调用 id 生成 `intr_<tool_call_id>`，按调用 id 的答复照常
      换算到它；其发起者未知，对应 rail 会再问一次。
    - 同一调用仍暂停时（包括之后在子 agent 内暂停的调用），之前轮次的答复带到后续轮次
      （`carried=True`），被同一 rail 再次询问的答复丢弃。有外部副作用的 rail 用
      `is_carried_answer(ctx.extra, tool_call_id)` 避免重复：权限 rail 对携带的批准不再写规则，
      `permission_scene_hook` 的入参带 `answer_carried`。权限 rail 收到的每个批准（新的或携带的）都
      重新做引擎判定，等待期间新增的 deny 规则生效。
    - **暂停不是异常**：rail 中断在 `@rail` 包装层不触发 `on_tool_exception`、不重试；
      `after_tool_call` 仍触发，此时 `ctx.is_paused` 为真、调用还没有结果。
    - 宿主可用 `pending_interrupts(state)` 列出暂停的中断，用 `match_answers(user_input, pending)`
      按处理器同一规则换算 `InteractiveInput` 并找出过期答复。

## 接口契约

```python
# rails/__init__.py 公开导出（__all__ 节选）
AgentModeRail, AskUserPayload, AskUserRail, BaseInterruptRail, BaseSecurityRail,
CodingMemoryRail, ConfirmInterruptRail, ContextEvolutionRail, DeepAgentRail,
EvolutionRail, EvolutionInterruptRail, ExternalMemoryRail, HeartbeatRail,
JiuwenMemoryRail, ModelAnomalyDetectionRail, LspRail, McpRail, MemoryRail, MemberSkillEvolutionRail,
MultimodalContextSummarizerRail, ARCHIVED_SCREEN_PLACEHOLDER,
PermissionInterruptRail, ProgressiveToolRail, SafetyPromptRail, SecurityAllow,
SecurityCheckContext, SecurityDecision, SecurityInterrupt, SecurityReject, SecurityRail,
SessionRail, SkillCreateRail, SkillEvolutionRail, SkillUseRail, SubagentRail,
SysOperationRail, TaskCompletionRail, TaskPlanningRail, Team*Rail, TrajectoryRail,
VerificationContractRail, VerificationRail, ...

class DeepAgentRail(AgentRail):
    def set_workspace(self, workspace: Workspace) -> None
    def set_sys_operation(self, sys_operation: SysOperation) -> None
    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None
    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None

class BaseInterruptRail(AgentRail):
    def approve(self, new_args: Optional[str] = None) -> ApproveResult
    def reject(self, tool_result: object = None) -> RejectResult
    def interrupt(self, request: InterruptRequest) -> InterruptResult
    def add_tool(self, tool_name: str) -> None
    # 子类实现: resolve_interrupt(...) -> InterruptDecision

class BaseSecurityRail(AgentRail):
    def allow(self, new_args: Optional[str] = None) -> SecurityAllow
    def reject(self, ...) -> SecurityReject
    def interrupt(self, ...) -> SecurityInterrupt
    def alert(self, ...) -> SecurityAlert
    def add_tool(self, tool_name: str) -> None
    # 子类实现: run_security_check(...) -> SecurityDecision; supported_events 声明
```

错误 / 返回语义：

- `ApproveResult` 可改写 `tool_args` 后放行；`RejectResult` 置 `ctx.extra["_skip_tool"]`
  让 `ability_manager` **不执行工具**直接返回伪造结果；`InterruptResult` 抛
  `AbortError(cause=ToolInterruptException(...))`。
- `unregister_rail` 幂等；`strip_rails_by_type(rail_types)` 返回移除个数。
- `add_rail` 只排队（pending），不初始化；`register_rail` 三步走：`set_sys_operation` /
  `set_workspace` → `init_rail` → `_register_rail_selective`。

## 数据结构

### rail 状态列表

| 列表 | 语义 | 生命周期 |
|---|---|---|
| `_pending_rails` | `add_rail` / `configure` 排队，未 init | 首轮 `_ensure_initialized` 清空 |
| `_registered_rails` | 已 init + 已注册回调 | 常驻；`unregister` / hot-reload 移出 |
| `_stale_rails` | hot-reload 淘汰的旧实例 | 等待 uninit 后丢弃 |

### 事件路由三集合

| 集合 | 去向 | 成员（摘要） |
|---|---|---|
| `_BRIDGE_EVENTS` | 内层 ReActAgent | `BEFORE/AFTER_MODEL_CALL`、`ON_MODEL_EXCEPTION`、`BEFORE/AFTER_TOOL_CALL`、`ON_TOOL_EXCEPTION`、`AFTER_REACT_ITERATION`、`ON_USER_MESSAGE`、`BEFORE_STEERING_DRAIN` |
| `_OUTER_ONLY_EVENTS` | 外层 DeepAgent | `BEFORE_INVOKE` / `AFTER_INVOKE` |
| `_DEEP_EVENTS` | 外层 DeepAgent | `BEFORE/AFTER_TASK_ITERATION` |

判据：这个事件是谁 `ctx.fire` 的？`react_agent.py` fire 的进 `_BRIDGE_EVENTS`；
`deep_agent.py` fire 的进另外两个。

### 五种典型作用方式

| 方式 | 范本 | 要点 |
|---|---|---|
| 注入 prompt section | `heartbeat_rail.py`（64 行） | `init` 抓 builder 引用，`before_model_call` 里 add/remove，`uninit` 删干净 |
| 注册工具 | `mcp_rail.py` / `sys_operation_rail.py` | 纯 init 型，不参与生命周期钩子 |
| 改写模型输入 | `progressive_tool_rail.py` | `before_model_call` 里直接改写 `ctx.inputs.tools` |
| 拦截执行 | `interrupt/ask_user_rail.py` | 首次 interrupt，resume 后不执行工具、把用户答复当 tool_result |
| 回灌指令 | `task_planning_rail.py` | 全包唯一 `ctx.push_steering(...)`，延到 ToolMessage 写完之后 |

## 与其它 spec 的关系

- rail 挂载点（`_register_rail_selective` / `_ensure_initialized` / `start()`）在
  `deep_agent.py` —— `S_02`。
- `ToolLoopCompactConfig` / `ModelAnomalyDetectionRail` 的模型异常语义 —— `S_02`。
- `TaskCompletionRail` 的 goal 评估接 `goal/` —— `S_11`；`SubagentRail` / `SessionRail`
  接 `subagent_runtime` —— `S_10`；`PermissionInterruptRail` 接 `security/` —— `S_08`；
  `LspRail` 接 `lsp/` —— `S_14`。
- core 事件枚举 / `AgentRail` / `ctx` 控制面（`push_steering` / `drain_steering` /
  `request_retry` / `request_force_finish`）在 `openjiuwen/core/single_agent/rail/base.py`，
  属 core 规约管辖，不在本 spec 展开。
- 子模块本地扩展（每一个坑的展开、加 rail 清单）见 `openjiuwen/harness/rails/AGENTS.md`。
