# SwarmFlow 有状态会话 fork（`agent_session` 上下文继承）

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-08-21 |
| 范围 | `workflow/engine/primitives.py`（`AgentSession.fork()` + `_fork_data` + `_member_name` + `_ensure_member_name` + cache-hit 命名 + `_ensure_open` 复用命名 + 透传），`workflow/engine/backends/base.py`（`capture_fork` + `open_session(fork_data=, member_name=)` + `ensure_member_name`），`workflow/engine/backends/mock.py`（`capture_fork` 返 `None` + `ensure_member_name`），`workflow/backends/avatar_session_backend.py`（`_SessionState.session_id` / `context_seeded`、`_derive_avatar_session_id`、`capture_fork` + `_fork_data_from_native` / `_fork_ctx_from_messages` / `_persisted_messages`、`ensure_member_name`、`_start_avatar` 固定 session_id + fork 注入、`_seed_mirror_fallback`、`_round_boundary_index`），`workflow/backends/team_worker_backend.py`（`workflow_name` / `fork_data` / `member_name` 透传 + `capture_fork` / `ensure_member_name` 委托），`workflow/runner.py`（`_workflow_name`），`tools/locales/descs/{cn,en}/workflow/swarmflow.md`（fork 原语 + 链式 fork 约束） |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/test_avatar_session_backend.py` 25 passed；`test_session_primitives.py` 22 passed；`workflow/` 全目录 187 passed |

## 背景

swarmflow 只有一种有状态执行单元：`agent_session` 的常驻 avatar（长生命周期 `TeamHarness`，跨轮保留上下文）。会话演进到某节点后常需"分道扬镳"——同一个 base 认知派生出多个朝不同方向继续演进的执行者（如一个先理解基类的 `analyst`，fork 出 `分歧-1`/`分歧-2` 各自实现不同派生类）。

现状没有这个能力：`AgentSession` 没有 `fork` 方法。F_37（`F_37_swarmflow-stateful-sessions-and-human.md`）的"已知遗留"明确记录"`fork`：本期不做"——本文即补这一欠账。

团队 fork（`spawn_teammate`，见 `F_75` / `F_76`）已演进出一套完整上下文继承能力——五种 fork 模式（`full` / `before` / `after` / 两 compact）且基建（`ForkContext.from_agent(keep=)` / `compact_context(direction=)` / `fork_compact` 双向压缩 / `_on_teammate_created` 解析）已在 agent-core 完整实现。swarmflow 会话 fork 的目标是**把这五种模式完整迁移**到 `agent_session`，复用同一套 fork 引擎，零重写注入逻辑。

### 关键前置缺口：avatar 上下文当前不可跨 resume 恢复

代码调研确认（对应 SDD-0014 §1.1）：F_37 决策 3 的"avatar `Session` checkpoint 恢复"机制**已在代码里但从未生效**——avatar 每次随机 uuid session（`_start_avatar` 调 `harness.start()` 无参 → `_make_child_session` 随机 `create_agent_session(card=card)`），`pre_run` 恢复时按随机 id 查不到上次记录。让该机制生效（**固定 session_id**）是部分-hit 续跑与 fork 捕获完整上下文（含 ToolMessage）的共同底座。

## 决策

### D1 `AgentSession.fork()` — 引擎层原语（业务无关）

`agent_session` 新增 `async fork()`，eager 捕获父上下文：

```python
async def fork(self, *, fork_mode="full", keep_rounds=None,
               label=None, phase=None, instructions=None, options=None) -> AgentSession:
    if self._human:
        raise WorkflowError("fork() is only supported on agent_session")
    if fork_mode != "full" and keep_rounds is None:
        raise WorkflowError("fork() requires keep_rounds unless fork_mode='full'")
    rt = _rt.get()
    fork_data = None
    if self._sid is not None:
        fork_data = await rt.backend.capture_fork(self._sid, keep_rounds=keep_rounds, fork_mode=fork_mode)
    return AgentSession(
        label=label if label is not None else self._label,
        phase=phase if phase is not None else self._phase,
        instructions=instructions if instructions is not None else self._instructions,
        options={**self._options, **(options or {})},
        _human=False,
        _node_type="agent_session_fork" if self._history else "agent_session",
        _history=[dict(m) for m in self._history],   # 继承父镜像（resume 签名一致性）
        _fork_data=fork_data,
    )
