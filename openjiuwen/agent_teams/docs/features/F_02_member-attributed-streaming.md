# Member-Attributed Streaming

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-09 |
| 涉及 commit | 待提交（与本文档同 PR） |
| 范围 | `openjiuwen/agent_teams/schema/stream.py`（新增）、`openjiuwen/agent_teams/agent/stream_controller.py`、`openjiuwen/agent_teams/agent/spawn_manager.py`、`openjiuwen/agent_teams/spawn/inprocess_handle.py`、`openjiuwen/core/runner/team_runner.py`、`openjiuwen/agent_teams/__init__.py`、`openjiuwen/agent_teams/schema/__init__.py` |
| 测试基线 | `tests/unit_tests/agent_teams/test_stream_controller.py` 16 passed；`tests/unit_tests/agent_teams/test_spawn_manager_chunk_forward.py` 3 passed |
| Refs | `#751` |

## 背景

`Runner.run_agent_team_streaming(...)` 在改造前只能流出 leader `TeamAgent` 这一条 chunk 流。inprocess 模式下 spawn 出来的 teammate 走 `Runner.run_agent_team(member=True) → TeamAgent.invoke()` 路径，invoke 内部虽然创建了 `stream_queue` 并把 DeepAgent 产出的每个 chunk 都入了队，但只把 last_result 当返回值——所有中间 chunk 在 teammate 自己的 invoke 主循环里被吞掉，外层调用方完全看不到 teammate 的思考 / 工具调用 / 文本输出。

对上层（CLI、监控、调用 SDK 的应用）来说，多 teammate 协同就是一个黑盒——只能从 leader 的 chunk 看到协调动作，看不到子任务现场。要做端到端可观测、做实时 UI 反馈，就必须让 leader 的 streaming 同时携带 teammate 的 chunk，且每个 chunk 标注归属成员。

本期目标限定为 **inprocess 模式**：同一进程同一 event loop，可以走对象引用；subprocess 模式留扩展点。

## 数据结构与状态机

### TeamOutputSchema（新增）

```python
class TeamOutputSchema(OutputSchema):
    source_member: str | None = None
    role: TeamRole | None = None

    @classmethod
    def from_output(
        cls,
        base: OutputSchema,
        *,
        source_member: str | None,
        role: TeamRole | None = None,
    ) -> "TeamOutputSchema":
        return cls(**base.model_dump(), source_member=source_member, role=role)
```

放在 `agent_teams/schema/stream.py`，**不动 core 层 `OutputSchema`**——core 跨子系统共享，不应被 team-specific 字段污染。子类对 `isinstance(x, OutputSchema)` 透明，现有消费端访问 `chunk.type / chunk.payload` 不变。

`role` 字段用 `TeamRole` 枚举（`LEADER` / `TEAMMATE` / `HUMAN_AGENT`）。消费端拿到 chunk 后能立即区分协调指令 vs 子任务输出 vs 人类输入的语义，无需维护 `member_name → role` 外部映射。`TeamRole` 是 `str` 枚举，pydantic 序列化得到字符串值，跨进程兼容。

### 数据流（inprocess 模式）

```
Teammate DeepAgent
   │ run_streaming() yields raw OutputSchema chunk
   ▼
Teammate StreamController._stream_one_round
   │ _tag_chunk(): 升级为 TeamOutputSchema(source_member, role)
   ├──────► teammate.stream_queue.put(chunk)        [本地]
   └──────► for observer in _chunk_observers:       [fan-out]
                forward(chunk)
                  │
                  ▼
                Leader StreamController.stream_queue.put(chunk)
                  │
                  ▼
                TeamAgent.stream() yield chunk
                  │
                  ▼
                Runner.run_agent_team_streaming yields chunk
```

Leader 自己的 chunk 走相同的 `_tag_chunk` 路径（observer 列表为空，直接进自己 queue）。所有出口 chunk 都是 `TeamOutputSchema`，没有"leader 走哪条 / teammate 走哪条"的代码分支。

## 决策

### 1. 派生子类，不在 core 层加字段

把 `source_member` 字段放在 `TeamOutputSchema(OutputSchema)` 而不是 `OutputSchema` 上。子类化让消费端透明，同时保持 `core/session/stream/base.py` 的纯净——`source_member` 只对 team 子系统有意义，single_agent / harness 路径继续 yield 原生 `OutputSchema`。

### 2. observer hook 而不是消息总线

leader 与 teammate 共享同一个 event loop（inprocess 同进程），直接走对象引用最简单。在 `StreamController` 加 `add_chunk_observer / remove_chunk_observer`：每次 chunk 入队时同步 fan-out 给所有 observer。observer 抛异常自动 detach（不阻塞主流），由 `team_logger.exception` 记录。

### 3. SpawnManager 是单点 wiring 责任方

teammate 的 `StreamController` 不知道 leader 是谁；leader 的 `StreamController` 不知道 teammate 在哪。`SpawnManager._wire_inprocess_chunk_forward(handle)` 是**唯一**持有两边引用的地方，构造闭包把 chunk 转投到 leader queue。数据流方向单向（teammate → leader），没有反向耦合。

