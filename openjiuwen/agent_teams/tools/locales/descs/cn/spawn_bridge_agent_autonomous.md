把一个外部独立 agent（如 claudecode / codex / hermes 等）以“团队成员”形式桥接进来。本地是一个完整 teammate，但具体工作产出由通过协议接入的远程 agent 完成；本地 LLM 只做调度并原样转发远程结果。

| 参数 | 可见性 | 用法 |
|---|---|---|
| **member_name** | 公开 | 唯一语义化名（如 `remote-claude-1`，DNS label 风格 kebab-case），**首字符必须是小写字母，其余仅允许小写字母、数字和连字符**，团队内唯一 |
| **display_name** | 公开 | 桥接成员显示名（如「远程 Claude」），仅用于展示 |
| **desc** | 公开 | 可选。桥接成员的对外花名册描述，注入其他成员的 system prompt，禁止写入私密信息 |
| **prompt** | 私有 | **必填**。远程 agent 据此扮演角色的系统提示词；仅本成员自己可见，不进他人花名册 |
| **mailbox_inject_mode** | 内部 | 可选。`passthrough`（默认）最简直传；`rephrase` 包装完整发送者上下文 |
| **protocol** | 内部 | 可选。协议标识；空字符串表示尚未绑定适配器 |
| **adapter_config** | 内部 | 可选。协议适配器配置，原样透传给 BridgeProtocolAdapter.connect |
| **model_name** | 内部 | 可选。本地调度 LLM 的模型名称；远程模型不由此字段控制 |

**能力前提**：需要 `TeamAgentSpec.enable_bridge=True` 且当前 build_team 实例未禁用 Bridge。能力关闭时本工具不会出现在可用工具列表中。

必须先调用 build_team。spawn_bridge_agent 只创建成员记录（状态为 UNSTARTED）；成员就位后按系统提示词已选分支继续：**思辨分支**用 `send_message` 启动参与，**任务协作分支**才使用 `create_task`。成员先于消息或任务存在。何时拉起取决于团队的调度模式。`prompt` 是长期角色设定和远程 briefing，不要绑定到具体请求。
