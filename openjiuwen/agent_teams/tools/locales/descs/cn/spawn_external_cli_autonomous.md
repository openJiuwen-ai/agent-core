直接拉起一个第三方 CLI agent（claudecode / codex 等）作为队友，其大脑是 CLI 子进程而非本地 LLM，通过自动注入的团队 MCP 工具收发消息与认领任务。

| 参数 | 可见性 | 用法 |
|---|---|---|
| **member_name** | 公开 | 唯一语义化名（如 `cli-coder-1`，DNS label 风格 kebab-case），**首字符必须是小写字母，其余仅允许小写字母、数字和连字符**，团队内唯一 |
| **display_name** | 公开 | CLI 成员显示名（如「Claude CLI 编码助手」），仅用于展示 |
| **desc** | 公开 | 可选。该 CLI 成员的对外花名册描述，注入其他成员的 system prompt，禁止写入私密信息 |
| **prompt** | 私有 | **必填**。该 CLI 成员的私有系统提示词；仅本成员自己可见，不进他人花名册 |
| **cli_agent** | 内部 | **必填**。要拉起的 CLI 类型标识，必须命中 `TeamAgentSpec.external_cli_agents` 中的静态配置 |

CLI 成员不接受 `model_name`（模型在 CLI 侧）。框架按声明的配置拉起 CLI 子进程，并自动注入团队协作工具，使其以一等成员身份参与协同。

**能力前提**：`TeamAgentSpec.external_cli_agents` 非空。未声明任何 CLI 类型时本工具不会出现在可用工具列表中。

必须先调用 build_team。spawn_external_cli 只创建成员记录（状态为 UNSTARTED）；成员就位后按系统提示词已选分支继续：**思辨分支**用 `send_message` 启动参与，**任务协作分支**才使用 `create_task`。成员先于消息或任务存在。何时拉起取决于团队的调度模式。`prompt` 是长期角色设定，不要绑定到具体请求。
