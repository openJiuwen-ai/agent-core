# openjiuwen.agent_evolving.trajectory

`openjiuwen.agent_evolving.trajectory` 定义执行轨迹的类型（Trajectory、TrajectoryStep、ExecutionSpec、UpdateKey、Updates），以及从 Session 抽取轨迹与按条件筛选步的接口。

---

## 类型别名

* **UpdateKey**：`Tuple[str, str]`，表示 (operator_id, target)。
* **Updates**：`Dict[UpdateKey, Any]`，算子参数更新集合。
* **StepKind**：`Literal["llm", "tool", "memory", "workflow", "agent"]`，单步类型。

---

## class openjiuwen.agent_evolving.trajectory.types.ExecutionSpec

单次执行的元信息（只读 dataclass）。

* **case_id**(str)：样本 ID。
* **execution_id**(str)：执行 ID。
* **seed**(int，可选)：随机种子。默认值：`None`。
* **tags**(Dict[str, Any]，可选)：标签。默认值：`None`。

---

## class openjiuwen.agent_evolving.trajectory.types.TrajectoryStep

轨迹中的单步。

* **kind**(StepKind)：步类型（llm/tool/memory/workflow/agent）。
* **operator_id**(str，可选)：算子 ID。
* **agent_id**(str，可选)：智能体 ID。
* **role**(str，可选)：角色。
* **node_id**(str，可选)：节点 ID。
* **inputs**(Any)：输入。
* **outputs**(Any)：输出。
* **error**(Dict[str, Any]，可选)：错误信息。
* **start_time_ms**(int，可选)：开始时间（毫秒）。
* **end_time_ms**(int，可选)：结束时间（毫秒）。
* **meta**(Dict[str, Any])：元数据（如 invoke_id、parent_invoke_id、child_invokes 等）。

---

## class openjiuwen.agent_evolving.trajectory.types.Trajectory

完整执行轨迹。

* **case_id**(str)：样本 ID。
* **execution_id**(str)：执行 ID。
* **trace_id**(str，可选)：链路 ID。
* **steps**(List[TrajectoryStep])：步骤列表。
* **edges**(List[Tuple[int, int]]，可选)：步骤间依赖边（索引对）。默认值：`None`。

---

## class openjiuwen.agent_evolving.trajectory.operation.TracerTrajectoryExtractor

从 Session 的 tracer 中抽取 Trajectory。会解析 agent 与 workflow 的 span，构建 steps 与 edges，不依赖 core 内部实现细节（仅依赖 invoke_type、name、inputs、outputs、error、meta_data、llm_call_id 等字段）。

### extract(session, execution: ExecutionSpec) -> Trajectory

从 session 的 tracer 中抽取一条轨迹。

**参数**：

* **session**：带 tracer 属性的会话对象。
* **execution**(ExecutionSpec)：本次执行的元信息。

**返回**：

**Trajectory**，包含所有步骤及依赖关系。

---

## func openjiuwen.agent_evolving.trajectory.operation.iter_steps(trajectories, *, case_id=None, operator_id=None, kind=None)

按可选条件迭代 TrajectoryStep。

**参数**：

* **trajectories**(List[Trajectory])：轨迹列表。
* **case_id**(str，可选)：按 case_id 过滤。
* **operator_id**(str，可选)：按 operator_id 过滤。
* **kind**(StepKind，可选)：按步类型过滤（如 `"llm"`、`"tool"`）。

**返回**：

**Iterator[TrajectoryStep]**，满足所有给定条件的步骤。

---

## func openjiuwen.agent_evolving.trajectory.operation.get_steps_for_case_operator(trajectories, case_id, operator_id, kind='llm')

获取指定 case 与算子的所有匹配步。

**参数**：

* **trajectories**(List[Trajectory])：轨迹列表。
* **case_id**(str)：样本 ID。
* **operator_id**(str)：算子 ID。
* **kind**(StepKind，可选)：步类型，默认 `"llm"`。

**返回**：

**List[TrajectoryStep]**，匹配的步骤列表。

---

## class TeamTrajectory

聚合的团队轨迹数据类，用于单个 session 的团队级别视图。

* **team_id** (str): 团队 ID。
* **session_id** (str): 会话 ID。
* **combined** (Trajectory): 所有成员轨迹合并后的视图，按 `start_time_ms` 排序。
* **members** (Dict[str, Trajectory]): 成员 ID 到其独立轨迹的映射。

---

## class TeamTrajectoryAggregator

聚合团队成员轨迹，生成团队级别视图。

```text
class TeamTrajectoryAggregator(
    *,
    store: Optional[TrajectoryStore] = None,
    trajectories_dir: Optional[Path] = None,
    team_id: str,
)
```

**参数**：

* **store** (TrajectoryStore, 可选): 轨迹存储实例。
* **trajectories_dir** (Path, 可选): 轨迹目录路径（向后兼容）。
* **team_id** (str): 团队 ID。

**必须提供 `store` 或 `trajectories_dir` 其中之一**。

### aggregate(session_id, filter_collaborative) -> TeamTrajectory

聚合指定会话的所有成员轨迹。

**参数**：

* **session_id** (str): 会话 ID。
* **filter_collaborative** (bool): 是否过滤协作相关步骤，默认 `True`。

**返回**：

* **TeamTrajectory**：包含 `members` 字典和 `combined` 合并视图。

---

## func filter_member_trajectory(trajectory: Trajectory) -> Trajectory

过滤成员轨迹，仅保留协作相关步骤。

保留步骤类型：

* 包含跨成员 meta 标记（`invoke_id`、`parent_invoke_id`、`child_invokes`）
* 协作工具调用（`view_task`、`claim_task`、`send_message` 等）
* 技能文件读取（包含 "skill" 的 `read_file` 调用）

**参数**：

* **trajectory** (Trajectory): 成员轨迹。

**返回**：

* **Trajectory**: 过滤后的轨迹，保留其他字段。

---

## 常量

### COLLABORATIVE_TOOLS

协作工具名称集合：

```python
COLLABORATIVE_TOOLS = frozenset({
    "view_task",
    "claim_task",
    "send_message",
    "workspace_meta",
    "read_file",
    "write_file",
})
```

### CROSS_MEMBER_META_KEYS

跨成员交互 meta 键集合：

```python
CROSS_MEMBER_META_KEYS = frozenset({
    "invoke_id",
    "parent_invoke_id",
    "child_invokes",
})
```
