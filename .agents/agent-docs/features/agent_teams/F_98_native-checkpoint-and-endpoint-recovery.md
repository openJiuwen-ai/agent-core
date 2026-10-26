# NativeHarness 冷恢复与原端点重连恢复

- 日期：2026-09-11
- Refs: #751
- 范围：`agent_teams/harness/{protocol_adapter,checkpoint}.py`、Claude/Codex provider

## NativeHarness checkpoint

NativeHarnessProtocolAdapter 以 `native_v2` 注册到统一 manifest 工厂，card/checkpoint 名也为 `native_v2`。
配置支持 `deep_agent`、`agent_template`、`language` 和 `event_buffer_capacity`。
新增 CHECKPOINT / PERSISTENT_SESSION。checkpoint 在暂停边界和
整条输入链完成后发布；宿主也可在 PAUSED/IDLE 调用 export_checkpoint。运行中导出显式拒绝，
不截取正在执行工具的中间状态。已有 NativeHarness pause/_prepare/resume 内核保持不变。

schema_version=1 的 JSON 信封包含 native session/card/cwd 身份、ContextEngine 上下文、
DeepAgent 状态（含任务计划、plan mode、coordinator 状态）、暂停输入与原 query、排队输入，
以及已累积的 protocol 输出。消息使用现有 `core.session.vcs.codec` 编解码，保留
AssistantMessage/ToolMessage 类型及 tool call 关联，避免 pickle 或反射构造。

恢复仍先走 manifest → DeepAgentSpec.agent_template_spec → NativeHarness._prepare，模型端点和
扩展配置由宿主重新提供，不进入 checkpoint。ContextEngine 在首次使用时从重建的 session
恢复消息；coordinator 在 native.start 后加载保留的状态。

- REQUIRE_RESUME 必须有 checkpoint，RESUME_IF_AVAILABLE 有则恢复，NEW 不采用传入的快照。
- 校验 provider、host session、agent、schema version、native card/cwd、Turn ID 唯一性和 JSON 形状。
- start 恢复后保持 IDLE，不自动运行模型。暂停快照须先 resume()；可传原 query 做一致性校验。
- resume 恢复原 Turn/message ID 和 queued receipt，调用 native.resume(query=原 query)，不追加
  新用户消息。新 observation cycle 输出 STARTED、PAUSED、RESUMED，随后继续正常输出并终结。
- 快照是自包含的 JSON；仍依赖原工作目录及被引用的外部文件存在，不迁移工作区文件。
- 不迁移正在运行的后台工具、进程、子代理执行；后台工具未结束或 ask-user 尚未解决时拒绝导出。
  此快照范围为父 NativeHarness 的上下文与 DeepAgent 状态，不是任意扩展运行对象的序列化。
- checkpoint sink 沿用公共协议的序号和背压规则，超过信封大小上限明确失败。自动发布失败记录日志，
  不把 native 的暂停改成失败；显式 export_checkpoint 会将错误返回给宿主。

## fallback 原端点重连

保留原有持久化门：宿主拒绝 fallback 时，关闭备用 client 并重连原端点。本 Turn 仍以原认证错误
结束。若原端点重连也失败，后续每个已接受输入先尝试一次原端点连接，并恢复同一个 session/thread。
连接成功后才发送新输入；连接仍失败则返回结构化 FAILED，保留下一次重试机会。

不在后台无限重试，也不重放先前已经失败的输入。重连期间遇到 abort/stop，按 ABORTED 结束并关闭
迟到的 client；不会把被拒绝的备用端点标记为 active 或发布 fallback 激活事件。

## 验证

- Native：JSON 信封 round-trip 后构造新实例，验证同 Turn 恢复、排队输入、上下文中用户消息不重复、
  原生 continuation 标记、scope/version 检查、运行中拒绝导出。
- 实际 ContextEngine + VCS codec：保留 assistant/tool 类型、tool-call ID、coordinator iteration；
  验证 checkpoint sink 顺序与 CAS revision 传递。
- Claude/Codex：持久化拒绝 → 原端点重连失败 → 后续输入再次失败 → 再次输入恢复成功，验证重连次数、
  原模型、session/thread 及旧 client 关闭情况。
