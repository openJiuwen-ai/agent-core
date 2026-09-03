直接拉起一个第三方 CLI agent（claudecode / codex 等）作为队友，其大脑是 CLI 子进程而非本地 LLM，通过自动注入的团队 MCP 工具收发消息与认领任务。

| 参数 | 可见性 | 用法 |
|---|---|---|
| **member_name** | 公开 | 唯一语义化名（如 `cli-coder-1`，DNS label 风格 kebab-case），**首字符必须是小写字母，其余仅允许小写字母、数字和连字符**，团队内唯一 |
| **display_name** | 公开 | CLI 成员显示名（如「Claude CLI 编码助手」），仅用于展示 |
| **desc** | 公开 | 可选。该 CLI 成员的对外花名册描述，仅供其他成员在 list_members / 团队 roster 中识别；注入其他成员的 system prompt，禁止写入私密信息 |
| **prompt** | 私有 | **必填**。该 CLI 成员的私有系统提示词，CLI 据此扮演本成员角色；仅本成员自己可见，不进他人花名册 |
| **cli_agent** | 内部 | **必填**。要拉起的 CLI 类型标识（如 `claude` / `codex`），必须命中 `TeamAgentSpec.external_cli_agents` 中预先声明的某条静态配置——启动命令、工作目录、MCP 注入都在那条配置里，本字段只按名引用 |
| **model_name** | 内部 | 可选。仅当用户明确指定该第三方 Agent 使用的模型名称时填写。你不得自行选择、推断或补全；用户未明确指定时必须省略，使该 Agent 使用其自身默认模型 |
| **fallback_model_name** | 内部 | **字段必填**。存在兼容模型时，必须从团队模型池中选择与该第三方 Agent 模型调用协议兼容的模型。当前模型在模型池中且协议兼容时优先选择当前模型；当前模型不在模型池中或协议不兼容时再选择其他兼容模型。不得随意填写不存在或不兼容的模型。只有模型池中没有兼容模型时才传 `null`，明确使用原生模式且不启用自动回退 |

只有用户明确指定 `model_name` 时才填写该字段；你不得根据可用模型自行选择、推断或补全。用户未明确指定时必须省略，让第三方 Agent 使用其自身默认模型。`fallback_model_name` 必须来自团队模型池，并与该第三方 Agent 支持的模型调用协议兼容。选择 fallback 时遵循固定优先级：当前模型在模型池中且协议兼容时优先选择当前模型；当前模型不在模型池中或协议不兼容时再选择其他兼容模型；只有没有任何兼容模型时才传 `null`，使成员继续使用原生模式。不得为了满足必填而随意填写模型。存在兼容的 fallback 时，认证失败且本轮尚未产生输出或工具副作用才切换并重试一次。框架按声明的配置拉起 CLI 子进程，并自动注入团队协作工具（read_inbox / claim_task / send_message 等），使其以一等成员身份参与协同。

**能力前提**：`TeamAgentSpec.external_cli_agents` 非空（至少声明一种 CLI 类型）。未声明任何 CLI 类型时本工具不会出现在可用工具列表中。

必须先调用 build_team。调用顺序：build_team → spawn_external_cli → create_task。成员先于任务存在。spawn_external_cli 只创建成员记录（状态为 UNSTARTED），何时被拉起取决于团队的调度模式（见系统提示词《任务下发与获取》一节）。`prompt` 是长期角色设定，不要绑定到具体任务——任务通过 create_task / send_message 下发。
