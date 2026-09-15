把一名真人作为团队成员直接加入（Human in the Team），**不为其准备任何内部代理（avatar）**。被动人类成员是一个纯名册身份 + 消息地址：团队侧的消息与任务指派经外部通道（SDK 回调）直达本人；本人经外部协议回应该团队——发言走消息总线，工具操作（查看任务、认领、完成、审查、发消息）以 tool call 透传进运行时，由运行时以其成员身份直接执行，效果与人类成员（avatar）的工具操作完全一致。

| 参数 | 可见性 | 用法 |
|---|---|---|
| **member_name** | 公开 | 唯一语义化名（如 `product-owner`，DNS label 风格 kebab-case），**首字符必须是小写字母，其余仅允许小写字母、数字和连字符**，团队内唯一 |
| **display_name** | 公开 | 被动人类成员显示名（如「产品负责人」），仅用于展示 |
| **desc** | 公开 | 被动人类成员的角色画像与职责范围，注入其他成员的 system prompt 并由 list_members 返回；禁止写入私密信息 |

被动人类成员**不接受** `model_name` 与 `prompt`——没有 avatar，也就没有模型与启动提示，本工具不暴露这两个参数。

**能力前提**：需要 `TeamAgentSpec.enable_hitt=True` 且当前 build_team 实例未禁用 HITT。能力关闭时本工具不会出现在可用工具列表中（运行时降级则返回拒绝并提示改用 spawn_teammate）。

必须先调用 build_team。调用顺序：build_team → spawn_passive_human → create_task。成员先于任务存在。被动人类成员注册即 READY（无进程可拉起），**可以被 create_task / update_task 指派任务**，真人经外部通道收到通知并以工具透传完成。`desc` 是长期角色画像，不要绑定到具体任务——任务通过 create_task / send_message 下发。
