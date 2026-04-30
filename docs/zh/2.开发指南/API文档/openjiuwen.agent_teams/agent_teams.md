# openjiuwen.agent_teams

`openjiuwen.agent_teams` 提供多Agent团队编排能力。通过 `TeamAgentSpec.model_validate()` 从配置加载，然后调用 `build()` 创建 `TeamAgent`。

## class TeamAgentSpec

用于构建 TeamAgent 的 JSON 可序列化规格类。组合按角色的 DeepAgentSpec 配置与团队级别配置。

* **agents**(dict[str, [DeepAgentSpec](./agent_teams.md#class-deepagentspec)]): 按角色的 DeepAgentSpec 配置。必须包含 `"leader"` 键；`"teammate"` 为可选，缺省时回退到 leader 配置。
* **team_name**(str, 可选): 团队名称。默认值：`agent_team`。
* **lifecycle**(str, 可选): 团队生命周期模式 — `temporary`（完成后解散）或 `persistent`（跨会话保留）。默认值：`temporary`。
* **teammate_mode**(str, 可选): 队友执行模式 — `build_mode`（直接完成任务）或 `plan_mode`（需要 leader 审批）。默认值：`build_mode`。
* **spawn_mode**(str, 可选): 队友启动方式 — `process`（子进程）或 `inprocess`（同一事件循环）。默认值：`process`。
* **leader**([LeaderSpec](./agent_teams.md#class-leaderspec), 可选): Leader 身份配置。默认值：`LeaderSpec()`。
* **predefined_members**(list[[TeamMemberSpec](./agent_teams.md#class-teammemberspec)], 可选): 预配置成员。提供时 leader 跳过 `spawn_member` 工具。默认值：`[]`。
* **transport**([TransportSpec](./agent_teams.md#class-transportspec), 可选): 传输层配置。默认值：`None`。
* **storage**([StorageSpec](./agent_teams.md#class-storagespec), 可选): 存储层配置。默认值：`None`。
* **worktree**(WorktreeConfig, 可选): 队友 worktree 隔离配置。默认值：`None`。
* **workspace**(TeamWorkspaceConfig, 可选): 团队共享工作空间配置。默认值：`None`。
* **metadata**(dict[str, Any], 可选): 附加元数据。默认值：`{}`。

### model_validate

```python
model_validate(data: dict) -> TeamAgentSpec
```

从 dict/JSON 解析。继承自 Pydantic BaseModel。

**参数：**

* **data**(dict): 配置字典，通常从 YAML/JSON 文件加载。

**返回：**

**TeamAgentSpec**: 解析后的规格实例。

**样例：**

```python
>>> import yaml
>>> from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
>>> 
>>> with open("config.yaml") as f:
...     cfg = yaml.safe_load(f)
>>> 
>>> spec = TeamAgentSpec.model_validate(cfg)
```

### build

```python
build() -> TeamAgent
```

实例化配置好的 TeamAgent。

**返回：**

**TeamAgent**: 配置好的 leader 实例，可用于执行。

**样例：**

```python
>>> from openjiuwen.core.runner.runner import Runner
>>> 
>>> await Runner.start()
>>> leader = spec.build()
>>> 
>>> async for chunk in Runner.run_agent_team_streaming(leader, inputs={"query": "hello"}):
...     print(chunk)
```

## class DeepAgentSpec

单个 DeepAgent 的 JSON 可序列化配置。用于 `TeamAgentSpec.agents` 字典。

* **model**(TeamModelConfig, 可选): LLM 模型配置。默认值：`None`。
* **card**([AgentCard](../openjiuwen.core/single_agent/single_agent.md#class-agentcard), 可选): Agent 身份卡片。默认值：`None`。
* **system_prompt**(str, 可选): 自定义系统提示词。默认值：`None`。
* **tools**(list[ToolCard | BuiltinToolSpec], 可选): 工具列表。默认值：`None`。
* **mcps**(list[McpServerConfig], 可选): MCP 服务器配置。默认值：`None`。
* **subagents**(list[SubAgentSpec], 可选): 子 Agent 配置。默认值：`None`。
* **rails**(list[RailSpec], 可选): 护栏配置。默认值：`None`。
* **enable_task_loop**(bool, 可选): 启用任务迭代循环。默认值：`False`。
* **enable_async_subagent**(bool, 可选): 启用异步子 Agent 执行。默认值：`False`。
* **add_general_purpose_agent**(bool, 可选): 添加通用子 Agent。默认值：`False`。
* **max_iterations**(int, 可选): 最大循环迭代次数。默认值：`15`。
* **workspace**(WorkspaceSpec, 可选): 工作空间配置。默认值：`None`。
* **skills**(list[str], 可选): 技能名称列表。默认值：`None`。
* **sys_operation**(SysOperationSpec, 可选): 系统操作配置。默认值：`None`。
* **language**(str, 可选): 语言设置（`cn` 或 `en`）。默认值：`None`。
* **prompt_mode**(str, 可选): 提示词模式。默认值：`None`。
* **vision_model**(VisionModelSpec, 可选): 视觉模型配置。默认值：`None`。
* **audio_model**(AudioModelSpec, 可选): 音频模型配置。默认值：`None`。
* **enable_task_planning**(bool, 可选): 启用任务规划护栏。默认值：`False`。
* **restrict_to_sandbox**(bool, 可选): 限制文件操作到沙箱目录。默认值：`False`。
* **auto_create_workspace**(bool, 可选): 自动创建工作空间目录。默认值：`True`。
* **completion_timeout**(float, 可选): 完成超时时间（秒）。默认值：`600.0`。
* **progressive_tool**(ProgressiveToolSpec, 可选): 渐进式工具加载配置。默认值：`None`。
* **approval_required_tools**(list[str], 可选): 需要 Leader 审批的工具名称（仅队友）。默认值：`None`。

## class LeaderSpec

Leader 身份配置。

* **member_name**(str, 可选): 成员标识符。默认值：`team_leader`。
* **display_name**(str, 可选): 显示名称。默认值：`Team Leader`。
* **persona**(str, 可选): 人设描述。默认值：`天才项目管理专家`。

## class TeamMemberSpec

预定义团队成员。用于 `TeamAgentSpec.predefined_members`。

* **member_name**(str): 成员标识符。
* **display_name**(str): 显示名称。
* **role_type**(TeamRole, 可选): `leader` 或 `teammate`。默认值：`teammate`。
* **persona**(str): 人设描述。
* **prompt_hint**(str, 可选): 初始提示。默认值：`None`。

## class TransportSpec

传输层配置。

* **type**(str): 后端类型 — `inprocess` 或 `pyzmq`。
* **params**(dict, 可选): 后端参数。默认值：`{}`。

## class StorageSpec

存储层配置。

* **type**(str): 存储类型 — `sqlite`、`postgresql` 或 `memory`。
* **params**(dict, 可选): 存储参数。默认值：`{}`。

## function create_agent_team

```python
create_agent_team(
    agents: dict[str, DeepAgentSpec],
    *,
    team_name: str = "agent_team",
    lifecycle: str = "temporary",
    teammate_mode: str = "build_mode",
    spawn_mode: str = "process",
    leader: Optional[LeaderSpec] = None,
    predefined_members: list[TeamMemberSpec] | None = None,
    transport: Optional[TransportSpec] = None,
    storage: Optional[StorageSpec] = None,
    worktree: Optional[WorktreeConfig] = None,
    metadata: Optional[dict] = None,
) -> TeamAgent
```

便捷函数，构造 `TeamAgentSpec` 并调用 `build()`。等效于 `TeamAgentSpec(...).build()`。

**参数：**

* **agents**(dict[str, [DeepAgentSpec](./agent_teams.md#class-deepagentspec)]): 按角色的 DeepAgentSpec 配置。必须包含 `"leader"` 键。
* **team_name**(str, 可选): 团队名称。默认值：`agent_team`。
* **lifecycle**(str, 可选): `temporary` 或 `persistent`。默认值：`temporary`。
* **teammate_mode**(str, 可选): `build_mode` 或 `plan_mode`。默认值：`build_mode`。
* **spawn_mode**(str, 可选): `process` 或 `inprocess`。默认值：`process`。
* **leader**([LeaderSpec](./agent_teams.md#class-leaderspec), 可选): Leader 身份。默认值：`None`。
* **predefined_members**(list[[TeamMemberSpec](./agent_teams.md#class-teammemberspec)], 可选): 预配置成员。默认值：`None`。
* **transport**([TransportSpec](./agent_teams.md#class-transportspec), 可选): 传输配置。默认值：`None`。
* **storage**([StorageSpec](./agent_teams.md#class-storagespec), 可选): 存储配置。默认值：`None`。
* **worktree**(WorktreeConfig, 可选): Worktree 隔离。默认值：`None`。
* **metadata**(dict, 可选): 元数据。默认值：`None`。

**返回：**

**TeamAgent**: 配置好的 leader 实例。

## function resume_persistent_team

```python
async resume_persistent_team(
    agent: TeamAgent,
    new_session_id: str,
) -> TeamAgent
```

在新会话中恢复持久团队。

**参数：**

* **agent**(TeamAgent): 已完成至少一轮的持久团队 leader。
* **new_session_id**(str): 新一轮的会话 ID。

**返回：**

**TeamAgent**: 同一 leader 实例，已就绪进入下一轮。