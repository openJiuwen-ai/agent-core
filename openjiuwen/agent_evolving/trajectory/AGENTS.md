# Agent Evolving Trajectory

规范执行轨迹、进程内 span capture、消息重建与同步归档。公开入口以 `__init__.py` 为准，
只包含 `Trajectory`、`TrajectorySpanProcessor` 和三种 `TrajectoryStore` 类型。

## 模块地图

| 文件 | 职责 |
|---|---|
| `model.py` | 不可变 canonical OTLP JSON 值对象 |
| `processor.py` | `SpanProcessor` fan-out、订阅、ended-span 路由与 drain |
| `spans.py` | canonical span 的无状态访问器与转换 |
| `schema.py` | trajectory 自有的 scope、schema version 与 RL 字段名 |
| `messages.py` | span 到 OpenAI-compatible messages 的唯一重建实现 |
| `windows.py` | v2 `context.window.commit` 链重放：按 `message_id` 重建每次请求读到的窗口，与前端 reducer 同语义 |
| `serialization.py` | OTLP 值的 JSON-compatible 归一化 |
| `store.py` | 内存与 append-only JSONL 归档 |
| `team.py` | Team root-trace scope 辅助 |
| `offline/` | Session / 历史 span 到 canonical `Trajectory` 的离线转换 |

## Capture 生命周期

```text
subscribe(scope) → on_end(span) → drain(scope) → clean Trajectory
→ EvolutionRail prepared input → sync/background run_evolution
```

- `TrajectorySpanProcessor` 只做进程内路由与 drain，不负责 exporter、持久化或业务演进。
- Agent scope 使用 `session_id + member_id`；Team scope 使用 `session_id + team_id`，成员 span
  通过 root trace 汇入同一 Team 轨迹，不能同时生成成员归档副本。
- clean window、detached prepared input 和执行归档是三个不同生命周期。异步执行前必须复制
  callback 期间仍有效的数据，不能在后台继续读取可变 session/context。
- `EvolutionRail.get_trajectory()` 只返回当前 clean view；`TrajectoryStore` 是显式归档边界。

## Canonical 模型铁律

- `Trajectory` 拥有输入 OTLP JSON 的深拷贝；输入 payload、`to_otlp()` 返回值和访问器结果都
  不能反向修改对象。
- 数据必须带合法 scope。不存在历史格式读取入口：旧 step/detail 记录与旧语义键（`gen_ai.prompt.{i}`、
  `gen_ai.tool.*`、`gen_ai.usage.*_tokens` 等）一律不再读取，也不要重新引入回退。
- `trajectory_to_messages()` / `project_trajectory_messages()` 以 v2 `context.window.commit` 链为唯一
  依据：每次请求贡献其窗口，已出现过的消息按 `message_id` 识别，**绝不按文本比较合并 prompt**。
  completion 在后续窗口中重现时保留模型原始输出；tool 结果以窗口内容（模型实际看到的）为准、
  名称取自 tool span。无窗口的请求原样贡献 prompt 并报告 `missing_context_window`，不做启发式回退。
  compaction 请求（`openjiuwen.request.purpose=compaction`）不贡献消息。
  TTSE detect/induce 传 `invoke_local=True`：无窗口 prompt / 窗口从最后一条 `user` 切开，后续
  span 不再把整段 prompt 追加进去；这是切片，不是 overlap merge。
- 裁剪 clean window 用 `windows.trim_trajectory_window()`：事件 span 不占 `max_trajectory_spans`
  配额，被裁断的提交链由 `trim_baseline` 重新给出可重放的链首。
- `TrajectoryBuilder` / `TrajectoryExtractor` 只属于 `trajectory.offline`。不要恢复旧顶层导出，
  也不要重新引入 step、snapshot 或第二套在线轨迹模型。
- `FileTrajectoryStore` 保持 append-only JSONL，只读写 canonical OTLP 记录。

## 修改与测试

- 新增 span 语义先扩访问器，再让消费方使用；不要让业务模块直接遍历并猜测 OTLP 字段。
- 修改消息重建时覆盖 assistant tool call、tool result、空字段 fallback、同文本不同身份的消息、
  缺失窗口与 compaction；修改 capture 时覆盖 Agent/Team scope、drain 隔离和 detached async input。
- 修改重放语义或 v2 payload 形状时，同步 `fixtures/v2/` 向量与前端测试中的同名副本，以及
  `extensions/observability/schemas/trajectory_v2_payloads.schema.json`。
- 运行：`uv run pytest tests/unit_tests/agent_evolving/trajectory -q`。
- 同时检查 `tests/unit_tests/harness/rails/evolution/` 中相关 Rail capture 和 message 测试。
