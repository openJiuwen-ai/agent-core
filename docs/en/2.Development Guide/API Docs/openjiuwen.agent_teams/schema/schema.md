# openjiuwen.agent_teams.schema

Sub-configuration classes used by `DeepAgentSpec`. These are typically specified as nested fields within YAML/JSON configs.

## class VisionModelSpec

Vision model configuration.

* **api_key**(str, optional): API key. Default: empty string.
* **base_url**(str, optional): Base URL. Default: `https://api.openai.com/v1`.
* **model**(str, optional): Model name. Default: `gpt-4.1-mini`.
* **max_retries**(int, optional): Maximum retry count. Default: `3`.

## class AudioModelSpec

Audio model configuration.

* **api_key**(str): API key. Default: empty string.
* **base_url**(str): Base URL. Default: `https://api.openai.com/v1`.
* **transcription_model**(str): Transcription model. Default: `gpt-4o-transcribe`.
* **question_answering_model**(str): Question-answering model. Default: `gpt-4o-audio-preview`.
* **max_retries**(int): Maximum retry count. Default: `3`.
* **http_timeout**(int): HTTP timeout in milliseconds. Default: `20`.
* **max_audio_bytes**(int): Maximum audio byte size. Default: `25 * 1024 * 1024`.
* **acr_access_key**(str): ACR access key. Default: empty string.
* **acr_access_secret**(str): ACR access secret. Default: empty string.
* **acr_base_url**(str): ACR base URL. Default: `https://identify-ap-southeast-1.acrcloud.com/v1/identify`.

## class WorkspaceSpec

Workspace configuration. When `stable_base` is True, workspace path is anchored under `.agent_teams/workspaces/` to survive ephemeral worktree cleanup.

* **root_path**(str, optional): Root directory path. Default: `./`.
* **language**(str, optional): Language. Default: `cn`.
* **stable_base**(bool, optional): When True, use stable workspace path. Default: `False`.

## class ProgressiveToolSpec

Progressive tool exposure configuration.

* **enabled**(bool, optional): Whether enabled. Default: `True`.
* **always_visible_tools**(list[str], optional): Always-visible tool list. Default: `[]`.
* **default_visible_tools**(list[str], optional): Default-visible tool list. Default: `[]`.
* **max_loaded_tools**(int, optional): Maximum loaded tools. Default: `12`.

## class SysOperationSpec

System operation configuration.

* **id**(str): Operation ID.
* **mode**([OperationMode](../../openjiuwen.core/sys_operation/sys_operation.md#class-operationmode), optional): Operation mode — `local` or `sandbox`. Default: `local`.
* **work_config**([LocalWorkConfig](../../openjiuwen.core/sys_operation/sys_operation.md#class-localworkconfig), optional): Local work config. Default: `None`.
* **gateway_config**(SandboxGatewayConfig, optional): Sandbox gateway config. Default: `None`.

## class RailSpec

Guardrail configuration.

* **type**(str): Guardrail type, such as `"task_planning"` or `"skill_use"`.
* **params**(dict[str, Any], optional): Guardrail parameters. Default: `{}`.

## class BuiltinToolSpec

Declarative tool reference resolved via tool type registry.

* **type**(str): Tool type name, such as `"web_search"` or `"web_fetch"`.
* **params**(dict[str, Any], optional): Tool constructor parameters. Default: `{}`.

## class SubAgentSpec

Sub-agent configuration.

* **agent_card**([AgentCard](../../openjiuwen.core/single_agent/single_agent.md#class-agentcard)): Agent card.
* **system_prompt**(str): System prompt.
* **tools**(list[ToolCard | BuiltinToolSpec], optional): Tool list. Default: `[]`.
* **mcps**(list[McpServerConfig], optional): MCP server configs. Default: `[]`.
* **model**(TeamModelConfig, optional): Model config. Default: `None`.
* **rails**(list[RailSpec], optional): Guardrail configs. Default: `None`.
* **skills**(list[str], optional): Skill list. Default: `None`.
* **workspace**(WorkspaceSpec, optional): Workspace config. Default: `None`.
* **sys_operation**(SysOperationSpec, optional): System operation config. Default: `None`.
* **language**(str, optional): Language. Default: `None`.
* **prompt_mode**(str, optional): Prompt mode. Default: `None`.
* **enable_task_loop**(bool, optional): Enable task loop. Default: `False`.
* **max_iterations**(int, optional): Max iterations. Default: `None`.
* **factory_name**(str, optional): Factory name. Default: `None`.
* **factory_kwargs**(dict[str, Any], optional): Factory kwargs. Default: `{}`.

## class TeamModelConfig

Model configuration for a team role.

* **model_client_config**(ModelClientConfig): Model client config.
* **model_request_config**(ModelRequestConfig, optional): Model request config. Default: `None`.