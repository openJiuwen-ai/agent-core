## 在第三方 CLI 上运行

团队在 `TeamAgentSpec.external_cli_agents` 中声明了第三方 CLI agent（claudecode / codex 等）。填写 `cli_agent` 后，这名队友的大脑是该 CLI 子进程而不是本地 LLM：框架按声明的配置拉起 CLI 子进程，自动注入团队协作工具（view_task / claim_task / send_message 等），入站消息由框架直接推送，使其以一等成员身份参与协同。`member_name` / `display_name` / `desc` / `prompt` 的含义与本地队友相同；`isolation` / `permissions` / `fork` / `fork_source` / `fork_mode` 只适用于本地队友，与 `cli_agent` 同时出现会被拒绝。

只有用户明确指定时才填写 `model_name`，且必须是团队模型池中与该 CLI 模型调用协议兼容的端点；你不得根据可用模型自行选择、推断或补全。用户未明确指定时必须省略，让第三方 Agent 使用其自身默认模型。`external_cli.fallback_model_name` 必须来自团队模型池，并与该第三方 Agent 支持的模型调用协议兼容。选择 fallback 时遵循固定优先级：当前模型在模型池中且协议兼容时优先选择当前模型；当前模型不在模型池中或协议不兼容时再选择其他兼容模型；没有任何兼容模型、或该 cli_agent 不支持认证回退时省略本字段，使成员继续使用原生模式。不得为了填写而随意编造模型。存在兼容的 fallback 时，认证失败且本轮尚未产生输出或工具副作用才切换并重试一次。

| 参数 | 可见性 | 用法 |
|---|---|---|
| **cli_agent** | 内部 | 要拉起的 CLI 类型标识（如 `claude` / `codex`），必须命中 `TeamAgentSpec.external_cli_agents` 中预先声明的某条静态配置——启动命令、工作目录、MCP 注入都在那条配置里，本字段只按名引用 |
| **external_cli.fallback_model_name** | 内部 | 仅对支持认证回退的 cli_agent（`compatible_fallback_models` 目录中列出的）有效，其余 cli_agent **必须省略**。该 cli_agent 存在兼容模型时**必须填写**；模型池中没有兼容模型时省略，明确使用原生模式且不启用自动回退 |
