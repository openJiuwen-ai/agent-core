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
| `serialization.py` | OTLP 值的 JSON-compatible 归一化 |
| `store.py` | 内存与 append-only JSONL 归档 |
| `team.py` | Team root-trace scope 辅助 |
| `legacy.py` | 历史 step/detail mapping 到 canonical `Trajectory` 的只读转换 |
| `legacy_semconv.py` | 仅供历史转换读取的旧语义键 |
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
- 新数据必须带合法 scope。历史缺失字段只能在明确的 legacy/offline 读取入口兼容，不能
  放宽 canonical 构造约束。
- `trajectory_to_messages()` 负责 span 排序、prompt-tail overlap 合并、tool call 归一化与
  result 按 call ID 关联。保留真实重复消息，只合并跨 span 的 prompt overlap。
- `TrajectoryBuilder` / `TrajectoryExtractor` 只属于 `trajectory.offline`。不要恢复旧顶层导出，
  也不要重新引入 step、snapshot 或第二套在线轨迹模型。
- `FileTrajectoryStore` 保持 append-only JSONL；legacy record 在读取时转换，不原地改写历史。

## 修改与测试

- 新增 span 语义先扩访问器，再让消费方使用；不要让业务模块直接遍历并猜测 OTLP 字段。
- 修改消息重建时覆盖 assistant tool call、tool result、空字段 fallback、真实重复消息和多 span
  overlap；修改 capture 时覆盖 Agent/Team scope、drain 隔离和 detached async input。
- 运行：`uv run pytest tests/unit_tests/agent_evolving/trajectory -q`。
- 同时检查 `tests/unit_tests/harness/rails/evolution/` 中相关 Rail capture 和 message 测试。
