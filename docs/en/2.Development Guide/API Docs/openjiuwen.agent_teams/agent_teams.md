# openjiuwen.agent_teams

`openjiuwen.agent_teams` provides multi-agent team orchestration. Use `TeamAgentSpec.model_validate()` to load from config, then `build()` to create a `TeamAgent`.

## class TeamAgentSpec

JSON-serializable specification for constructing a TeamAgent. Composes per-role DeepAgentSpecs with team-level configuration.

* **agents**(dict[str, [DeepAgentSpec](./agent_teams.md#class-deepagentspec)]): Per-role DeepAgentSpec configs. Must contain `"leader"` key; `"teammate"` is optional and falls back to leader config.
* **team_name**(str, optional): Team name. Default: `agent_team`.
* **lifecycle**(str, optional): Team lifecycle — `temporary` (disband after completion) or `persistent` (retain across sessions). Default: `temporary`.
* **teammate_mode**(str, optional): Teammate execution mode — `build_mode` (complete tasks directly) or `plan_mode` (require leader approval). Default: `build_mode`.
* **spawn_mode**(str, optional): How teammates are launched — `process` (subprocess) or `inprocess` (same event loop). Default: `process`.
* **leader**([LeaderSpec](./agent_teams.md#class-leaderspec), optional): Leader identity config. Default: `LeaderSpec()`.
* **predefined_members**(list[[TeamMemberSpec](./agent_teams.md#class-teammemberspec)], optional): Pre-configured members. When provided, leader skips `spawn_member` tool. Default: `[]`.
* **transport**([TransportSpec](./agent_teams.md#class-transportspec), optional): Transport layer config. Default: `None`.
* **storage**([StorageSpec](./agent_teams.md#class-storagespec), optional): Storage layer config. Default: `None`.
* **worktree**(WorktreeConfig, optional): Worktree isolation config for team members. Default: `None`.
* **workspace**(TeamWorkspaceConfig, optional): Shared workspace config for team members. Default: `None`.
* **metadata**(dict[str, Any], optional): Additional metadata. Default: `{}`.

### model_validate

```python
model_validate(data: dict) -> TeamAgentSpec
```

Parse from dict/JSON. Inherited from Pydantic BaseModel.

**Parameters:**

* **data**(dict): Config dict, typically loaded from YAML/JSON file.

**Returns:**

**TeamAgentSpec**: Parsed specification instance.

**Example:**

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

Materialize the configured TeamAgent instance.

**Returns:**

**TeamAgent**: A configured leader instance ready for execution.

**Example:**

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

JSON-serializable configuration for a single DeepAgent. Used in `TeamAgentSpec.agents` dict.

* **model**(TeamModelConfig, optional): LLM model config. Default: `None`.
* **card**([AgentCard](../openjiuwen.core/single_agent/single_agent.md#class-agentcard), optional): Agent identity card. Default: `None`.
* **system_prompt**(str, optional): Custom system prompt. Default: `None`.
* **tools**(list[ToolCard | BuiltinToolSpec], optional): Tool list. Default: `None`.
* **mcps**(list[McpServerConfig], optional): MCP server configs. Default: `None`.
* **subagents**(list[SubAgentSpec], optional): Sub-agent configs. Default: `None`.
* **rails**(list[RailSpec], optional): Guardrail configs. Default: `None`.
* **enable_task_loop**(bool, optional): Enable task iteration loop. Default: `False`.
* **enable_async_subagent**(bool, optional): Enable async subagent execution. Default: `False`.
* **add_general_purpose_agent**(bool, optional): Add general-purpose subagent. Default: `False`.
* **max_iterations**(int, optional): Max loop iterations. Default: `15`.
* **workspace**(WorkspaceSpec, optional): Workspace config. Default: `None`.
* **skills**(list[str], optional): Skill names. Default: `None`.
* **sys_operation**(SysOperationSpec, optional): System operation config. Default: `None`.
* **language**(str, optional): Language (`cn` or `en`). Default: `None`.
* **prompt_mode**(str, optional): Prompt mode. Default: `None`.
* **vision_model**(VisionModelSpec, optional): Vision model config. Default: `None`.
* **audio_model**(AudioModelSpec, optional): Audio model config. Default: `None`.
* **enable_task_planning**(bool, optional): Enable task planning rail. Default: `False`.
* **restrict_to_sandbox**(bool, optional): Restrict file operations to sandbox. Default: `False`.
* **auto_create_workspace**(bool, optional): Auto-create workspace directory. Default: `True`.
* **completion_timeout**(float, optional): Timeout in seconds. Default: `600.0`.
* **progressive_tool**(ProgressiveToolSpec, optional): Progressive tool loading config. Default: `None`.
* **approval_required_tools**(list[str], optional): Tool names requiring leader approval (teammates only). Default: `None`.

## class LeaderSpec

Leader identity configuration.

* **member_name**(str, optional): Member identifier. Default: `team_leader`.
* **display_name**(str, optional): Display name. Default: `Team Leader`.
* **persona**(str, optional): Persona description. Default: `天才项目管理专家`.

## class TeamMemberSpec

Pre-defined team member. Used in `TeamAgentSpec.predefined_members`.

* **member_name**(str): Member identifier.
* **display_name**(str): Display name.
* **role_type**(TeamRole, optional): `leader` or `teammate`. Default: `teammate`.
* **persona**(str): Persona description.
* **prompt_hint**(str, optional): Initial prompt hint. Default: `None`.

## class TransportSpec

Transport layer configuration.

* **type**(str): Backend type — `inprocess` or `pyzmq`.
* **params**(dict, optional): Backend-specific parameters. Default: `{}`.

## class StorageSpec

Storage layer configuration.

* **type**(str): Storage type — `sqlite` or `memory`.
* **params**(dict, optional): Storage-specific parameters. Default: `{}`.

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

Convenience function that constructs a `TeamAgentSpec` and calls `build()`. Equivalent to `TeamAgentSpec(...).build()`.

**Parameters:**

* **agents**(dict[str, [DeepAgentSpec](./agent_teams.md#class-deepagentspec)]): Per-role DeepAgentSpec configs. Must contain `"leader"` key.
* **team_name**(str, optional): Team name. Default: `agent_team`.
* **lifecycle**(str, optional): `temporary` or `persistent`. Default: `temporary`.
* **teammate_mode**(str, optional): `build_mode` or `plan_mode`. Default: `build_mode`.
* **spawn_mode**(str, optional): `process` or `inprocess`. Default: `process`.
* **leader**([LeaderSpec](./agent_teams.md#class-leaderspec), optional): Leader identity. Default: `None`.
* **predefined_members**(list[[TeamMemberSpec](./agent_teams.md#class-teammemberspec)], optional): Pre-configured members. Default: `None`.
* **transport**([TransportSpec](./agent_teams.md#class-transportspec), optional): Transport config. Default: `None`.
* **storage**([StorageSpec](./agent_teams.md#class-storagespec), optional): Storage config. Default: `None`.
* **worktree**(WorktreeConfig, optional): Worktree isolation. Default: `None`.
* **metadata**(dict, optional): Metadata. Default: `None`.

**Returns:**

**TeamAgent**: A configured leader instance.

## function resume_persistent_team

```python
async resume_persistent_team(
    agent: TeamAgent,
    new_session_id: str,
) -> TeamAgent
```

Resume a persistent team in a new session.

**Parameters:**

* **agent**(TeamAgent): A persistent-team leader that has completed at least one round.
* **new_session_id**(str): Session ID for the new round.

**Returns:**

**TeamAgent**: The same leader instance, ready for the next round.