# openjiuwen.agent_teams.schema

`DeepAgentSpec` 使用的子配置类。通常作为 YAML/JSON 配置中的嵌套字段指定。

## class VisionModelSpec

视觉模型配置。

* **api_key**(str, 可选): API 密钥。默认值：空字符串。
* **base_url**(str, 可选): Base URL。默认值：`https://api.openai.com/v1`。
* **model**(str, 可选): 模型名称。默认值：`gpt-4.1-mini`。
* **max_retries**(int, 可选): 最大重试次数。默认值：`3`。

## class AudioModelSpec

音频模型配置。

* **api_key**(str): API 密钥。默认值：空字符串。
* **base_url**(str): Base URL。默认值：`https://api.openai.com/v1`。
* **transcription_model**(str): 转录模型。默认值：`gpt-4o-transcribe`。
* **question_answering_model**(str): 问答模型。默认值：`gpt-4o-audio-preview`。
* **max_retries**(int): 最大重试次数。默认值：`3`。
* **http_timeout**(int): HTTP 超时时间（毫秒）。默认值：`20`。
* **max_audio_bytes**(int): 最大音频字节大小。默认值：`25 * 1024 * 1024`。
* **acr_access_key**(str): ACR access key。默认值：空字符串。
* **acr_access_secret**(str): ACR access secret。默认值：空字符串。
* **acr_base_url**(str): ACR base URL。默认值：`https://identify-ap-southeast-1.acrcloud.com/v1/identify`。

## class WorkspaceSpec

工作空间配置。当 `stable_base` 为 True 时，工作空间路径锚定在 `.agent_teams/workspaces/` 下，可在临时 worktree 清理后保留。

* **root_path**(str, 可选): 根目录路径。默认值：`./`。
* **language**(str, 可选): 语言。默认值：`cn`。
* **stable_base**(bool, 可选): True 时使用稳定工作空间路径。默认值：`False`。

## class ProgressiveToolSpec

渐进式工具暴露配置。

* **enabled**(bool, 可选): 是否启用。默认值：`True`。
* **always_visible_tools**(list[str], 可选): 始终可见的工具列表。默认值：`[]`。
* **default_visible_tools**(list[str], 可选): 默认可见的工具列表。默认值：`[]`。
* **max_loaded_tools**(int, 可选): 最大加载工具数。默认值：`12`。

## class SysOperationSpec

系统操作配置。

* **id**(str): 操作 ID。
* **mode**([OperationMode](../../openjiuwen.core/sys_operation/sys_operation.md#class-operationmode), 可选): 操作模式 — `local` 或 `sandbox`。默认值：`local`。
* **work_config**([LocalWorkConfig](../../openjiuwen.core/sys_operation/sys_operation.md#class-localworkconfig), 可选): 本地工作配置。默认值：`None`。
* **gateway_config**(SandboxGatewayConfig, 可选): 沙箱网关配置。默认值：`None`。

## class RailSpec

护栏配置。

* **type**(str): 护栏类型，如 `"task_planning"` 或 `"skill_use"`。
* **params**(dict[str, Any], 可选): 护栏参数。默认值：`{}`。

## class BuiltinToolSpec

通过工具类型注册表解析的声明式工具引用。

* **type**(str): 工具类型名称，如 `"web_search"` 或 `"web_fetch"`。
* **params**(dict[str, Any], 可选): 工具构造参数。默认值：`{}`。

## class SubAgentSpec

子 Agent 配置。

* **agent_card**([AgentCard](../../openjiuwen.core/single_agent/single_agent.md#class-agentcard)): Agent 卡片。
* **system_prompt**(str): 系统提示词。
* **tools**(list[ToolCard | BuiltinToolSpec], 可选): 工具列表。默认值：`[]`。
* **mcps**(list[McpServerConfig], 可选): MCP 服务器配置。默认值：`[]`。
* **model**(TeamModelConfig, 可选): 模型配置。默认值：`None`。
* **rails**(list[RailSpec], 可选): 护栏配置。默认值：`None`。
* **skills**(list[str], 可选): 技能列表。默认值：`None`。
* **workspace**(WorkspaceSpec, 可选): 工作空间配置。默认值：`None`。
* **sys_operation**(SysOperationSpec, 可选): 系统操作配置。默认值：`None`。
* **language**(str, 可选): 语言。默认值：`None`。
* **prompt_mode**(str, 可选): 提示词模式。默认值：`None`。
* **enable_task_loop**(bool, 可选): 启用任务循环。默认值：`False`。
* **max_iterations**(int, 可选): 最大迭代次数。默认值：`None`。
* **factory_name**(str, 可选): 工厂名称。默认值：`None`。
* **factory_kwargs**(dict[str, Any], 可选): 工厂参数。默认值：`{}`。

## class TeamModelConfig

团队角色的模型配置。

* **model_client_config**(ModelClientConfig): 模型客户端配置。
* **model_request_config**(ModelRequestConfig, 可选): 模型请求配置。默认值：`None`。