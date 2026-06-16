把一名真人作为团队成员加入（Human in the Team）。人类成员由真人通过 HumanAgentInbox 驱动，框架为其准备一个 avatar（LLM + 工具）承接任务、收发消息，但具体决策与产出来自真人。

| 参数 | 可见性 | 用法 |
|---|---|---|
| **member_name** | 公开 | 唯一语义化名（如 `product-owner`，DNS label 风格 kebab-case），**首字符必须是小写字母，其余仅允许小写字母、数字和连字符**，团队内唯一 |
| **display_name** | 公开 | 人类成员显示名（如「产品负责人」），仅用于展示 |
| **desc** | 公开 | 人类成员的角色画像与职责范围，注入其他成员的 system prompt 并由 list_members 返回；禁止写入私密信息 |

人类成员**不接受** `model_name` 与 `prompt`——模型与启动提示由框架内置模板托管，本工具也不暴露这两个参数。`desc` / `display_name` 仅用于展示与持久化人设。

**能力前提**：需要 `TeamAgentSpec.enable_hitt=True` 且当前 build_team 实例未禁用 HITT。能力关闭时本工具不会出现在可用工具列表中（运行时降级则返回拒绝并提示改用 spawn_teammate）。

必须先调用 build_team。调用顺序：build_team → create_task → spawn_human_agent → send_message。spawn_human_agent 只创建成员记录（状态为 UNSTARTED），首次 send_message 时系统自动拉起。`desc` 是长期角色画像，不要绑定到具体任务——任务通过 create_task / send_message 下发。
