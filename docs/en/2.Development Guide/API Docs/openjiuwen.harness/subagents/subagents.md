# openjiuwen.harness.subagents

Factory functions for creating pre-configured sub-agents. Each factory returns a [`DeepAgent`](../deep_agent.md#class-openjiuwenharnessdeepagent) with a role-specific tool set, system prompt, and configuration.

Sub-agents are registered through the `subagents` parameter in [`DeepAgentConfig`](../schema/config.md#class-openjiuwenharnessschemadeepagentconfig) or [`create_deep_agent`](../factory.md#function-openjiuwenharnesscreate_deep_agent).

---

## function openjiuwen.harness.subagents.create_browser_agent

```python
create_browser_agent(
    model: str | BaseChatModel,
    *,
    card: AgentCard | None = None,
    system_prompt: str | None = None,
    tools: list[ToolCard] | None = None,
    mcps: list[McpServerConfig] | None = None,
    subagents: list[SubAgentConfig] | None = None,
    rails: list[Rail] | None = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Workspace | str | None = None,
    skills: list[str] | None = None,
    backend: str | None = None,
    sys_operation: SysOperation | None = None,
    language: str | None = None,
    prompt_mode: str | PromptMode | None = None,
    **config_kwargs,
) -> DeepAgent
```

Create a sub-agent equipped with Playwright browser automation tools for web interaction tasks such as navigating pages, filling forms, clicking elements, and extracting data.

**Parameters**:

- **model** (str | BaseChatModel): LLM model name or instance.
- **card** (AgentCard, optional): Agent identity card. Default: `None`.
- **system_prompt** (str, optional): System prompt override. Default: `None`.
- **tools** (list[ToolCard], optional): Additional tools. Default: `None`.
- **mcps** (list[McpServerConfig], optional): MCP server configurations. Default: `None`.
- **subagents** (list[[SubAgentConfig](../schema/config.md#class-openjiuwenharnessschemasubagentconfig)], optional): Nested sub-agents. Default: `None`.
- **rails** (list[Rail], optional): Guardrails. Default: `None`.
- **enable_task_loop** (bool, optional): Enable the task loop. Default: `False`.
- **max_iterations** (int, optional): Maximum iterations. Default: `15`.
- **workspace** ([Workspace](../workspace/workspace.md#class-openjiuwenharnessworkspaceworkspace) | str, optional): Workspace. Default: `None`.
- **skills** (list[str], optional): Skills. Default: `None`.
- **backend** (str, optional): LLM backend. Default: `None`.
- **sys_operation** (SysOperation, optional): System operation. Default: `None`.
- **language** (str, optional): Language code. Default: `None`.
- **prompt_mode** (str | PromptMode, optional): Prompt mode. Default: `None`.
- ****config_kwargs**: Additional configuration arguments.

**Returns**:

**[DeepAgent](../deep_agent.md#class-openjiuwenharnessdeepagent)**: A configured browser sub-agent.

---

## function openjiuwen.harness.subagents.create_code_agent

```python
create_code_agent(
    model: str | BaseChatModel,
    *,
    card: AgentCard | None = None,
    system_prompt: str | None = None,
    tools: list[ToolCard] | None = None,
    mcps: list[McpServerConfig] | None = None,
    subagents: list[SubAgentConfig] | None = None,
    rails: list[Rail] | None = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Workspace | str | None = None,
    skills: list[str] | None = None,
    backend: str | None = None,
    sys_operation: SysOperation | None = None,
    language: str | None = None,
    prompt_mode: str | PromptMode | None = None,
    **config_kwargs,
) -> DeepAgent
```

Create a sub-agent specialized for coding tasks. Comes pre-configured with file system tools, bash execution, code analysis, and workspace management.

**Parameters**:

- **model** (str | BaseChatModel): LLM model name or instance.
- **card** (AgentCard, optional): Agent identity card. Default: `None`.
- **system_prompt** (str, optional): System prompt override. Default: `None`.
- **tools** (list[ToolCard], optional): Additional tools. Default: `None`.
- **mcps** (list[McpServerConfig], optional): MCP server configurations. Default: `None`.
- **subagents** (list[[SubAgentConfig](../schema/config.md#class-openjiuwenharnessschemasubagentconfig)], optional): Nested sub-agents. Default: `None`.
- **rails** (list[Rail], optional): Guardrails. Default: `None`.
- **enable_task_loop** (bool, optional): Enable the task loop. Default: `False`.
- **max_iterations** (int, optional): Maximum iterations. Default: `15`.
- **workspace** ([Workspace](../workspace/workspace.md#class-openjiuwenharnessworkspaceworkspace) | str, optional): Workspace. Default: `None`.
- **skills** (list[str], optional): Skills. Default: `None`.
- **backend** (str, optional): LLM backend. Default: `None`.
- **sys_operation** (SysOperation, optional): System operation. Default: `None`.
- **language** (str, optional): Language code. Default: `None`.
- **prompt_mode** (str | PromptMode, optional): Prompt mode. Default: `None`.
- ****config_kwargs**: Additional configuration arguments.

**Returns**:

**[DeepAgent](../deep_agent.md#class-openjiuwenharnessdeepagent)**: A configured code sub-agent.

---

## function openjiuwen.harness.subagents.create_research_agent

```python
create_research_agent(
    model: str | BaseChatModel,
    *,
    card: AgentCard | None = None,
    system_prompt: str | None = None,
    tools: list[ToolCard] | None = None,
    mcps: list[McpServerConfig] | None = None,
    subagents: list[SubAgentConfig] | None = None,
    rails: list[Rail] | None = None,
    enable_task_loop: bool = False,
    max_iterations: int = 15,
    workspace: Workspace | str | None = None,
    skills: list[str] | None = None,
    backend: str | None = None,
    sys_operation: SysOperation | None = None,
    language: str | None = None,
    prompt_mode: str | PromptMode | None = None,
    **config_kwargs,
) -> DeepAgent
```

Create a sub-agent specialized for research tasks. Comes pre-configured with web search, web fetch, file reading, and information synthesis tools.

**Parameters**:

- **model** (str | BaseChatModel): LLM model name or instance.
- **card** (AgentCard, optional): Agent identity card. Default: `None`.
- **system_prompt** (str, optional): System prompt override. Default: `None`.
- **tools** (list[ToolCard], optional): Additional tools. Default: `None`.
- **mcps** (list[McpServerConfig], optional): MCP server configurations. Default: `None`.
- **subagents** (list[[SubAgentConfig](../schema/config.md#class-openjiuwenharnessschemasubagentconfig)], optional): Nested sub-agents. Default: `None`.
- **rails** (list[Rail], optional): Guardrails. Default: `None`.
- **enable_task_loop** (bool, optional): Enable the task loop. Default: `False`.
- **max_iterations** (int, optional): Maximum iterations. Default: `15`.
- **workspace** ([Workspace](../workspace/workspace.md#class-openjiuwenharnessworkspaceworkspace) | str, optional): Workspace. Default: `None`.
- **skills** (list[str], optional): Skills. Default: `None`.
- **backend** (str, optional): LLM backend. Default: `None`.
- **sys_operation** (SysOperation, optional): System operation. Default: `None`.
- **language** (str, optional): Language code. Default: `None`.
- **prompt_mode** (str | PromptMode, optional): Prompt mode. Default: `None`.
- ****config_kwargs**: Additional configuration arguments.

**Returns**:

**[DeepAgent](../deep_agent.md#class-openjiuwenharnessdeepagent)**: A configured research sub-agent.
