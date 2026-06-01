把一个外部独立 agent（如 claudecode / codex / hermes 等）以"团队成员"形式桥接进来。本地是一个完整 teammate（按 teammate 行为认领任务、收发消息），但具体工作产出由通过协议接入的远程 agent 完成；本地 LLM 只做调度，原样转发远程结果。

| 参数 | 可见性 | 用法 |
|---|---|---|
| **member_name** | 公开 | 唯一语义化名（如 `remote-claude-1`，DNS label 风格 kebab-case），**首字符必须是小写字母，其余仅允许小写字母、数字和连字符**，团队内唯一 |
| **display_name** | 公开 | 桥接成员显示名（如「远程 Claude」），仅用于展示 |
| **desc** | 公开 | **必填**。桥接成员的角色画像，同时作为本地团队 persona 与远程 agent 的连接 briefing（通过 adapter.connect 下发，远程据此扮演角色）。注入其他成员的 system prompt 并由 list_members 返回，禁止写入私密信息 |
| **mailbox_inject_mode** | 内部 | 可选。团队消息转发给远程时的形态：`passthrough`（默认）仅加最简发送者前缀直传；`rephrase` 包装完整发送者上下文（角色、人设、相关任务） |
| **protocol** | 内部 | 可选。协议标识（如 `a2a` / `acp` / `claudecode`），保留用于后续 BridgeProtocolAdapter 适配器查找；空字符串表示尚未绑定适配器 |
| **adapter_config** | 内部 | 可选。协议适配器配置（endpoint / auth / relay_timeout_s 等），原样透传给 BridgeProtocolAdapter.connect |
| **model_name** | 内部 | 可选。本地调度 LLM 的模型名称；远程 agent 的模型在其自身侧，不由此字段控制 |

**能力前提**：需要 `TeamAgentSpec.enable_bridge=True` 且当前 build_team 实例未禁用 Bridge。能力关闭时本工具不会出现在可用工具列表中。

必须先调用 build_team。调用顺序：build_team → create_task → spawn_bridge_agent → send_message。spawn_bridge_agent 只创建成员记录（状态为 UNSTARTED），首次 send_message 时系统自动拉起。`desc` 是长期角色画像兼远程 briefing，不要绑定到具体任务——任务通过 create_task / send_message 下发。
