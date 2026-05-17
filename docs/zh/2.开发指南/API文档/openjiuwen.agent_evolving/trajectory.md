# openjiuwen.agent_evolving.trajectory

`openjiuwen.agent_evolving.trajectory` 定义 agent-evolving 流程对外公开的轨迹数据模型、轨迹抽取接口、轨迹存储接口，以及团队轨迹聚合能力。

---

## 类型别名

* **StepKind**：`Literal["llm", "tool"]`，抽取后轨迹中的步骤类型。
* **CostInfo**：`Dict[str, int]`，聚合 token 成本，例如 `{"input_tokens": N, "output_tokens": M}`。

---

## class openjiuwen.agent_evolving.trajectory.types.LLMCallDetail

单个 LLM 步骤的结构化详情。

* **model**(str)：模型名。
* **messages**(List[Dict[str, Any]])：输入消息。
* **response**(Dict[str, Any]，可选)：解析后的响应内容。
* **tools**(List[Dict[str, Any]]，可选)：传给模型的工具 schema。
* **usage**(Dict[str, Any]，可选)：usage 元数据。
* **meta**(Dict[str, Any])：扩展元数据。

---

## class openjiuwen.agent_evolving.trajectory.types.ToolCallDetail

单个工具步骤的结构化详情。

* **tool_name**(str)：工具名。
* **call_args**(Any)：工具输入参数。
* **call_result**(Any)：工具执行结果。
* **tool_description**(str，可选)：来自资源元数据的工具描述。
* **tool_schema**(Dict[str, Any]，可选)：工具参数 schema。
* **tool_call_id**(str，可选)：用于产物追踪的工具调用 ID。

---

## class openjiuwen.agent_evolving.trajectory.types.TrajectoryStep

抽取后的单个轨迹步骤。

* **kind**(StepKind)：步骤类型。
* **error**(Dict[str, Any]，可选)：错误信息。
* **start_time_ms**(int，可选)：步骤开始时间，毫秒。
* **end_time_ms**(int，可选)：步骤结束时间，毫秒。
* **detail**(LLMCallDetail | ToolCallDetail，可选)：结构化步骤详情。
* **reward**(float，可选)：后注入的标量奖励。
* **prompt_token_ids**(List[int]，可选)：从 LLM 响应中提取的 prompt token ID。
* **completion_token_ids**(List[int]，可选)：从 LLM 响应中提取的 completion token ID。
* **logprobs**(Any，可选)：token 级 log probabilities。
* **meta**(Dict[str, Any])：扩展元数据，例如 `operator_id`、`agent_id`、调用链关系等。

---

## class openjiuwen.agent_evolving.trajectory.types.Trajectory

完整的抽取轨迹。

* **execution_id**(str)：唯一执行 ID。
* **steps**(List[TrajectoryStep])：有序步骤列表。
* **source**(str)：执行来源，默认 `"offline"`。
* **case_id**(str，可选)：离线训练 case 标识。
* **session_id**(str，可选)：会话 ID。
* **cost**(CostInfo，可选)：聚合后的 token 成本。
* **meta**(Dict[str, Any])：轨迹级元数据。

---

## class openjiuwen.agent_evolving.trajectory.extractor.TrajectoryExtractor

从 `Session.tracer()` span 中抽取 `Trajectory`，并把 span 数据规范化为轨迹步骤。

```text
class TrajectoryExtractor(resource_manager: Any = None)
```

### extract(session, case_id=None) -> Trajectory

从会话 tracer 中抽取一条轨迹。

**参数**：

* **session**：暴露 `tracer` 的 Session 对象。
* **case_id**(str，可选)：离线训练使用的 case 标识。

**返回**：

* **Trajectory**：包含规范化步骤的轨迹对象。

---

## class openjiuwen.agent_evolving.trajectory.builder.TrajectoryBuilder

以增量方式记录 `TrajectoryStep`，并构建最终 `Trajectory`。

```text
class TrajectoryBuilder(
    execution_id: str | None = None,
    *,
    session_id: str | None = None,
    source: str = "offline",
    case_id: str | None = None,
)
```

### record_step(step) -> None

向 builder 追加一个步骤。

### build() -> Trajectory

构建最终轨迹对象。

---

## class openjiuwen.agent_evolving.trajectory.store.TrajectoryStore

用于保存、加载、查询轨迹的持久化协议。

### save(trajectory, version=None) -> None

保存一条轨迹。

### load(execution_id, version=None) -> Optional[Trajectory]

按 execution ID 加载一条轨迹。

### query(version=None, **filters) -> List[Trajectory]

按 `session_id`、`case_id`、`source` 等元数据过滤查询轨迹。

---

## class openjiuwen.agent_evolving.trajectory.store.InMemoryTrajectoryStore

用于测试和开发的内存轨迹存储。

---

## class openjiuwen.agent_evolving.trajectory.store.FileTrajectoryStore

基于 JSONL 的轨迹文件存储。

```text
class FileTrajectoryStore(base_dir: Path)
```

---

## class openjiuwen.agent_evolving.trajectory.aggregator.TeamTrajectory

单个 session 的团队聚合轨迹。

* **team_id**(str)：团队 ID。
* **session_id**(str)：会话 ID。
* **combined**(Trajectory)：团队合并轨迹。
* **members**(Dict[str, Trajectory])：成员 ID 到个人轨迹的映射。

---

## class openjiuwen.agent_evolving.trajectory.aggregator.TeamTrajectoryAggregator

把成员轨迹聚合为团队级视图。

```text
class TeamTrajectoryAggregator(
    *,
    store: Optional[TrajectoryStore] = None,
    trajectories_dir: Optional[Path] = None,
    team_id: str,
)
```

### aggregate(session_id, filter_collaborative=True) -> TeamTrajectory

聚合一个 session 下的全部成员轨迹。

**参数**：

* **session_id**(str)：要聚合的会话 ID。
* **filter_collaborative**(bool)：是否仅保留协作相关步骤。

**返回**：

* **TeamTrajectory**：团队聚合视图。

---

## func openjiuwen.agent_evolving.trajectory.aggregator.filter_member_trajectory(trajectory: Trajectory) -> Trajectory

把单个成员轨迹过滤为仅包含协作相关步骤的版本。
