# Session 版本控制（vcs）设计文档

> 本文描述 `openjiuwen/core/session/vcs/` 的设计。该子系统给 agent 的会话提供
> **以 LLM 上下文为一等公民**的版本控制：消息级 append-only WAL、提交、快照、
> 重放、覆写式回退（rewind）与分叉（fork）。
> 立场：务实优先。每个能力都对应一段可运行的实现与单测；设计取舍与边界单列一节，不回避。

---

## 1. 背景与动机

对一个 agent 来说，最该做版本管理的不是抽象的 state，而是 **LLM 对话上下文（messages）**。
能"回到三步之前的对话""从某个消息点分叉出一条新对话给另一个 agent 并行探索"，
才是 agent 场景真正的杀手级能力——这正是 Claude Code `fork_session` 的形态。

而现状里，上下文的持久化是**粗粒度黑盒**：

- messages 运行时只存在于 `ContextEngine._context_pool` 的
  `SessionModelContext._message_buffer._context_messages`（一个 `list[BaseMessage]`）。
- 只有调用 `ContextEngine.save_contexts(session)` 时，messages 才被整体序列化塞进
  `global_state["context"]`，再由 checkpointer 用 **pickle 全量覆写**。
- 没有版本号、没有父子指针、没有历史链，无法回退，更无法分叉。

vcs 的目标：在**不修改任何现有代码**的前提下，给这条上下文（以及附带的 kv state）
叠加一套独立的、JSON-only 的版本历史。

---

## 2. 核心设计模型

四条由实践确定的约束，构成整个设计的骨架：

### 2.1 session_id ↔ 一条线性历史

一个 `session_id` 对应**一条线性的 append-only 历史**（WAL），一一对应、不分叉。
`Session` 对象只是这条底层历史的**访问入口**——存储机制不因 session 模型而特殊化。

### 2.2 context 是一等公民，message 级增量

版本控制的快照由两部分组成：`{context, state}`，其中 **context 为主**。

- **context**：有序消息序列。正常对话是纯 append，diff 只记录"新增的尾部"
  （`MessageDelta(kind="append")`）；压缩 / 截断 / offload 改变了前缀时记录一次整体
  `reset`。这与 Claude Code 的 `.jsonl`（append + compact_boundary）同构。
- **state**：无序 kv 字典，走递归 delta（嵌套 set + 删除路径）。

两类数据结构本质不同（有序消息 vs 无序 kv），显式分两轨反而**消除了特殊情况**，
共用同一套 WAL / commit / snapshot / replay。

### 2.3 rewind：同 session_id，覆写

`rewind(at)` 在**当前 session**上回退：截断 `at` 之后的历史，重新载入 `at` 时的状态，
从该点继续。session_id **不变**，旧的尾巴被物理覆写。

### 2.4 fork：唯一换 session_id 的操作

`fork(at)` 是**唯一**产生新 session_id 的操作：用 `at` 点的全量快照作为创世点，
通过现成的 `create_agent_session` clone 出一个全新的 `Session`，交给另一个 agent
**并行使用**；源 session 完全不受影响。

> 为什么 fork 用"全量快照创世"而非 COW 共享祖先？因为 rewind 是覆写式的——若新 session
> COW 共享了源的祖先历史，源随后 rewind 覆写就会破坏已 fork 出去的历史。以快照创世让
> 两个 session 的存储彻底独立，代价是 fork 点的消息历史被复制一份（对话历史通常不大）。

---

## 3. 整体架构

```
VersionControl (Protocol 对外能力)                         ← protocol.py
        │ 实现
VersioningManager（绑定一个 session_id 的线性历史）─持有─> VersioningBackend ← manager.py / backend.py
        │ 经注入回调对接（不改现有代码）                      │ 两实现（按 session_id 隔离）
        │   snapshot_provider / applier / forker            ├── JsonlBackend  ← jsonl_backend.py
        │                                                    └── KvBackend（复用 BaseKVStore） ← kv_backend.py
        │
   单 session 内：WAL（message append + context reset + state delta）+ snapshot + commit + head
   rewind(at)：restore(at) → backend.truncate(after=X) → 灌回 live → 从 X 续写
   fork(at)  ：restore(at) → 新 session_id 创世快照 → create_agent_session → 注入 → 返回新 Session
```

`VersioningManager` 本身**与存储和 session 无关**：它只跟一个 `VersioningBackend` 和三个
注入回调打交道，因此可以用内存替身充分单测。`adapter.for_session` 负责把这些回调接到
真实的 `Session` + `ContextEngine`。