forward observer 引用挂在 `InProcessSpawnHandle._chunk_forward` 字段上，`cleanup_teammate` 调用 `remove_chunk_observer` 反注册；`shutdown_all_handles` 改为 routes through `cleanup_teammate`，避免漏反注册。

### 4. None sentinel 不跨成员传播

`StreamController.close_stream()` 用 `stream_queue.put_nowait(None)` 关 stream，None 不走 observer 路径——teammate 关 stream 不应该把 leader 关掉。observer fan-out 只发生在 `_stream_one_round` 内部对真实 chunk 的循环里。

### 5. forward observer 在 leader queue 为 None 时丢弃

leader 的 `stream_queue` 仅在 `TeamAgent.stream()` / `invoke()` 头部惰性创建，而 spawn 操作发生在 leader 的 DeepAgent task loop 里、必然晚于 stream 启动——正常时序下 queue 一定存在。但 forward 函数仍做 None 检查并直接丢弃，避免 leader 已经 teardown 后 teammate 的尾部 chunk 阻塞。**不缓冲**——缓冲会反向耦合数据流。

### 6. team_runner 的 ready chunk 也升级为 TeamOutputSchema

`_build_team_runtime_ready_chunk` 产出的启动信号原本是 `OutputSchema`。team 路径下流出的所有 chunk 都应一致地带 `source_member`，这里改为 `TeamOutputSchema(..., source_member=leader_member_name)`。leader member_name 通过 `activation.agent.blueprint.member_name` 取。

## 拒绝的方案

### 方案 A：在 core 层 `OutputSchema` 上加 `source_member` 字段

**拒绝理由**：`OutputSchema` 是跨 single_agent / harness / agent_teams 共享的数据结构。`source_member` 只对 team 层有意义；给 core 加 team-specific 字段是概念污染，违反 Card / Config 与分层边界。子类化在 isinstance 关系上等价、消费端无感知，是更干净的扩展。

### 方案 B：包 envelope `TeamStreamChunk(member_name, role, chunk)`

**拒绝理由**：envelope 包装让所有现有消费端必须改 `chunk.payload → env.chunk.payload`，是大面积破坏性变更；继承可以做到同样的语义而对老代码透明（仍是 `OutputSchema` 子类）。

### 方案 C：messager 总线广播 chunk

**拒绝理由**：inprocess 同进程同 event loop，对象引用一步到位，没有理由绕一圈进 pub/sub。messager 适合跨进程（subprocess 模式），本期不需要。如果未来要支持 subprocess，扩展点已留好——`add_chunk_observer` 的接口和 `TeamOutputSchema` 结构不变，subprocess 端把 chunk publish 到 `TeamTopic.STREAM_CHUNK`、leader 端订阅并反序列化 put 到自己 queue 即可。

### 方案 D：teammate 主路径改成走 streaming，不再丢 chunk

**拒绝理由**：`Runner.run_agent_team(member=True)` 现在的 invoke 路径与 spawn 工具调用约定深度耦合，改它会牵连 `child_process.py` 的子进程入口与 `from_spawn_payload` wire 协议。observer fan-out 实现同样的语义，但只新增数据通路、不破坏既有契约。

## 验证

- `tests/unit_tests/agent_teams/test_stream_controller.py`：新增 9 个用例覆盖 `_tag_chunk` 行为、`_stream_one_round` 标注 + fan-out、observer 异常自动 detach、observer 反注册幂等、teammate → leader queue 端到端数据流、leader queue 为 None 时丢弃。**16 passed**。
- `tests/unit_tests/agent_teams/test_spawn_manager_chunk_forward.py`：新增 3 个用例覆盖 `_wire_inprocess_chunk_forward` 注入路径、`cleanup_teammate` 反注册、leader / agent_ref 缺失时 wire no-op。**3 passed**。
- 现有 streaming 用例无需修改，向后兼容。

## 已知遗留

- **subprocess 模式的 chunk 转发**：本期不实现。扩展方向：teammate 进程 publish chunk（`TeamOutputSchema.model_dump()`）到 `TeamTopic.STREAM_CHUNK`、leader 进程订阅并反序列化为 `TeamOutputSchema` 后 put 到自己 queue。`add_chunk_observer` 与 `TeamOutputSchema` 数据结构无需改动。要点：跨进程序列化 chunk 是性能开销点，可加 `TeamAgentSpec.stream_member_chunks: bool` 让用户按需开。
- **CLI / 示例的来源展示**：`cli/stream_renderer.py` 当前未利用 `source_member` 做着色或前缀；后续可加一个 per-member 颜色映射，把不同成员的 chunk 在 TUI 中可视化区分。这是渲染优化，不是协议变更。
- **chunk_observer 的有界化**：当前 fan-out 是 `for ob in list(...): await ob(chunk)` 串行调用。生产环境 observer 只有 forward 一个，无阻塞；如果未来挂多个高延迟 observer，要考虑改成并发 gather + 单 observer 超时熔断。
