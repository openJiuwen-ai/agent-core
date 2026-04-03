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

Creates and configures a team Leader.

**Parameters**:

- **agents** (dict[str, [DeepAgentSpec](#class-openjiuwenagent_teamsdeepagentspec)]): Role-based [DeepAgentSpec](#class-openjiuwenagent_teamsdeepagentspec) configuration. Must contain the `"leader"` key; `"teammate"` is optional.
- **team_name** (str, optional): Team name. Default: `agent_team`.
- **lifecycle** (str, optional): Team lifecycle mode, either `temporary` or `persistent`. Default: `temporary`.
- **teammate_mode** (str, optional): Default execution mode for teammates, either `plan_mode` or `build_mode`. Default: `plan_mode`.
- **leader** ([LeaderSpec](#class-openjiuwenagent_teamsleaderspec), optional): Leader identity configuration. Default: `None`.
- **predefined_members** (list[TeamMemberSpec] | None, optional): Pre-configured team members. When provided, leader skips `spawn_member` and `build_team` registers all members automatically. Default: `None`.
- **transport** ([TransportSpec](#class-openjiuwenagent_teamstransportspec), optional): Transport layer configuration. Default: `None`.
- **storage** ([StorageSpec](#class-openjiuwenagent_teamsstoragespec), optional): Storage layer configuration. Default: `None`.
- **metadata** (dict, optional): Additional metadata. Default: `None`.

**Returns**:

**TeamAgent**: A configured Leader instance.

## function openjiuwen.agent_teams.resume_persistent_team

```python
async resume_persistent_team(
    agent: TeamAgent,
    new_session_id: str,
) -> TeamAgent
```

Resume a persistent team in a new session.

Creates a fresh session, initializes new dynamic tables for tasks and messages, and returns the same agent ready for a new `invoke()` / `stream()` call.

**Parameters**:

- **agent** (TeamAgent): A configured persistent-team leader that has completed at least one round.
- **new_session_id** (str): Session ID for the new round.

**Returns**:

**TeamAgent**: The same leader instance, ready for the next round.

## class openjiuwen.agent_teams.DeepAgentSpec

JSON-serializable configuration used to construct a `DeepAgent`.

**Attributes**:

- **model** ([TeamModelConfig](./schema/deep_agent_spec.md#openjiuwenagent_teamsteammodelconfig), optional): Model configuration. Default: `None`.
- **card** ([AgentCard](../openjiuwen.core/single_agent/single_agent.md#class-openjiuwencoresingle_agentagentcard), optional): Agent card. Default: `None`.
- **system_prompt** (str, optional): System prompt. Default: `None`.
- **tools** (list[[ToolCard](../openjiuwen.core/foundation/tool/tool.md#class-toolcard)], optional): Tool list. Default: `None`.
- **mcps** (list[McpServerConfig], optional): MCP server configuration list. Default: `None`.
- **subagents** (list[[SubAgentSpec](./schema/schema.md#class-openjiuwenagent_teamsschemasubagentspec)], optional): Sub-agent configuration list. Default: `None`.
- **rails** (list[[RailSpec](./schema/schema.md#class-openjiuwenagent_teamsschemarailspec)], optional): Guardrail configuration list. Default: `None`.
- **stop_condition** ([StopConditionSpec](./schema/schema.md#class-openjiuwenagent_teamsschemastopconditionspec), optional): Stop condition configuration. Default: `None`.
- **enable_task_loop** (bool): Whether to enable the task loop. Default: `False`.
- **max_iterations** (int): Maximum number of iterations. Default: `15`.
- **workspace** ([WorkspaceSpec](./schema/schema.md#class-openjiuwenagent_teamsschemaworkspacespec), optional): Workspace configuration. Default: `None`.
- **skills** (list[str], optional): Skill list. Default: `None`.
- **sys_operation** ([SysOperationSpec](./schema/schema.md#class-openjiuwenagent_teamsschemasysoperationspec), optional): System operation configuration. Default: `None`.
- **language** (str, optional): Language setting. Default: `None`.
- **prompt_mode** (str, optional): Prompt mode. Default: `None`.
- **vision_model** ([VisionModelSpec](./schema/schema.md#class-openjiuwenagent_teamsschemavisionmodelspec), optional): Vision model configuration. Default: `None`.
- **audio_model** ([AudioModelSpec](./schema/schema.md#class-openjiuwenagent_teamsschemaaudiomodelspec), optional): Audio model configuration. Default: `None`.
- **enable_task_planning** (bool): Whether to enable task planning. Default: `False`.
- **completion_timeout** (float): Completion timeout in seconds. Default: `600.0`.
- **progressive_tool** ([ProgressiveToolSpec](./schema/schema.md#class-openjiuwenagent_teamsschemaprogressivetoolspec), optional): Progressive tool exposure configuration. Default: `None`.

## class openjiuwen.agent_teams.TransportSpec

Pluggable transport layer configuration.

**Attributes**:

- **type** (str): Backend type, such as `"pyzmq"`.
- **params** (dict, optional): Backend parameters. Default: `{}`.

## class openjiuwen.agent_teams.StorageSpec

Pluggable storage layer configuration.

**Attributes**:

- **type** (str): Storage type, such as `"sqlite"`.
- **params** (dict): Storage parameters. Default: `{}`.

## class openjiuwen.agent_teams.LeaderSpec

Leader identity configuration.

**Attributes**:

- **member_id** (str, optional): Member ID. Default: `team_leader`.
- **name** (str, optional): Name. Default: `TeamLeader`.
- **persona** (str, optional): Persona. Default: `Genius project management expert`.
- **domain** (str, optional): Domain. Default: `project_management`.

## class openjiuwen.agent_teams.MessagerTransportConfig

Message transport configuration.

**Attributes**:

- **backend** (str, optional): Backend type. Default: `team_runtime`.
- **team_id** (str, optional): Team ID. Default: `default`.
- **node_id** (str, optional): Node ID. Default: `None`.
- **direct_addr** (str, optional): Direct communication address. Default: `None`.
- **pubsub_publish_addr** (str, optional): Publish address. Default: `None`.
- **pubsub_subscribe_addr** (str, optional): Subscribe address. Default: `None`.
- **listen_addrs** (list[str], optional): List of listen addresses. Default: `[]`.
- **bootstrap_peers** (list[[MessagerPeerConfig](#class-openjiuwenagent_teamsmessagerpeerconfig)], optional): Bootstrap peer list. Default: `[]`.
- **known_peers** (list[[MessagerPeerConfig](#class-openjiuwenagent_teamsmessagerpeerconfig)], optional): Known peer list. Default: `[]`.
- **request_timeout** (float, optional): Request timeout in seconds. Default: `10.0`.
- **metadata** (dict[str, Any], optional): Metadata dictionary. Default: `{}`.

## class openjiuwen.agent_teams.MessagerPeerConfig

Metadata for a node in the message transport layer.

**Attributes**:

- **agent_id** (str): Agent ID.
- **peer_id** (str, optional): Peer ID. Default: `None`.
- **addrs** (list[str]): Address list. Default: `[]`.
- **metadata** (dict[str, Any]): Metadata dictionary. Default: `{}`.
