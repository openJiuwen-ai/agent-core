# S_02 DeepAgent 架构

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/deep_agent.py`、`openjiuwen/harness/schema/interaction.py`、`openjiuwen/harness/schema/state.py`、`openjiuwen/harness/schema/agent_mode.py` |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 `DeepAgent` 的本体架构：生命周期、Runner 入口、交互式 supervisor 装配、
状态持久化。它是 harness 的"脊柱"，把 `S_01` 的构造流产物接进 `S_03` 的 task loop、
`S_04` 的 rails、`S_10` 的子代理。

具体覆盖：

- `DeepAgent` 的公开执行入口：`invoke` / `stream` / `follow_up` / `steer` / `abort` /
  `start` / `stop` / `send_input`。
- 交互式会话 supervisor（`InteractionPhase` 状态机、`RoundWorkItem` / `EventManager`、
  `OutputLeaseManager` 输出消费模型）。
- 状态持久化面：`load_state` / `save_state` / `clear_state`。
- mode 切换：`switch_mode` / `restore_mode_after_plan_exit` / `get_plan_file_path`。
- rail 装配面（`add_rail` / `register_rail` / `unregister_rail` / 状态查询）——但**不定义**
  rail 本身，见 `S_04`。
- task-loop 装配入口（`_setup_task_loop` / `loop_coordinator` / `event_manager`）——但**不定义**
  事件语义，见 `S_03`。
- 子代理装配（`create_subagent` / `_bind_inherited_artifact_root`）——语义见 `S_10` / `S_18`。

不在本规约范围内：
- 公开 API 表面与构造流 —— `S_01`。
- TaskLoop 事件类型、handler/executor/controller —— `S_03`。
- Rail 基类与 priority —— `S_04`。
- Goal 状态机（`_load_goal_record_locked` 只是透传）—— `S_11`。

## 不变量

1. `DeepAgent` 的公开执行入口**只有** `invoke` / `stream` / `follow_up` / `steer` /
   `abort` / `start` / `stop` / `send_input` 八个（外加 `run_one_round` 供 supervisor
   内部单轮执行）。任何宿主不得直接触碰 `_run_task_loop*` 私有方法。
2. `invoke(inputs, session)`（单轮或 task-loop 模式）在返回前**必先** `_ensure_initialized()`；
   未初始化前的 `invoke` 抛 `build_error`（react agent 未构造）。
3. `start(*, session)` 绑定会话并启动交互式 supervisor；`session=None` 时创建
   `create_agent_session()` （id 为 `"default"`，仅测试 / 简单嵌入用）。`start` 是**幂等的**
   ——已启动时 `_ensure_interaction_running()` 只复位运行态，不二次绑定；`stop()` 走后
   置 `InteractionPhase.TERMINATED`，再次 `start` 抛 `RuntimeError("interaction_terminated")`。
4. **一次只绑一个会话**：`start` 时若已绑定其它 session，抛 `RuntimeError`；
   `_interaction_start_lock` 持有期间禁止 re-enter `start` / `stop`。
5. 交互循环的入站只有 `SendInputRequest`（`inputs["query"]` 是唯一用户文本源；可携带
   `InteractiveInput` 做中断恢复）；`mode` 只能是 `InputDispatchMode.FOLLOW_UP` 或 `STEER`。
6. 输出消费是**单消费者租约**模型：`OutputLeaseManager.attach()` 同一时刻至多一个租约；
   `attach_output()` 在已有消费者时返回 `None`。租约用 `token` 标识，`detach_output(token, ...)`
   只认租约持有 token。
7. 交互 phase 状态机：`IDLE → RUNNING → TERMINATED`。`phase()` 暴露当前值；
   `_try_transition_interaction_phase` 是唯一转换入口。
8. `EventManager` 是 supervisor 的唯一工作队列：`push_user` / `push_goal` 分工，
   `push_goal` 在 goal 已 running 时返回 `False`（去重）；`next_work` 只弹一个
   `RoundWorkItem`。见 `S_11` goal 语义。
9. 状态持久化：`load_state(session)` 从 session 读 `DeepAgentState`（`_SESSION_STATE_KEY`）；
   `save_state` 写回；`clear_state` 清空并移除 session 运行时属性。`DeepAgentState` 字段：`iteration` / `task_plan` / `stop_condition_state` /
   `pending_follow_ups` / `plan_mode: PlanModeState`（`schema/state.py`，`to_session_dict` /
   `from_session_dict` 是 checkpoint 序列化边界）。
10. mode 切换只经 `switch_mode(session, mode)`（`AgentMode.PLAN` / `NORMAL`，`AgentMode` 只有两值
   `PLAN = "plan"` / `NORMAL = "normal"`）；plan 模式下
    `get_plan_file_path(session)` 返回 plan 文件路径（`resolve_plan_file_path` 语义见 S_05）。
11. rail 生命周期面：`register_rail` / `unregister_rail` 是异步的并维护
    `_pending_rails` / `_registered_rails` / `_stale_rails` 三列表状态；`add_rail` 只排队
    （pending）。rail 优先级、事件路由见 `S_04`。

## 接口契约

```python
async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Dict[str, Any]
async def stream(self, inputs: Any, session: Optional[Session] = None,
                 stream_modes: Optional[List[StreamMode]] = None) -> AsyncIterator[Any]
