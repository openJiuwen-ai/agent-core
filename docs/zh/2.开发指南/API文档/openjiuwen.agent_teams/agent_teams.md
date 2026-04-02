# openjiuwen.agent_teams

## function openjiuwen.agent_teams.create_agent_team

```python
create_agent_team(
    agents: dict[str, DeepAgentSpec],
    *,
    team_name: str = "agent_team",
    lifecycle: str = "temporary",
    teammate_mode: str = "plan_mode",
    leader: Optional[LeaderSpec] = None,
    predefined_members: list[TeamMemberSpec] | None = None,
    transport: Optional[TransportSpec] = None,
    storage: Optional[StorageSpec] = None,
    metadata: Optional[dict] = None,
) -> TeamAgent
```

创建并配置一个团队 Leader。

**参数**：

- **agents**(dict[str, [DeepAgentSpec](#class-openjiuwenagent_teamsdeepagentspec)]): 按角色配置的[DeepAgentSpec](#class-openjiuwenagent_teamsdeepagentspec)，必须包含 "leader" 键，"teammate" 为可选。
- **team_name**(str，可选): 团队名称，默认值：`agent_team`。
- **lifecycle**(str，可选): 团队生命周期模式，`temporary` 或 `persistent`，默认值：`temporary`。
- **teammate_mode**(str，可选): 队友默认执行模式，`plan_mode` 或 `build_mode`，默认值：`plan_mode`。
- **leader**([LeaderSpec](#class-openjiuwenagent_teamsleaderspec)，可选): Leader 身份配置，默认值：`None`。
- **predefined_members**(list[TeamMemberSpec] | None，可选): 预配置的团队成员。提供时 leader 跳过 `spawn_member`，`build_team` 自动注册所有成员，默认值：`None`。
- **transport**([TransportSpec](#class-openjiuwenagent_teamstransportspec)，可选): 传输层配置，默认值：`None`。
- **storage**([StorageSpec](#class-openjiuwenagent_teamsstoragespec)，可选): 存储层配置，默认值：`None`。
- **metadata**(dict，可选): 附加元数据，默认值：`None`。

**返回**：

**TeamAgent**：配置好的 Leader 实例

## function openjiuwen.agent_teams.resume_persistent_team

```python
async resume_persistent_team(
    agent: TeamAgent,
    new_session_id: str,
) -> TeamAgent
```

在新会话中恢复持久团队。

创建新会话，初始化任务和消息的动态表，返回同一 agent 实例，可直接进行下一轮 `invoke()` / `stream()` 调用。

**参数**：

- **agent**(TeamAgent): 已完成至少一轮的持久团队 leader 实例。
- **new_session_id**(str): 新一轮的会话 ID。

**返回**：

**TeamAgent**：同一 leader 实例，已就绪进入下一轮。

## class openjiuwen.agent_teams.DeepAgentSpec

用于构造 DeepAgent 的 JSON 可序列化配置。

**属性**：

- **model** ([TeamModelConfig](#openjiuwenagent_teamsteammodelconfig)，可选): 模型配置，默认值：`None`。
- **card** ([AgentCard](../openjiuwen.core/single_agent/single_agent.md#class-openjiuwencoresingle_agentagentcard)，可选): 代理卡片，默认值：`None`。
- **system_prompt** (str，可选): 系统提示词，默认值：`None`。
- **tools** (list[[ToolCard](../openjiuwen.core/foundation/tool/tool.md#class-toolcard)]，可选): 工具列表，默认值：`None`。
- **mcps** (list[McpServerConfig]，可选): MCP 服务器配置列表，默认值：`None`。
- **subagents** (list[[SubAgentSpec](./schema/schema.md#class-openjiuwenagent_teamsschemasubagentspec)]，可选): 子Agent配置列表，默认值：`None`。
- **rails** (list[[RailSpec](./schema/schema.md#class-openjiuwenagent_teamsschemarailspec)]，可选): 护栏配置列表，默认值：`None`。
- **stop_condition** ([StopConditionSpec](./schema/schema.md#class-openjiuwenagent_teamsschemastopconditionspec)，可选): 停止条件配置，默认值：`None`。
- **enable_task_loop** (bool): 是否启用任务循环，默认值：`False`。
- **max_iterations** (int): 最大迭代次数，默认值：`15`。
- **workspace** ([WorkspaceSpec](./schema/schema.md#class-openjiuwenagent_teamsschemaworkspacespec)，可选): 工作空间配置，默认值：`None`。
- **skills** (list[str]，可选): 技能列表，默认值：`None`。
- **sys_operation** ([SysOperationSpec](./schema/schema.md#class-openjiuwenagent_teamsschemasysoperationspec)，可选): 系统操作配置，默认值：`None`。
- **language** (str，可选): 语言设置，默认值：`None`。
- **prompt_mode** (str，可选): 提示词模式，默认值：`None`。
- **vision_model** ([VisionModelSpec](./schema/schema.md#class-openjiuwenagent_teamsschemavisionmodelspec)，可选): 视觉模型配置，默认值：`None`。
- **audio_model** ([AudioModelSpec](./schema/schema.md#class-openjiuwenagent_teamsschemaaudiomodelspec)，可选): 音频模型配置，默认值：`None`。
- **enable_task_planning** (bool): 是否启用任务规划，默认值：`False`。
- **completion_timeout** (float): 完成超时时间（秒），默认值：`600.0`。
- **progressive_tool** ([ProgressiveToolSpec](./schema/schema.md#class-openjiuwenagent_teamsschemaprogressivetoolspec)，可选): 渐进式工具暴露配置，默认值：`None`。

## class openjiuwen.agent_teams.TransportSpec

可插拔的传输层配置。

**属性**：

- **type** (str): 后端类型（如 "pyzmq"）。
- **params** (dict，可选): 后端参数，默认值：`{}`。

## class openjiuwen.agent_teams.StorageSpec

可插拔的存储层配置。

**属性**：

- **type** (str): 存储类型（如 "sqlite"）。
- **params** (dict): 存储参数，默认值：`{}`。

## class openjiuwen.agent_teams.LeaderSpec

Leader 身份配置。

**属性**：

- **member_id** (str，可选): 成员 ID，默认值：`team_leader`。
- **name** (str，可选): 名称，默认值：`TeamLeader`。
- **persona** (str，可选): 人设，默认值：`天才项目管理专家`。
- **domain** (str，可选): 领域，默认值：`project_management`。

## class openjiuwen.agent_teams.MessagerTransportConfig

消息传输配置。

**属性**：

- **backend** (str，可选): 后端类型，默认值：`team_runtime`。
- **team_id** (str，可选): 团队 ID，默认值：`default`。
- **node_id** (str，可选): 节点 ID，默认值：`None`。
- **direct_addr** (str，可选): 直接通信地址，默认值：`None`。
- **pubsub_publish_addr** (str，可选): 发布地址，默认值：`None`。
- **pubsub_subscribe_addr** (str，可选): 订阅地址，默认值：`None`。
- **listen_addrs** (list[str]，可选): 监听地址列表，默认值：`[]`。
- **bootstrap_peers** (list[[MessagerPeerConfig](#class-openjiuwenagent_teamsmessagerpeerconfig)]，可选): 启动节点列表，默认值：`[]`。
- **known_peers** (list[[MessagerPeerConfig](#class-openjiuwenagent_teamsmessagerpeerconfig)]，可选): 已知节点列表，默认值：`[]`。
- **request_timeout** (float，可选): 请求超时时间（秒），默认值：`10.0`。
- **metadata** (dict[str, Any]，可选): 元数据字典，默认值：`{}`。

## class openjiuwen.agent_teams.MessagerPeerConfig

用于配置消息传输层的节点元数据。

**属性**：

- **agent_id** (str): Agent ID。
- **peer_id** (str，可选): 节点ID，默认值：`None`。
- **addrs** (list[str]): 地址列表，默认值：`[]`。
- **metadata** (dict[str, Any]): 元数据字典，默认值：`{}`。
