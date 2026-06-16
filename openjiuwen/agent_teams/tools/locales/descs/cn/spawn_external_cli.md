直接拉起一个第三方 CLI agent（claudecode / codex 等）作为队友，其大脑是 CLI 子进程而非本地 LLM，通过自动注入的团队 MCP 工具收发消息与认领任务。

| 参数 | 可见性 | 用法 |
|---|---|---|
| **member_name** | 公开 | 唯一语义化名（如 `cli-coder-1`，DNS label 风格 kebab-case），**首字符必须是小写字母，其余仅允许小写字母、数字和连字符**，团队内唯一 |
| **display_name** | 公开 | CLI 成员显示名（如「Claude CLI 编码助手」），仅用于展示 |
| **desc** | 公开 | **必填**。该 CLI 成员的 persona / 角色画像，注入其他成员的 system prompt 并由 list_members 返回；禁止写入私密信息 |
| **cli_agent** | 内部 | **必填**。要拉起的 CLI 类型标识（如 `claude` / `codex`），必须命中 `TeamAgentSpec.external_cli_agents` 中预先声明的某条静态配置——启动命令、工作目录、MCP 注入都在那条配置里，本字段只按名引用 |

CLI 成员不接受 `model_name` / `prompt`（模型与配置都在 CLI 侧）。框架按声明的配置拉起 CLI 子进程，并自动注入团队协作工具（read_inbox / claim_task / send_message 等），使其以一等成员身份参与协同。

**能力前提**：`TeamAgentSpec.external_cli_agents` 非空（至少声明一种 CLI 类型）。未声明任何 CLI 类型时本工具不会出现在可用工具列表中。

必须先调用 build_team。调用顺序：build_team → create_task → spawn_external_cli → send_message。spawn_external_cli 只创建成员记录（状态为 UNSTARTED），首次 send_message 时系统自动拉起。`desc` 是长期角色画像，不要绑定到具体任务——任务通过 create_task / send_message 下发。
