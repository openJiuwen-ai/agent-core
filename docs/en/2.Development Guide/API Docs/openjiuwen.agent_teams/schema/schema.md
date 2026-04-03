# openjiuwen.agent_teams.schema

## class openjiuwen.agent_teams.schema.StopConditionSpec

Defines the stop conditions for a `DeepAgent`.

**Attributes**:

- **max_iterations** (int, optional): Maximum number of iterations. Default: `None`.
- **max_token_usage** (int, optional): Maximum token usage. Default: `None`.
- **completion_promise** (str, optional): Completion promise string. Default: `None`.
- **timeout_seconds** (float, optional): Timeout in seconds. Default: `None`.

## class openjiuwen.agent_teams.schema.VisionModelSpec

Vision model configuration.

**Attributes**:

- **api_key** (str, optional): API key. Default: empty string.
- **base_url** (str, optional): Base URL. Default: `https://api.openai.com/v1`.
- **model** (str, optional): Model name. Default: `gpt-4.1-mini`.
- **max_retries** (int, optional): Maximum retry count. Default: `3`.

## class openjiuwen.agent_teams.schema.AudioModelSpec

Audio model configuration.

**Attributes**:

- **api_key** (str): API key. Default: empty string.
- **base_url** (str): Base URL. Default: `https://api.openai.com/v1`.
- **transcription_model** (str): Transcription model. Default: `gpt-4o-transcribe`.
- **question_answering_model** (str): Question-answering model. Default: `gpt-4o-audio-preview`.
- **max_retries** (int): Maximum retry count. Default: `3`.
- **http_timeout** (int): HTTP timeout in milliseconds. Default: `20`.
- **max_audio_bytes** (int): Maximum audio byte size. Default: `25 * 1024 * 1024`.
- **acr_access_key** (str): ACR access key. Default: empty string.
- **acr_access_secret** (str): ACR access secret. Default: empty string.
- **acr_base_url** (str): ACR base URL. Default: `https://identify-ap-southeast-1.acrcloud.com/v1/identify`.

## class openjiuwen.agent_teams.schema.WorkspaceSpec

Workspace configuration.

**Attributes**:

- **root_path** (str, optional): Root directory path. Default: `./`.
- **language** (str, optional): Language. Default: `cn`.

## class openjiuwen.agent_teams.schema.ProgressiveToolSpec

Progressive tool exposure configuration.

**Attributes**:

- **enabled** (bool, optional): Whether it is enabled. Default: `True`.
- **always_visible_tools** (list[str], optional): List of always-visible tools. Default: `[]`.
- **default_visible_tools** (list[str], optional): List of default-visible tools. Default: `[]`.
- **max_loaded_tools** (int, optional): Maximum number of loaded tools. Default: `12`.

## class openjiuwen.agent_teams.schema.SysOperationSpec

System operation configuration.

**Attributes**:

- **id** (str): Operation ID.
- **mode** ([OperationMode](../../openjiuwen.core/sys_operation/sys_operation.md#class-operationmode), optional): Operation mode, either `local` or `sandbox`. Default: `local`.
- **work_config** ([LocalWorkConfig](../../openjiuwen.core/sys_operation/sys_operation.md#class-localworkconfig), optional): Local work configuration. Default: `None`.
- **gateway_config** (SandboxGatewayConfig, optional): Sandbox gateway configuration. Default: `None`.

## class openjiuwen.agent_teams.schema.RailSpec

Guardrail configuration.

**Attributes**:

- **type** (str): Guardrail type, such as `"task_planning"` or `"skill_use"`.
- **params** (dict[str, Any]): Guardrail parameters. Default: `{}`.

## class openjiuwen.agent_teams.schema.SubAgentSpec

Sub-agent configuration.

**Attributes**:

- **agent_card** ([AgentCard](../../openjiuwen.core/single_agent/single_agent.md#class-openjiuwencoresingle_agentagentcard)): Agent card.
- **system_prompt** (str): System prompt.
- **tools** (list[[ToolCard](../../openjiuwen.core/foundation/tool/tool.md#class-toolcard)]): Tool list. Default: `[]`.
- **mcps** (list[McpServerConfig]): MCP server configuration list. Default: `[]`.
- **model** (TeamModelConfig, optional): Model configuration. Default: `None`.
- **rails** (list[[RailSpec](#class-openjiuwenagent_teamsschemarailspec)], optional): Guardrail configuration list. Default: `None`.
- **skills** (list[str], optional): Skill list. Default: `None`.
