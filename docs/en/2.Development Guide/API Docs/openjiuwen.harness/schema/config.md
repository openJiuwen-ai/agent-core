# openjiuwen.harness.schema.config

## class openjiuwen.harness.schema.VisionModelConfig

Configuration for a vision-capable model used by image tools.

**Attributes**:

- **api_key** (str, optional): API key. Default: empty string.
- **base_url** (str, optional): Base URL. Default: `https://api.openai.com/v1`.
- **model** (str, optional): Model name. Default: `gpt-4.1-mini`.
- **max_retries** (int, optional): Maximum retry count. Default: `3`.

### classmethod from_env

```python
@classmethod
from_env() -> VisionModelConfig
```

Create a `VisionModelConfig` from environment variables (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, etc.).

**Returns**:

**VisionModelConfig**: A config instance populated from the environment.

---

## class openjiuwen.harness.schema.AudioModelConfig

Configuration for audio transcription and question-answering models.

**Attributes**:

- **api_key** (str): API key. Default: empty string.
- **base_url** (str): Base URL. Default: `https://api.openai.com/v1`.
- **transcription_model** (str): Transcription model name. Default: `gpt-4o-transcribe`.
- **question_answering_model** (str): Audio question-answering model name. Default: `gpt-4o-audio-preview`.
- **max_retries** (int): Maximum retry count. Default: `3`.
- **http_timeout** (int): HTTP timeout in seconds. Default: `20`.
- **max_audio_bytes** (int): Maximum audio file size in bytes. Default: `25 * 1024 * 1024` (25 MB).
- **acr_access_key** (str): ACRCloud access key. Default: empty string.
- **acr_access_secret** (str): ACRCloud access secret. Default: empty string.
- **acr_base_url** (str): ACRCloud base URL. Default: `https://identify-ap-southeast-1.acrcloud.com/v1/identify`.

### classmethod from_env

```python
@classmethod
from_env() -> AudioModelConfig
```

Create an `AudioModelConfig` from environment variables.

**Returns**:

**AudioModelConfig**: A config instance populated from the environment.

---

## class openjiuwen.harness.schema.DeepAgentConfig

Full configuration dataclass for a [`DeepAgent`](../deep_agent.md#class-openjiuwenharnessdeepagent).

**Attributes**:

- **model** (str | BaseChatModel): LLM model name or instance.
- **card** ([AgentCard](../../openjiuwen.core/single_agent/single_agent.md#class-openjiuwencoresingle_agentagentcard), optional): Agent identity card. Default: `None`.
- **system_prompt** (str, optional): System prompt override. Default: `None`.
- **enable_task_loop** (bool): Enable the autonomous task loop. Default: `False`.
- **enable_async_subagent** (bool): Allow sub-agents to run asynchronously. Default: `False`.
- **add_general_purpose_agent** (bool): Automatically add a general-purpose sub-agent. Default: `False`.
- **max_iterations** (int): Maximum task-loop iterations. Default: `15`.
- **subagents** (list[[SubAgentConfig](#class-openjiuwenharnessschemasubagentconfig)], optional): Sub-agent configuration list. Default: `None`.
- **tools** (list[[ToolCard](../../openjiuwen.core/foundation/tool/tool.md#class-toolcard)], optional): Tool cards. Default: `None`.
- **mcps** (list[McpServerConfig], optional): MCP server configurations. Default: `None`.
- **workspace** ([Workspace](../workspace/workspace.md#class-openjiuwenharnessworkspaceworkspace) | str, optional): Workspace instance or root path. Default: `None`.
- **skills** (list[str], optional): Skill identifiers. Default: `None`.
- **backend** (str, optional): LLM backend identifier. Default: `None`.
- **sys_operation** (SysOperation, optional): System operation instance. Default: `None`.
- **completion_timeout** (float): Timeout in seconds for a single completion call. Default: `600.0`.
- **language** (str, optional): Language code. Default: `None`.
- **prompt_mode** (str | [PromptMode](../prompts/prompts.md#enum-openjiuwenharnesspromptspromptmode), optional): Prompt assembly mode. Default: `None`.
- **vision_model_config** ([VisionModelConfig](#class-openjiuwenharnessschemavisionmodelconfig), optional): Vision model configuration. Default: `None`.
- **audio_model_config** ([AudioModelConfig](#class-openjiuwenharnessschemaudiomodelconfig), optional): Audio model configuration. Default: `None`.
- **rails** (list[Rail], optional): Guardrails. Default: `None`.
- **progressive_tool_enabled** (bool, optional): Enable progressive tool exposure. Default: `None`.
- **progressive_tool_always_visible_tools** (list[str], optional): Tools always visible to the model. Default: `None`.
- **progressive_tool_default_visible_tools** (list[str], optional): Tools visible by default. Default: `None`.
- **progressive_tool_max_loaded_tools** (int): Maximum number of concurrently loaded tools. Default: `12`.

---

## class openjiuwen.harness.schema.SubAgentConfig

Configuration for a sub-agent spawned by a parent `DeepAgent`.

**Attributes**:

- **agent_card** ([AgentCard](../../openjiuwen.core/single_agent/single_agent.md#class-openjiuwencoresingle_agentagentcard)): Agent identity card for the sub-agent.
- **system_prompt** (str): System prompt for the sub-agent.
- **tools** (list[[ToolCard](../../openjiuwen.core/foundation/tool/tool.md#class-toolcard)], optional): Tool list. Default: `[]`.
- **mcps** (list[McpServerConfig], optional): MCP server configurations. Default: `[]`.
- **model** (str | BaseChatModel, optional): Model override. Default: `None` (inherits parent).
- **rails** (list[Rail], optional): Guardrails. Default: `None`.
- **skills** (list[str], optional): Skill identifiers. Default: `None`.
- **backend** (str, optional): LLM backend identifier. Default: `None`.
- **workspace** ([Workspace](../workspace/workspace.md#class-openjiuwenharnessworkspaceworkspace) | str, optional): Workspace override. Default: `None`.
- **sys_operation** (SysOperation, optional): System operation override. Default: `None`.
- **language** (str, optional): Language code. Default: `None`.
- **prompt_mode** (str | [PromptMode](../prompts/prompts.md#enum-openjiuwenharnesspromptspromptmode), optional): Prompt mode. Default: `None`.
- **enable_task_loop** (bool): Enable task loop for the sub-agent. Default: `False`.
- **max_iterations** (int): Maximum iterations. Default: `15`.
- **factory_name** (str, optional): Factory function name for custom sub-agent creation. Default: `None`.
- **factory_kwargs** (dict, optional): Additional keyword arguments for the factory function. Default: `None`.
