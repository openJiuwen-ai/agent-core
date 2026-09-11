# DeepSeek Harness external adapter

本包使用 DeepSeek Harness（DSH）Python SDK 实现
`openjiuwen.agent_teams.external.protocol` 4.0。它负责 provider session、输入排队、DSH notification
归一化和有界事件流；`external.member_runtime.ExternalHarnessMemberRuntime` 再把公共协议投影成
AgentTeam 内部 `MemberRuntime` 行为。

## 当前边界

```text
DshHarnessProvider
       |
       v
   DshHarness  -- ExternalHarnessProtocol 4.0
       |
       v
ExternalHarnessMemberRuntime
       |
       v
AgentTeam MemberRuntime / StreamController
```

首版只支持程序化构造，尚未注册到 `ExternalCliAgentSpec`、`build_cli_runtime` 或声明式 member spawn。
现有 Claude Code/Codex backend 也没有迁入本包。

## Turn 与事件映射

一个 OpenJiuwen Turn 是一次串行 `Session.run()` activity interval：adapter 开始派发已接受输入，
DSH 以 prompt durable receipt 作为通知收集边界，并持续到整个 agent（含子 agent）进入 idle。
DSH native turn/step 不改变该边界：

- native turn 作为 `ProviderEvent` 保留；
- native step 映射为 `item_type="iteration"` 的 `ItemLifecycleEvent`；
- assistant text/reasoning chunk 映射为 `OutputEvent` DELTA；assistant message 提供 FINAL；
- tool call/result 映射为 tool item lifecycle；
- usage 映射为 `UsageUpdatedEvent`；
- 未识别 notification 作为 namespaced `ProviderEvent` 保留。

adapter 会在接受输入时立即返回外部 `message_id`/`turn_id`。如果已有 Turn 正在运行，后续 AUTO
输入会作为 FOLLOW_UP 排队；整个 follow-up 链保持 Harness RUNNING，只在队列耗尽后进入 IDLE，
不会通过并发 `Session.run()` 模拟 steer。SDK 在 idle 时没有任何 native `turn/end` 属于协议失败，
不会被当作空成功；`turn/end` error 的原始 message 也会在 ProviderEvent 中脱敏。

## 能力矩阵

`DshHarness.card.capabilities` 当前为空。

| 行为 | 首版支持 | 说明 |
|---|---|---|
| 多轮 session | 是 | 同一 live DSH runtime/session 内串行运行多个外部 Turn |
| 持续/单 Turn event cursor | 是 | 同一逻辑单消费者流，有界 BLOCK backpressure |
| steer | 否 | DSH Python SDK 当前没有运行中插入 API |
| abort | 否 | graceful/force abort 均不声明 |
| pause/resume | 否 | 不声明 warm/cold resume |
| checkpoint | 否 | `export_checkpoint()` 返回 `None`；拒绝 checkpoint/REQUIRE_RESUME |
| 动态 MCP | 否 | `ExternalHarnessContext.mcp_servers` 非空时启动失败 |
| system prompt | 有条件 | 需要 custom Cordis composition 读取配置的环境变量 |

DSH 可在 custom Cordis composition 中静态装配 MCP，但这不是协议层动态 MCP 支持。不要仅凭 DSH
session id 声明 persistent session/checkpoint capability。

`ExternalHarnessMemberRuntime(..., stop_on_unsupported_force_abort=True)` 可在 AgentTeam 请求
hard-cancel 时停止整个 Harness cycle。这只是 MemberRuntime 兼容策略，不是 DSH turn force-abort，
也不改变空 capabilities；默认 `False` 会严格拒绝不支持的请求。

公开 protocol event buffer 是有界 BLOCK 队列，但 DSH SDK 内部 subscription queue 当前仍无界。
因此本 adapter 只能保证自身公开事件缓冲有界，无法消除上游 SDK 在慢消费者场景中的潜在积压。
BLOCK 同样约束停机：调用方应在发起 `stop()` 前启动唯一的 `events()` consumer，并让它持续读取到
EOF；先等待 `stop()`、再回头读取事件，在缓冲区已满时会形成符合背压语义的等待。通用
`ExternalHarnessMemberRuntime` 已按这个顺序持续消费。event callback 不得在 event pump 自身直接
`await runtime.stop()`；需要停机时应设置信号或创建独立 control task。

## Optional SDK

`deepseek-harness-sdk` 是 optional dependency，声明在 pyproject 的 `dsh` extra 里。导入
OpenJiuwen、本包的 config/provider 或协议不会导入 SDK；只有 `DshHarness.start()` 才会
lazy import `deepseek_harness`（发布包名是 `deepseek-harness-sdk`，import 名不同）。

```bash
uv pip install 'openjiuwen[dsh]'
```

该 SDK 迄今只发布过预发布版本，因此 extra 的下限写成 `>=0.1.2a3`——PEP 440 要求约束里出现
预发布标识，resolver 才会考虑预发布。它会连带装上同版本的 `deepseek-harness-runtime-bin`
平台 wheel（覆盖 macOS arm64 / linux x86_64 / linux aarch64 / win amd64）。

在 DSH 源码 checkout 中开发时改用可编辑安装：

```bash
uv pip install -e /path/to/deepseek-harness/python/sdk
```

缺少 SDK 时，start 会抛出 `ExternalHarnessError`，不会让公共 package import 失败——单元测试
因此不需要装 SDK，它们注入的是 fake `deepseek_harness` 模块。

## System prompt

DSH Python SDK 没有原生 system-prompt 参数。要传入
`ExternalHarnessContext.system_prompt`，必须同时配置：

1. `DshHarnessConfig.system_prompt_env_var`；
2. 一个显式读取该环境变量的 custom Cordis composition。

例如 `system_prompt_env_var="DSH_SYSTEM_PROMPT"` 只负责把值放进 runtime env；如果 Cordis 配置没有
读取 `process.env.DSH_SYSTEM_PROMPT`，prompt 不会生效。bundled 默认 composition 不应被假定会消费
这个变量。

完整程序化示例见
[`docs/dev/agent_teams/external_harness_integration.md`](../../../../docs/dev/agent_teams/external_harness_integration.md)。