---

## 4. 数据结构（`models.py`）

全部是 pydantic `BaseModel`（`ForkResult` 例外，它持有 live 对象、不持久化，用
`@dataclass`）。序列化走 `model_dump_json` / `model_validate_json`。`session_id` **不进**
数据结构——它是后端空间的隔离前缀。

| 类型 | 作用 | 关键字段 |
|---|---|---|
| `MessageDelta` | 某 context 一次变化 | `context_id` / `kind`(append\|reset) / `messages` / `offload_messages` |
| `StateDelta` | kv 变化（无 None 歧义） | `set`（嵌套路径→值）/ `removed`（路径列表） |
| `LogEntry` | 一条 WAL = 一次 append 的全部变化 | `event_id` / `context: list[MessageDelta]` / `state: StateDelta` / `ts` / `crc` |
| `Commit` | 线性历史命名点 | `commit_id` / `parent_id` / `event_id_high` / `snapshot_id` / `message` |
| `Snapshot` | 全量快照（replay 起点 / fork 创世） | `snapshot_id` / `event_id_high` / `context` / `state` |
| `Head` | 线性历史末端指针 | `event_id` / `commit_id` / `forked_from` |
| `ForkResult` | fork 返回 | `session_id` / `session` / `version_control` |

`LogEntry.crc` 是对"剔除 `crc` 字段后规范化 JSON"的 crc32，用于检测部分 / 损坏写入。

---

## 5. 对外 Protocol（`protocol.py`）

```python
@runtime_checkable
class VersionControl(Protocol):
    async def append(self) -> str: ...          # diff 当前 {context,state} vs 上次，落 WAL，返回事件 ref
    async def commit(self, message: str = "") -> str: ...   # append() 后打一个命名版本点
    async def snapshot(self) -> str: ...         # 在 head 落一个全量快照，加速重放
    async def restore(self, at: str) -> dict: ...   # 重建某点的 {context,state}，只读
    async def rewind(self, at: str) -> dict: ...    # 同 session 覆写回退
    async def fork(self, *, at: str | None = None) -> "ForkResult": ...  # 换 session_id，clone 新 Session
    async def list_history(self, *, limit: int | None = None) -> list[Commit]: ...
    def current_head(self) -> Head: ...          # 内存读，无 IO
```

- `at` 可以是事件 ref（`"e12"`）或 `commit_id`。
- `restore` 纯读、不改任何 session；`rewind` 改当前 session；`fork` 产生新 session。
- 不再有 branch 概念（`fork_branch` / `switch_branch` / `list_branches` 都不存在）——
  分支即"新 session"。

---

## 6. 存储后端（`backend.py` + 两实现）

`VersioningBackend` 是绑定**单个 session 空间**的存储原语集合：

```
append_log / read_log(since)            # read 遇坏 CRC/半行即停，不抛错
put_snapshot / get_snapshot / latest_snapshot(at_event_id)
put_commit / get_commit / list_commits
put_head / get_head
truncate(after_event_id)                # rewind 覆写：删 event_id>X 的 log 与 event_id_high>X 的 snapshot/commit
```

### 6.1 JsonlBackend（文件系统）

布局 `<root>/<session_id>/`：

```
HEAD                      一行 JSON（Head）
logs/log.jsonl            append-only，一行一个 LogEntry
snapshots/<id>.json       一个 Snapshot 一文件
commits/<id>.json         一个 Commit 一文件
```

- append 用 `open("a")` + 按 `fsync_policy` flush/fsync。
- HEAD / snapshot / commit / 截断后的 log 走"临时文件 + `os.replace`"做**跨平台原子覆写**。
- 阻塞 IO 包在 `asyncio.to_thread`，per-instance `asyncio.Lock` 串行化写（假设 single-writer per session）。

### 6.2 KvBackend（复用 BaseKVStore）

key 前缀 `{session_id}:vcs`：

| 概念 | key |
|---|---|
| HEAD | `{base}:head` |
| WAL | `{base}:log:{event_id:020d}`（定宽零填充 → 字典序 == 数值序，`get_by_prefix` 天然有序） |
| Snapshot | `{base}:snap:{id}` |
| Commit | `{base}:commit:{id}` |

