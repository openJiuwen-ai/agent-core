# openjiuwen.agent_evolving.trajectory

Canonical 轨迹值对象、进程内 span 采集、同步归档和无状态投影接口。

## 根包公开接口

```python
from openjiuwen.agent_evolving.trajectory import (
    FileTrajectoryStore,
    InMemoryTrajectoryStore,
    Trajectory,
    TrajectorySpanProcessor,
    TrajectoryStore,
)
```

根包只公开以上五个名称。Span accessor、Team selector、消息投影和离线构造使用明确的子模块 API。

## class Trajectory

```python
class Trajectory:
    @classmethod
    def from_otlp(cls, payload: Mapping[str, object]) -> "Trajectory": ...

    @classmethod
    def from_historical_otlp(cls, payload: Mapping[str, object]) -> "Trajectory": ...

    def to_otlp(self) -> dict[str, object]: ...
    def with_resource_attributes(self, attributes: Mapping[str, Any]) -> "Trajectory": ...
```

`Trajectory` 是持有 canonical OTLP JSON 深拷贝的不可变值对象。当前 payload 必须包含非空
`resourceSpans` 和 `trajectory_id`；存在 `team_id` 或 `member_id` 时还必须存在 `session_id`。

属性：

- `trajectory_id: str`
- `session_id: str | None`
- `team_id: str | None`
- `member_id: str | None`
- `resource_attributes: dict[str, Any]`
- `metadata: dict[str, Any]`，是 detached resource attributes 的别名

`to_otlp()`、`resource_attributes` 和 `metadata` 都返回脱离原对象的数据；
`with_resource_attributes()` 返回新轨迹，不修改原轨迹。

只有在兼容读取可能缺少 session identity 的历史数据时才使用 `from_historical_otlp()`。

## class TrajectorySpanProcessor

`TrajectorySpanProcessor` 实现 OpenTelemetry `SpanProcessor`，把已结束 span 扇出到进程内订阅。先把一个
共享实例注册到当前 `TracerProvider`，再把同一个对象注入所有消费 Rail。

```python
subscription = processor.subscribe(
    include_span_categories={"llm", "tool"},
    trace_id=None,
)

increment, issues = processor.drain(subscription)
processor.unsubscribe(subscription)
```

- `include_span_categories` 过滤 `llm`、`tool` 等类别。
- `trace_id` 将订阅路由到一条 trace；省略时使用 invoke-local `ContextVar` 路由。
- `drain()` 原子返回 pending canonical 增量和采集质量问题。
- `unsubscribe()` 在最终 drain 后释放 handle。
- `suppress()` 阻止 optimizer/reviewer 自身工作重新进入轨迹订阅，同时保留普通 exporter 输出。

不要从 exporter 回读轨迹，也不要为每个 Rail 单独创建 processor。

## class TrajectoryStore

```python
class TrajectoryStore(Protocol):
    def save(self, trajectory: Trajectory, version: str | None = None) -> None: ...

    def load(
        self,
        trajectory_id: str,
        version: str | None = None,
    ) -> Trajectory | None: ...

    def query(
        self,
        *,
        version: str | None = None,
        session_id: str | None = None,
        team_id: str | None = None,
        member_id: str | None = None,
        case_id: str | None = None,
        source: str | None = None,
    ) -> list[Trajectory]: ...
```

Store 是同步归档接口，新写入只接受 canonical `Trajectory`。

### InMemoryTrajectoryStore

按 version 和 trajectory ID 保存数据的进程内实现，存储 detached canonical snapshot。

### FileTrajectoryStore

JSONL 文件实现。`load()` 和 `query()` 会在读取边界识别受支持的历史 step record 和 OTLP resource alias，
单向升级后返回 canonical `Trajectory`；新记录只写 canonical OTLP。

## 无状态 Span Accessor

使用 `openjiuwen.agent_evolving.trajectory.spans` 检查 canonical OTLP，不要建立第二套长期模型：

```python
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    read_llm_exchange,
    read_llm_messages,
    read_rl_fields,
    read_span_error,
    read_tool_call,
    read_usage,
    span_attributes,
    span_events,
    span_identity,
    span_status,
)
```

该模块也提供 normalize、merge、crop 和 trim helper。Helper 返回 detached canonical 数据，不修改输入轨迹。

## 对话消息

消费方需要有序对话消息时使用 `trajectory_to_messages()`：

```python
from openjiuwen.agent_evolving.trajectory.messages import trajectory_to_messages

messages = trajectory_to_messages(
    trajectory,
    fields={"content", "name", "tool_calls", "tool_call_id"},
)
```

该投影会排序 span、合并重叠 prompt snapshot、规范化 OpenAI-compatible tool calls，并按调用方需求选择
消息字段；它不会把消息写回 `Trajectory`。

`tool_call_id()`、`tool_call_name()` 和 `tool_call_arguments()` 支持 mapping 或对象形式的 tool call，
包括嵌套的 `function` 字段。

## Team Selector

`openjiuwen.agent_evolving.trajectory.team` 提供无状态 Team 视图：

- `span_category()` 和 `is_team_span()`
- `select_team_spans()` 和 `iter_team_spans()`
- `build_team_forest()`、`team_forest()` 和 `flatten_forest()`
- `select_member_spans()`、`member_spans()`、`member_ids()` 和 `group_spans_by_member()`
- `select_task_spans()` 和 `descendant_spans()`

这些函数只选择或组织现有 canonical span，不创建 Team trajectory registry 或第二套 Team 数据模型。

## 离线构造

历史式构造和 session 抽取只保留在显式 offline 命名空间：

```python
from openjiuwen.agent_evolving.trajectory.offline import (
    TrajectoryBuilder,
    TrajectoryExtractor,
)
```

这些接口用于离线转换和数据集工具。在线 evolution 与 RL 通过 `TrajectorySpanProcessor` 和 Rails 采集。

## 迁移说明

以下旧根包/runtime API 已删除：

- `LLMCallDetail`、`ToolCallDetail` 和 `TrajectoryStep`
- 在线 `TrajectoryBuilder` / `TrajectoryExtractor` 根导出
- `MemberTrajectorySnapshot`、`TrajectorySource`、`TrajectorySink` 和 `InMemoryTrajectoryRegistry`
- `TeamTrajectory`、`TeamTrajectoryAggregator` 和在线聚合 helper

不要恢复这些兼容导入。根据用途改用 canonical span、无状态 accessor、Team root-trace 路由或显式 offline
命名空间。
