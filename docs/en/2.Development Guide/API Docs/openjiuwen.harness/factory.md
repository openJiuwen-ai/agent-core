# openjiuwen.harness.factory

## function openjiuwen.harness.create_deep_agent

```python
create_deep_agent(
    model: str | BaseChatModel,
    *,
    card: AgentCard | None = None,
    system_prompt: str | None = None,
    tools: list[ToolCard] | None = None,
    mcps: list[McpServerConfig] | None = None,
    subagents: list[SubAgentConfig] | None = None,
    rails: list[Rail] | None = None,
    enable_task_loop: bool = False,
    enable_async_subagent: bool = False,
    add_general_purpose_agent: bool = False,
    max_iterations: int = 15,
    workspace: Workspace | str | None = None,
    skills: list[str] | None = None,
    backend: str | None = None,
    sys_operation: SysOperation | None = None,
    language: str | None = None,
    prompt_mode: str | PromptMode | None = None,
    vision_model_config: VisionModelConfig | None = None,
    audio_model_config: AudioModelConfig | None = None,
    enable_task_planning: bool = False,
    restrict_to_work_dir: bool = True,
    **config_kwargs,
) -> DeepAgent
```

Convenience factory that builds and configures a [`DeepAgent`](./deep_agent.md#class-openjiuwenharnessdeepagent) in one call.

Constructs a `DeepAgentConfig` from the provided arguments, instantiates a `DeepAgent`, and calls `configure()` on it.

**Parameters**:

- **model** (str | BaseChatModel): LLM model name or a pre-built model instance.
- **card** ([AgentCard](../openjiuwen.core/single_agent/single_agent.md#class-openjiuwencoresingle_agentagentcard), optional): Agent identity card. Default: `None` (a default card is created).
- **system_prompt** (str, optional): System prompt override. Default: `None` (assembled by the prompt builder).
- **tools** (list[[ToolCard](../openjiuwen.core/foundation/tool/tool.md#class-toolcard)], optional): Additional tool cards. Default: `None`.
- **mcps** (list[McpServerConfig], optional): MCP server configurations. Default: `None`.
- **subagents** (list[[SubAgentConfig](./schema/config.md#class-openjiuwenharnessschemasubagentconfig)], optional): Sub-agent configurations. Default: `None`.
- **rails** (list[Rail], optional): Guardrails to register. Default: `None`.
- **enable_task_loop** (bool, optional): Enable the autonomous task loop. Default: `False`.
- **enable_async_subagent** (bool, optional): Allow sub-agents to run asynchronously. Default: `False`.
- **add_general_purpose_agent** (bool, optional): Automatically add a general-purpose sub-agent. Default: `False`.
- **max_iterations** (int, optional): Maximum task-loop iterations. Default: `15`.
- **workspace** ([Workspace](./workspace/workspace.md#class-openjiuwenharnessworkspaceworkspace) | str, optional): Workspace instance or root path string. Default: `None`.
- **skills** (list[str], optional): Skill identifiers to load. Default: `None`.
- **backend** (str, optional): LLM backend identifier. Default: `None`.
- **sys_operation** (SysOperation, optional): System operation instance for shell/file access. Default: `None`.
- **language** (str, optional): Language code (e.g. `"en"`, `"cn"`). Default: `None`.
- **prompt_mode** (str | [PromptMode](./prompts/prompts.md#enum-openjiuwenharnesspromptspromptmode), optional): Prompt assembly mode. Default: `None`.
- **vision_model_config** ([VisionModelConfig](./schema/config.md#class-openjiuwenharnessschemavisionmodelconfig), optional): Vision model configuration. Default: `None`.
- **audio_model_config** ([AudioModelConfig](./schema/config.md#class-openjiuwenharnessschemaudiomodelconfig), optional): Audio model configuration. Default: `None`.
- **enable_task_planning** (bool, optional): Enable the task-planning rail. Default: `False`.
- **restrict_to_work_dir** (bool, optional): Restrict file operations to the workspace directory. Default: `True`.
- ****config_kwargs**: Additional keyword arguments forwarded to `DeepAgentConfig`.

**Returns**:

**[DeepAgent](./deep_agent.md#class-openjiuwenharnessdeepagent)**: A fully configured `DeepAgent` instance, ready for `invoke()` or `stream()`.
