# DSH external harness adapter

本目录是 DeepSeek Harness Python SDK 到 `external.protocol` 4.0 的 provider adapter。附近代码、测试和
DSH SDK 当前源码优先于历史文档；不要根据其它厂商能力推断 DSH 能力。

## 模块职责

- `config.py`：provider-owned、可验证且不触发 SDK import 的配置。
- `provider.py`：实现 provider factory，只构造未启动 Harness。
- `harness.py`：session lifecycle、输入队列、外部 Turn ownership、notification bridge。
- `mapping.py`：DSH notification/RunResult 到公共事件和 `TurnResult` 的纯映射与累积。
- `stream.py`：有界、可关闭、单消费者的持续/单 Turn event cursor。

通用 AgentTeam 投影属于相邻的 `external/member_runtime.py`，不得在本目录复制
`MemberRuntime`、`StreamController` 或 team coordination 状态机。

## 不变量

1. 一个 OpenJiuwen Turn 对应一次序列化的 `Session.run()` activity interval：adapter 派发已接受
   输入后发布 STARTED，DSH 以 prompt durable receipt 作为通知收集边界，并等待到 whole-agent
   idle。DSH native turn 保留为 provider observation，native step 映射为 Iteration item；两者都不能
   重新定义公共 Turn。
2. `send()` 在接受时分配 receipt；active Turn 后的输入排为 FOLLOW_UP。不得并发调用
   `Session.run()` 或用 stop/restart 模拟 steer。
3. 每个已接受 Turn 必须有且仅有一个 STARTED 和一个 terminal event，包括 stop 时尚在队列中的 Turn。
4. notification callback 运行在 SDK worker thread。桥回 event loop 时必须保留顺序，并让有界 BLOCK
   backpressure 传播到 adapter 生产者；不能改成无界 queue 或 fire-and-forget task。该保证不包含
   DSH SDK 内部仍无界的 subscription queue，文档和测试不得声称端到端全部有界。
   BLOCK 也会约束 `stop()`：唯一 continuous consumer 必须在 stop 前启动并读取到 EOF。不得通过提前
   close、内部丢弃或隐藏的无界 replay queue 伪造无 consumer 的无阻塞停机。
5. Card capability 必须对应可验证 SDK 行为。首版 capabilities 为空：不支持 steer、abort、
   pause/resume、checkpoint/restore 或动态 MCP。
6. `ExternalHarnessContext.mcp_servers` 非空必须明确失败。Cordis 中静态装配 MCP 不等价于动态 MCP
   capability。
7. system prompt 只能在 `system_prompt_env_var` 与消费该变量的 custom Cordis composition 同时存在
   时传递；不要宣称 bundled 默认配置会消费任意环境变量。
8. `deepseek_harness` 必须在 `start()` 内 lazy import。config/provider/public package import 不得要求
   optional SDK 已安装，也不得在构造期启动进程或绑定 event loop。
9. SDK config/client、API key、context env、stderr 和原始异常正文不得进入 event、result、checkpoint
   或日志。错误只暴露已清洗的类别和稳定 message。
10. 未识别的 JSON-safe DSH notification 作为 namespaced `ProviderEvent` 保留；不要为追求公共形状而
    静默丢弃 provider 信息。
11. `stop_on_unsupported_force_abort=True` 属于通用 MemberRuntime 的整 cycle stop 兼容策略，不是
    DSH FORCE_ABORT capability；默认严格模式与 Card 的空 capabilities 必须保持一致。

## 变更要求

- 修改映射时同时覆盖 notification、terminal `RunResult` 和多 native turn/step 的测试。
- 新增 capability 前必须从当前 Python SDK 的 public API、wire request 和源码测试证明端到端可用；
  仅有 Cordis/TypeScript 内部能力不够。
- 保持测试不启动真实 DSH subprocess，不依赖网络或凭据；使用 fake SDK/session 验证 lifecycle、顺序、
  backpressure 与敏感信息清洗。
- 公共协议形状的变更不在本目录完成；先修改 `external/protocol` spec 和版本，再适配本实现。