```

- **五种 `fork_mode`**：`full` / `before` / `after` / `keep_before_compact_after` / `keep_after_compact_before`（逐字对齐团队 fork，见 D3 映射）。
- **eager 捕获**：`fork()` 调用时刻经 `capture_fork` 冻结父上下文；此后父可自由 `send`，两条线完全无关（懒捕获会让父在 fork() 与子首轮 send 之间的演进污染 after/compact 捕获）。
- **镜像继承**：子继承父 `_history`（每层 dict 浅拷贝，append-only 下语义安全），保 resume 签名一致性。
- **`_node_type="agent_session_fork"`**：父有 history 时 fork turn 以分支节点进入 4 层观测；父从未 send 则退化为普通 `agent_session`（无可继承）。
- **`human_session` 拒绝**：`fork()` 抛 `WorkflowError`（fork 出第二个真人的 avatar 语义不明）。
- **引擎铁律 1**：`fork()` 只继承镜像 + 委托 `capture_fork` + 存不透明 `fork_data`，不碰任何 `agent_teams` 业务符号（`engine/` 不得 import 业务模块）。

### D2 `keep_rounds` 两档语义（缺省报错 vs 越界降级）

- **缺省（`None`）且 `fork_mode != "full"` → 抛 `WorkflowError`**：四种非 full 模式全靠 split 点，缺省时结果与 `full` 无异，静默降级会掩盖作者"忘了传 / 模式选错"，与引擎对未知 options 键 fail-fast 一致。
- **越界（非 None 但 > 实际轮数）→ 不报错，backend 告警 + 静默回退全量**：脚本静态、轮数可能因演化变化，"传了但超出"是有意的健壮性降级，对齐团队 fork "wrong name silently falls back to full-context fork"（`fork.py:87` 守卫）。

### D3 五种模式 → swarmflow 映射（复用团队 fork 引擎）

`keep_rounds` 换算成消息索引后逐字对齐 `_on_teammate_created`（`F_75`）：

| fork_mode | 语义 | 复用机制 |
|---|---|---|
| `full` | 全量注入 | `ForkContext.from_agent(父)` |
| `before` | 第 N 轮之前（含）保留 | `from_agent(父, checkpoint=idx, keep="before")` |
| `after` | 从第 N 轮起（含）保留 | `from_agent(父, checkpoint=idx, keep="after")` |
| `keep_before_compact_after` | 前保留 + 后压摘要 | 全量捕获 + `compact_context(direction="after")` |
| `keep_after_compact_before` | 后保留 + 前压摘要 | 全量捕获 + `compact_context(direction="before")` |

- **轮数 → 消息索引**：作者只能感知"轮"，backend 用 `_round_boundary_index(msgs, keep_rounds, keep_after)` 换算成消息索引（一轮 = 一条 `UserMessage` 起始；`before` 系边界含该轮 assistant 与闭合 ToolMessage，`after` 系边界从该轮 `UserMessage` 起、由 `ForkContext._trim_leading_orphan_tool_messages` 剔除前导孤儿 ToolMessage）。越界返回 `None` → 告警 + 回退全量。
- **compact 模式**：`capture_fork` 全量捕获 + 标 `compact_split` / `compact_direction`；压缩**延后到注入时用子 native**（其自身 model 做摘要，KV 热），经 `fork_compact.compact_context(direction=)` 双向压缩——与团队 fork 注入路径一致。

### D4 接口层（`AgentBackend`）

- 新增 `capture_fork(session_id, *, keep_rounds, fork_mode) -> dict | None`：eager 快照会话上下文。默认 `NotImplementedError`（单次-shot backend 拒绝 fork）；`MockBackend` 返 `None`（fork 走镜像兜底，确定性保持）。
- `open_session(..., fork_data=None)`：默认行为逐字不变；支持 fork 的 backend 以 `fork_data` 种子新会话上下文。

### D5 前置底座：avatar 绑定稳定唯一 session_id（让 F_37 决策 3 生效）

`_start_avatar` 建 child session 时传入稳定派生 id（替代随机 uuid）：

```python
def _derive_avatar_session_id(self, member_name: str) -> str:
    wf = self._workflow_name or "workflow"
    return f"{self._team_name}/{wf}/{member_name}"