构造时**注入一个 `BaseKVStore` 实例**（`base_kv_store.py` 明确：KV store 走直接注入、无 name factory）。
已有的 `InMemoryKVStore` / `DbBasedKVStore`（SQLite/MySQL）/ `ShelveStore` / `RedisStore` 都可用。

两个后端共享同一套 `encode_log_entry` / `decode_log_entry`，因此**字节级等价**（有单测保证）。

---

## 7. 核心算法（`delta.py` + `codec.py`）

### 7.1 context：append vs reset

```python
def diff_context(old, new) -> list[MessageDelta]:
    # 对每个 context_id：
    #   new 是 old 的前缀超集且 offload 未变 → append（只记尾部）
    #   否则（截头/压缩/offload 变/新 context） → reset（完整 messages + offload）
```

前缀比较用 encode 后的 dict（同一条 message 的 dump 是确定性的），因此无需为消息引入额外 id。

### 7.2 state：递归 delta，None 安全

`diff_state` 递归比较，产出扁平的 `{nested_path: value}` set 与 `removed` 路径列表。

**关键决策**：apply **不复用** `session.utils.update_dict`。该 helper 在嵌套合并时硬编码
`ignore_delete=False`，会把 set 里合法的 `None` 值当成删除——这会让"值确实是 None"产生歧义。
vcs 是整体快照式 apply，因此用自写的精确 nested set / remove，保证 `None` 是普通值。

### 7.3 message 序列化（`codec.py`）

`BaseMessage` **没有 discriminated union**，对基类调 `model_validate` 会丢掉子类字段
（`tool_calls` / `tool_call_id`）。因此按 `role` dispatch 到具体子类。
`AssistantMessage.model_dump` 输出 OpenAI 风格的 `tool_calls`，其
`model_validator(mode="before")` 又能吃回该格式——**往返对称**。

### 7.4 replay

```python
async def _restore_at_event(target):
    snap = latest_snapshot(at_event_id=target)      # 最近的不超过 target 的快照
    context, state, since = (snap.context, snap.state, snap.event_id_high) if snap else ({}, {}, 0)
    for entry in read_log(since_event_id=since):
        if entry.event_id > target: break
        context = apply_context(context, entry.context)
        state = apply_state(state, entry.state)
    return {"context": context, "state": state}
```

快照只是**加速点**，不改变重放语义（有单测断言"有快照 == 无快照全程重放"）。

---

## 8. 与 Session / Context 的集成（零侵入）

`for_session(session, context_engine, *, backend_factory=None, config=None, kv_store=None)`
把三个回调接到真实组件，**不修改 `session.py` / `context.py` / `context_engine.py`**：

- **snapshot_provider**：
  - context = `await context_engine.save_contexts(session)` 的结果，经 `encode_context_state` 转 JSON；
  - state = `session._inner.state().get_state()` 再剔除 `global_state["context"]`（它归 context 轨道管，避免重复）。
- **applier**：每个 context 经 `context_engine.get_context / create_context` 后 `load_state`；
  state 经 `state().set_state` 整体灌回。
- **forker**：`create_agent_session(session_id=new_id, card=..., envs=...)` 建新 session，
  `_apply` 注入 seed，写创世快照与 head，再为新 session 绑定一个 `VersioningManager`。

唯一与现有系统的耦合点，是既有的 `state().get_state()/set_state()` 契约、`ContextEngine`
公开方法、以及 `create_agent_session`。agent 形态 `{global_state, agent_state}` 与 workflow
形态 `{io_state, global_state, comp_state, workflow_state}` 因为 vcs 不解释 state 结构、
整存整取，所以都适用。

---

## 9. 序列化策略：仅 JSON，永不 pickle

agent session 的 state（kv dict）与 context（messages 是 pydantic `BaseMessage`，可 `model_dump`）
都能安全 JSON 化。vcs **全程仅 JSON**：记录类型用 pydantic 承载，免手写编解码、自带结构校验。

pickle 只属于 workflow / graph 场景——`core/graph/store/serde.py` 的 `create_serializer("json")`
故意抛错，因为图的 `channel_values` 含非 JSON 对象。vcs 不碰这条线。

---

## 10. 崩溃恢复

- 每条 `LogEntry` 带 crc32。读取时遇到坏 CRC 或半行（部分写），**停在最后一条完好的条目、
  不抛错**，等价于"丢弃崩溃瞬间未落盘的尾巴"。
- 进程重启后 `get_head()` → `restore(head)` 即可恢复到最近一致状态。
- 假设 single-writer per session（与现有 checkpointer 的单 session 模型一致）。

