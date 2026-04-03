# openjiuwen.agent_teams.schema

## class openjiuwen.agent_teams.schema.StopConditionSpec

用于定义 DeepAgent 的停止条件。

**属性**：

- **max_iterations** (int，可选): 最大迭代次数，默认值：`None`。
- **max_token_usage** (int，可选): 最大 token 使用量，默认值：`None`。
- **completion_promise** (str，可选): 完成承诺字符串，默认值：`None`。
- **timeout_seconds** (float，可选): 超时时间（秒），默认值：`None`。

## class openjiuwen.agent_teams.schema.VisionModelSpec

视觉模型配置。

**属性**：

- **api_key** (str，可选): API Key，默认值：空字符串。
- **base_url** (str，可选): 基础 URL，默认值：`https://api.openai.com/v1`。
- **model** (str，可选): 模型名称，默认值：`gpt-4.1-mini`。
- **max_retries** (int，可选): 最大重试次数，默认值：`3`。

## class openjiuwen.agent_teams.schema.AudioModelSpec

音频模型配置。

**属性**：

- **api_key** (str): API Key，默认值：空字符串。
- **base_url** (str): 基础 URL，默认值：`https://api.openai.com/v1`。
- **transcription_model** (str): 转录模型，默认值：`gpt-4o-transcribe`。
- **question_answering_model** (str): 问答模型，默认值：`gpt-4o-audio-preview`。
- **max_retries** (int): 最大重试次数，默认值：`3`。
- **http_timeout** (int): HTTP 超时时间（毫秒），默认值：`20`。
- **max_audio_bytes** (int): 最大音频字节数，默认值：`25 * 1024 * 1024`。
- **acr_access_key** (str): ACR 访问密钥，默认值：空字符串。
- **acr_access_secret** (str): ACR 访问密钥，默认值：空字符串。
- **acr_base_url** (str): ACR 基础 URL，默认值：`https://identify-ap-southeast-1.acrcloud.com/v1/identify`。

## class openjiuwen.agent_teams.schema.WorkspaceSpec

工作空间配置。

**属性**：

- **root_path** (str，可选): 根目录路径，默认值：`./`。
- **language** (str，可选): 语言，默认值：`cn`。


## class openjiuwen.agent_teams.schema.ProgressiveToolSpec

渐进式工具暴露配置。

**属性**：

- **enabled** (bool，可选): 是否启用，默认值：`True`。
- **always_visible_tools** (list[str]，可选): 始终可见的工具列表，默认值：`[]`。
- **default_visible_tools** (list[str]，可选): 默认可见的工具列表，默认值：`[]`。
- **max_loaded_tools** (int，可选): 最大加载工具数量，默认值：`12`。

## class openjiuwen.agent_teams.schema.SysOperationSpec

系统操作配置。

**属性**：

- **id** (str): 操作 ID。
- **mode** ([OperationMode](../../openjiuwen.core/sys_operation/sys_operation.md#class-operationmode)，可选): 操作模式，`local` 或 `sandbox`，默认值：`local`。
- **work_config**** ([LocalWorkConfig](../../openjiuwen.core/sys_operation/sys_operation.md#class-localworkconfig)，可选): 本地工作配置，默认值：`None`。
- **gateway_config** (SandboxGatewayConfig，可选): 沙箱网关配置，默认值：`None`。

## class openjiuwen.agent_teams.schema.RailSpec

护栏配置。

**属性**：

- **type** (str): 护栏类型（如 "task_planning"、"skill_use" 等）。
- **params** (dict[str, Any]): 护栏参数，默认值：`{}`。

## class openjiuwen.agent_teams.schema.SubAgentSpec

子Agent配置。

**属性**：

- **agent_card** ([AgentCard](../openjiuwen.core/single_agent/single_agent.md#class-openjiuwencoresingle_agentagentcard)): Agent 卡片。
- **system_prompt** (str): 系统提示词。
- **tools** (list[[ToolCard](../openjiuwen.core/foundation/tool/tool.md#class-toolcard)]): 工具列表，默认值：`[]`。
- **mcps** (list[McpServerConfig]): MCP 服务器配置列表，默认值：`[]`。
- **model** (TeamModelConfig，可选): 模型配置，默认值：`None`。
- **rails** (list[[RailSpec](#class-openjiuwenagent_teamsschemarailspec)]，可选): 护栏配置列表，默认值：`None`。
- **skills** (list[str]，可选): 技能列表，默认值：`None`。