async def follow_up(self, msg: str, task_id: Optional[str] = None,
                    session: Optional[Session] = None) -> None
async def steer(self, msg: str, session: Optional[Session] = None) -> None
async def abort(self, session: Optional[Session] = None) -> None
async def start(self, *, session: Optional[Session] = None) -> None
async def stop(self) -> None
async def prepare_interaction_task_loop(self, session: Session) -> None
async def run_one_round(self, work: RoundWorkItem, task_id: str, session: Session) -> None
async def send_input(self, request: SendInputRequest) -> None
def add_rail(self, rail: AgentRail) -> "DeepAgent"          # 排队，不初始化
async def register_rail(self, rail: AgentRail) -> "DeepAgent"  # 挂载并 init
async def unregister_rail(self, rail: AgentRail) -> "DeepAgent"
def load_state(self, session: Session) -> DeepAgentState
def save_state(self, session: Session, state: DeepAgentState) -> None
def clear_state(self, session: Session) -> None
def switch_mode(self, session: Session, mode: str) -> None
def restore_mode_after_plan_exit(self, session: Session) -> None
def get_plan_file_path(self, session: Session) -> Path | None
def phase(self) -> InteractionPhase
def event_manager(self) -> EventManager
def loop_session(self) -> Optional[Session]
def loop_coordinator(self) -> Optional[LoopCoordinator]
```

错误 / 返回语义：

- `invoke` 未初始化 / `_react_agent is None` → `build_error`（`StatusCode`），不返回半结果。
- `start` 已停（TERMINATED）→ `RuntimeError("interaction_terminated")`。
- `start` 绑到第二个 session → `RuntimeError`。
- `attach_output` 已有消费者 → 返回 `None`（非异常）。
- `detach_output(token, *, abort_active_round)`：token 不匹配 → `False`。
- `abort` 触发 `_cancel_session_deep_tasks` + `_release_session_subagent_controls`，
  不抛异常（幂等）。

## 数据结构

### InteractionPhase 状态机

| 状态 | 进入条件 | 退出 |
|---|---|---|
| `IDLE` | 构造 / `stop()` 完成后可重新 start | → `RUNNING`（`start`） |
| `RUNNING` | `start()` 成功绑定会话 | → `TERMINATED`（`stop`）；临时复位不换状态 |
| `TERMINATED` | `stop()` 完成 | 终态，不再接受 `start` |

### OutputLeaseManager 生命周期

| 成员 | 语义 |
|---|---|
| `attach()` | 无消费者时创建租约，返回 `OutputLease(token)`；否则 `None` |
| `detach(token, discard_buffer=True)` | 释放租约、置 `closed`；token 不匹配返回 `False` |
| `emit(item, expected_token=None)` | 无消费者 / token 不匹配 / finishing 时丢弃 |
| `finish_current()` | 排 `_OUTPUT_END` 哨兵，drain 完关闭 |
| `next_item(lease)` | 阻塞取一个输出；租约关闭返回 `None` |
| `shutdown()` | 置 `_closed`，释放当前租约，清空队列 |



### DeepAgentState 字段生命周期

| 字段 | 设置时机 | 清空时机 | 备注 |
|---|---|---|---|
| `iteration` | 每轮 `LoopCoordinator.increment_iteration` | `clear_state` | checkpoint 恢复键 |
| `task_plan: Optional[TaskPlan]` | task 计划 rail / 工具写入 | `clear_state` | todo 图载体（模型见 `S_05`） |
| `stop_condition_state` | `LoopCoordinator.get_state` | `clear_state` | 冷恢复续跑键（求值器见 `S_03`） |
| `pending_follow_ups` | follow_up 事件 | drain 后清 | 队列 |
| `plan_mode: PlanModeState` | `switch_mode` | plan 退出后复位 | `mode` / `pre_plan_mode` / `plan_slug` / `prompt_context` |

### AgentMode
| 值 | 语义 |
|---|---|
| `PLAN = "plan"` | plan 模式（`get_plan_file_path` 生效） |
| `NORMAL = "normal"` | 默认模式 |

## 与其它 spec 的关系

- 入口与配置流见 `S_01`；`validate` 所用 `DeepAgentConfig` 字段语义见 `S_01`（数据形态）与本 spec（生命周期）。
- task-loop 事件类型（`DeepLoopEventType.FOLLOWUP/STEER/ABORT`）、controller/coordinator
  语义见 `S_03`；本 spec 只承诺 `event_manager` 作为 supervisor 唯一队列。
- rail 面（`_register_rail_selective` 的 `_BRIDGE_EVENTS` / `_OUTER_ONLY_EVENTS` /
  `_DEEP_EVENTS` 事件路由）见 `S_04`。
- `create_subagent` / `_find_subagent_spec` 的语义、`_bind_inherited_artifact_root` 的
  工作目录继承见 `S_10` / `S_18`。
- `_load_goal_record_locked` / `_promote_loop_follow_ups` 的 goal 语义见 `S_11`。
- `switch_mode` / plan 文件路径解析最终落到 `tools/agent_mode_tools.py` 的
  `resolve_plan_file_path` / `get_or_create_plan_slug`，见 `S_05`。