---

## 11. 配置（`config.py`）

```python
class VersioningConfig(BaseModel):
    backend: Literal["jsonl", "kv"] = "jsonl"
    root: str | None = None                # jsonl 根目录，默认 <cwd>/.openjiuwen/vcs
    fsync_policy: Literal["each", "batch", "snapshot", "off"] = "batch"
    snapshot_every: int = 50               # 每 N 条 append 自动 snapshot；<=0 关闭
```

- **commit 粒度**：`append` 每次状态变更落 WAL（集成层可在每轮 / `save_contexts` 后调用），
  `commit` 留给有意义的里程碑，默认不自动 commit。
- **快照频率**：默认每 50 条 append 自动落一个全量快照，平衡重放成本与空间。

---

## 12. 使用示例

```python
from openjiuwen.core.session.vcs import for_session, VersioningConfig

vc = for_session(session, context_engine, config=VersioningConfig(backend="kv"), kv_store=kv)

await vc.append()                 # 本 session 线性 WAL：逐条 message 增量 + state delta
cid = await vc.commit("after tool call")

await vc.rewind("e8")             # 同一 session：覆写回退到第 8 条事件，从这里续写

fork = await vc.fork(at=cid)      # 新 session_id 的独立 Session，源 session 不动
await asyncio.gather(             # 两个 agent 并行，各自独立线性历史
    agent_a.invoke(inputs_a, session=session),
    agent_b.invoke(inputs_b, session=fork.session),
)
```

---

## 13. 测试覆盖

`tests/unit_tests/core/session/vcs/`，pytest 纯函数风格，共 49 个用例：

- `test_codec`：BaseMessage 子类 encode/decode 往返不丢字段。
- `test_delta_context` / `test_delta_state`：append/reset、嵌套 set、删除、None 保留、list 整体替换。
- `test_backend`（两后端 parametrize）/ `test_crash_recovery`：CRUD、truncate、坏尾停读。
- `test_manager` / `test_rewind_overwrite` / `test_fork`：append 重放、snapshot 一致性、commit 链、
  rewind 覆写、fork 传 seed 且源不变。
- `test_config`：jsonl/kv 构造路径、kv 缺 store 报错。
- `test_backend_equivalence`：同序列下两后端 restore / list_history / LogEntry 编码字节相等。
- `test_integration`：真实 `SessionModelContext` + `AgentSession` 端到端
  （append → restore → rewind 覆写 → fork 出独立新 session）。

---

## 14. 文件清单

| 文件 | 职责 |
|---|---|
| `protocol.py` | `VersionControl` 对外能力 Protocol |
| `models.py` | 数据结构（pydantic） + `ForkResult` |
| `codec.py` | `BaseMessage` ↔ JSON dict（按 role dispatch） |
| `delta.py` | context append/reset diff、state 递归 delta |
| `backend.py` | `VersioningBackend` Protocol + 共享 crc / 编解码 |
| `jsonl_backend.py` / `kv_backend.py` | 文件系统 / `BaseKVStore` 两实现 |
| `manager.py` | `VersioningManager`——核心能力编排 |
| `adapter.py` | `for_session`——接真实 Session + ContextEngine |
| `config.py` | `VersioningConfig` + `build_backend` |

---

## 15. 设计取舍与未来扩展

- **不做 content-addressing 去重**：第一版用"parent 链 + 全量 snapshot + fork 快照创世"
  已满足需求；按内容哈希去重收益主要在"大量近似多分支"场景，引入 hash 对象表 + GC 的复杂度，
  待出现真实空间压力再加，且可向后兼容地加。
- **未挂在 `BaseSession` 上**：第一版 vcs 是独立组件，由上层显式 `for_session(...)` 持有。
  若未来希望 `session.version_control()` 直接可达，可向后兼容地新增一个带默认实现的访问器
  （`actor_manager()` / `close()` 已是同款非抽象默认实现的先例）。
- **接入对话循环**：当前 `append` 需由集成层显式驱动（例如在每轮 `save_contexts` 后）。
  后续可在 ReAct / DeepAgent 的循环里挂一个 hook 自动 `append`，让版本历史随对话自然增长。
- **content/state 的非 JSON 对象**：vcs 假设 agent state 可 JSON 化（用户已确认）。若某天
  agent state 引入非 JSON 对象，应在该对象层面提供 JSON 编解码，而非把 vcs 退回 pickle。