```

- **`{team}/{workflow}/{member}`**：跨同进程 resume 稳定（`pre_run` / `_read_persisted_messages` 能按稳定 id 定位上次 `state["context"]`），且成员间唯一（`member_name` 已唯一，`workflow_name` 区分同 session 下不同脚本）。
- **实现**：`open_session` 构造 `TeamSession(session_id=派生id, team_id=team)` → `harness.start(team_session=...)` → `_make_child_session` 经 `create_agent_session(session_id=...)` 继承固定 id（`team_harness.py:224-235` / `agent_team.py:129` 已支持）。
- **恢复链路**（同进程已验证）：每轮 `save_contexts` 写 `state["context"][...]["messages"]`（含 ToolMessage）→ run 结束 `dispose`→`commit`→InMemory store → 同进程 resume 前 N 轮 cache hit 短路、第 N+1 轮 miss 建 native → `pre_run` 按稳定 id 恢复 → `_load_state_from_session` 重建 messages。
- **`_SessionState.session_id`**：记录派生 id（跨 resume 稳定）。
- **范围界定**：仅同进程恢复（InMemory store + 固定 session_id）；**跨进程/重启的持久化 checkpointer 装配本期不做**——`PersistenceCheckpointer` 独立于 F_37 决策 3，且 `set_default_checkpointer` 是进程级全局、会改变所有 session 的持久化行为，副作用大。列为已知遗留。

### D6 fork 捕获的完整上下文（含 ToolMessage）

`capture_fork` 的两条捕获来源（eager 语义下 base 等价，区别在是否含 ToolMessage）：

1. **父 live**（本 run 的 `_sessions` 里有活跃 avatar）：`ForkContext.from_agent(父native)`（`get_current_context()`，含 ToolMessage）。
2. **父未重建（fully-hit resume，无 live native）**：从 checkpointer 恢复——构造一个**独立**的 `core.session.agent.Session`（非"绑定"父的 live session），复用父的固定 `session_id` 与 `{team}_{member}` 的 card.id（让 `pre_agent_execute` 命中同一个 `AgentStorage` 分桶），跑 `pre_run`（内部先 `trigger(AGENT_SESSION_CREATED)` 再调 `pre_agent_execute` 做 checkpointer 恢复；**无 harness / 无 LLM / 无 supervisor**）→ 读 `state["context"]["default_context_id"]["messages"]` → `_normalize_messages`（含 ToolMessage）。**不需要重建父 avatar**：恢复的载体是独立 Session，与 checkpointer 的交互走正规的 `pre_agent_execute`，不依赖 live native。

**`_read_persisted_messages`（fork.py:130-158）不能直接用于此**——它需要 `agent.loop_session`（live avatar 的属性），而 fully-hit resume 时 avatar 从未被拉起。故 swarmflow 侧自建 `_persisted_messages`，走 `create_agent_session(session_id=固定id, card=AgentCard(id=f"{team_name}_{member_name}"))` + `pre_run`，复用 `ForkContext._normalize_messages` 的消息归一化。**注意 card.id 必须带 team 前缀**（`{team_name}_{member_name}`）——真实 avatar 的 agent_id 由 `derive_member_spec`（`_member_spec.py:48`）如此派生，checkpointer 按它分桶 `AgentStorage`；恢复 card 不带前缀则 `pre_agent_execute` 查不到 store、静默返回 `None`。

若两条来源都拿不到 → 返 `None`，引擎镜像兜底（降级，缺 ToolMessage）。

### D9 `_member_name` + `ensure_member_name`（cache-hit 也命名）

**问题**：fully-hit resume 时父从不建 avatar，`_sid`（已建 avatar 的句柄）保持 `None`，`fork()` 无从知道父的恢复标识。

**方案**：引擎 `AgentSession` 新增 `_member_name` 字段，在**首次 `send`（无论 cache-hit/miss）** 由 backend 的 `ensure_member_name(kind, opts)` 预留（只算 member_name，不建 avatar / 不调 LLM / 不计 spawn）。`_sid` 保持"已建 avatar"语义，只在 miss 建 avatar 时设置。`fork()` 用 `_member_name` 判断恢复路径并传给 `capture_fork`。

- **cache-hit 首轮也命名**：这是 fully-hit resume 下 fork 能定位父的前提。纯内存计数（counter + 正则 + 拼接），进程内（inprocess），微秒级，仅每个 session 首轮一次。
- **顺带修复 counter 漂移**：cache-hit/miss 都命名 → `_counter` 由"session 访问顺序"决定（与命中无关）→ 跨 resume 稳定。现状（仅 miss 命名）在部分命中时 fork 子的 member_name 会与第一次运行不一致。
- **`_ensure_open` 复用已命名**：miss 建 avatar 时 `open_session(member_name=已命名)`，backend 复用而非重新命名，避免 counter 二次递增。

### D10 链式 fork 约束（提示词层）

`b = await a.fork()` 出的子 `b` 若**尚未 send 过**就被再次 fork（`c = await b.fork()`），`b._member_name` 为 `None` → `c` 无法定位 `b` 的上下文 → 走镜像兜底（缺 ToolMessage）。这**不是错误**（降级可用），但会丢失 ToolMessage。

**提示词约束**：不要 `fork` 一个未 send 过的 fork 子会话；每个被 fork 的会话必须先 `send` 过。想基于父的早期状态派生，直接用父的 `fork_mode` + `keep_rounds`（如 `before`/`after`），而非先 fork 出一个未 send 的子再 fork 它。

### D7 fork 注入时序（`open_session(fork_data=...)`）

child avatar 的 context 是**首次模型调用**才由 `react_agent._init_context` → `context_engine.create_context` 创建，`harness.start()` 后 `get_context()` 返 `None`——**不能在 start 后用 `get_context(...).set_messages(...)` 注入**。正确做法：start 之后，用 child bound session id（`native.session_id`）调 `native.create_new_context_engine(session_id=child_sid, messages=fork_ctx.to_messages())`，compact 模式再 `compact_context(子native, split_at=..., direction=...)`。注入后标 `state.context_seeded = True`。

**resume 下必须双写（bug 修复，评审发现）**：仅注入 context engine 不够——resume 时 `harness.start()` 里的 `child.pre_run()` 已从 checkpointer 恢复了**上次运行的旧 `state["context"]`**（含旧 base 上下文）。child 首次 `_init_context` 走 `create_context(session=真实child, history_messages=None)` → pool 命中（key=`child_sid_default`）→ `_load_state_from_session(context, child, None)` → 从 child 的 session state（= pre_run 恢复的旧 context）→ `context.load_state(旧)` **覆盖了注入的 fork messages**。故 `_seed_fork_context` 还必须把**注入后的最终 messages**（`native.get_current_context(session_id=child_sid)`，含 compact 结果）**写回 child session 的 `state["context"]`**（`child.update_state({"context": {"default_context_id": {"messages": final_msgs}}})`）——这样 `_init_context` 从 session state 读到的是新 fork context 而非旧 base 上下文。若 base 未变（fork 子首轮 cache-hit，不建 avatar），此路径不触发，零影响。

### D8 镜像兜底（fork_data 为 None）

`send_turn` 首轮（`turns_executed==0` 且未种子且 history 非空）经 `_seed_mirror_fallback` 把 `(user, assistant)` 对话重建进 context（`create_new_context_engine(session_id=child_sid, ...)`）。`context_seeded` 防双写（一次种子后不再覆盖 avatar 已重建的上下文）。

## 拒绝的方案

- **`_history` 镜像手搓 seed 做 resume 重建**——被 F_37 明确拒绝（与 session checkpoint 重复且丢状态/ToolMessage）。本设计遵循 F_37，不重引入；镜像仅作 fork_data 缺失时的最后降级兜底。
- **装配持久化 checkpointer（跨进程/重启）**——本期不做：独立于 F_37 决策 3，且 `set_default_checkpointer` 进程级全局、会改变所有 session 的持久化行为。列为遗留。
- **checkpoint 工具机制（模型打点 + `checkpoints.json`）**——swarmflow 脚本静态自包含，split 点 = 作者写死的固定值（轮数），零模型运行时依赖、天然可重放。
- **懒捕获（fork 后父不得再演进）**——限制过严，与"两条线无关"冲突；eager 捕获是唯一自洽实现。
- **父从未 send 即 fork 报错**——退化为全新会话（`_node_type="agent_session"`），父确实无上下文可继承。
- **fork 后 `keep_rounds` 越界报错**——脚本静态、轮数可能演化，"传了但超出"是有意健壮性降级（见 D2）。
- **直接读 checkpointer 摸私有字段**（`_agent_stores[固定id].state_blobs[member]` + `serde.loads_typed`）——侵入 `InMemoryCheckpointer` 私有结构、依赖 InMemory 专属数据、换后端即失效。改用"构造最小 Session + `pre_run`"走正规 `pre_agent_execute` 接口（见 D6）。
- **fork 时命名子会话以支持"未 send 就再 fork"**——增加复杂度（fork 与命名机制耦合）；改为提示词约束规避（见 D10）。

## 验证

- `test_avatar_session_backend.py`（25 passed，新增 fork + 恢复组）：`_derive_avatar_session_id` 稳定/唯一/命名空间、`_round_boundary_index` before/after/越界/None、`capture_fork` 五模式各产正确 fork_data、越界回退全量并告警、未知 session 返 None、`open_session(fork_data=)` 注入 + 固定 session_id 绑定、镜像兜底首轮种子 / 空 history 不种子、**`_persisted_messages` 从 checkpointer 恢复含 ToolMessage 的 `state["context"]`**、**`capture_fork` 无 live avatar 时从 checkpointer 恢复（含 ToolMessage）**。
- `test_session_primitives.py`（22 passed，新增 fork 组）：eager 捕获并种子子会话、缺省 full、非 full 缺省 `keep_rounds` 抛 `WorkflowError`、human fork 拒绝、链式 fork、父镜像不被子污染、**fully-hit resume 仍 `ensure_member_name`（命名不建 avatar）且 member_name 跨 run 一致（counter 稳定）**。
- `workflow/` 全目录 187 passed；ruff / format 全清；mypy 无新增错误（仅仓库既有依赖/`map_parallel` lambda 预存项）。
- agent_teams 其余 2405 passed（observability 42 / external 1 失败为预存问题，stash 后同样失败，与本次无关）。

## 已知遗留

- **跨进程/重启的上下文恢复（`PersistenceCheckpointer` 装配）**：**同进程**的 fully-hit fork 已能从 checkpointer 恢复（D6/D9）；**跨进程/重启**时 InMemory store 丢失，fork 仍退化为镜像兜底（缺 ToolMessage）。后续装配 `persistence.py:725` 前须评估 `set_default_checkpointer` 进程级全局对普通 worker / leader 的影响。
- **journal 记录 ToolMessage**：保住部分-hit 的 KV 前缀收益——独立特性级改动。
- **真状态快照（消息本体 + DeepAgentState 序列化）**：fork 只捕获消息快照；DeepAgentState（task_plan / plan_mode）通常为空，后续若需再议。
- **human_session fork**：语义未定，v1 拒绝。
- **`_round_boundary_index` 依赖上下文只含 send 的 user 消息**：若框架注入额外 user 消息，轮数可能偏移——当前 `_agent_turn` 只注入 send prompt，实现时已确认。
- **fork 未 send 的 fork 子**（链式缺口）：降级为镜像兜底（缺 ToolMessage），靠提示词约束规避（D10），代码不额外支持。
