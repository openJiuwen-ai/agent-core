# agent-core 技术定位与模块检视参考

依据本仓 `8a7d03da469c202a8b0d5151387e9031725806a3` 的模块指令、spec 与部分公共入口整理（2026-10-26）。
这是检视导航，不是全仓代码审计，也不替代模块维护的权威规约。待审版本发生变化时重新读取实际文件；不要硬编码本文未列出的能力值、默认值或状态数量。

## 仓库定位

包发布名为 `openjiuwen`，Python 范围以根 `pyproject.toml` 为准；当前为 `>=3.11,<3.14`。
它提供 Agent/Workflow 执行原语、编码 Harness、团队协作和扩展协议，不承担 JiuwenSwarm 的产品渠道与 UI。
框架改动应可被既有宿主与第三方实现消费；下游需求先判断能否使用现有公共扩展点，避免把产品分支写进通用执行路径。

## 模块与依据导航

表中路径均为当前真实存在的入口。先读变更所在模块及祖先规则，再向下读取实际影响的子目录。
集中 spec 仅覆盖部分子系统；找不到相应规约时记录缺口，使用真实导出、API 文档和行为测试，不虚构模块规范。

| 模块 | 技术职责与需审视的边界 | 规则与契约入口 |
|---|---|---|
| `core/single_agent`、`runner`、`session` | 基础 Agent、公共执行 facade、能力注册、会话与 checkpoint；区分 current 与 legacy，检查共享状态和下游契约。 | [根规则](../../../../AGENTS.md)；[单 Agent 导出](../../../../openjiuwen/core/single_agent/__init__.py)；[Runner](../../../../openjiuwen/core/runner/runner.py)；[Session](../../../../openjiuwen/core/session) |
| `core/foundation`、`context_engine` | 模型/工具/存储基础层及上下文窗口、处理器和 token 元数据；核对真实请求、工具结果、模型切换与恢复一致性。 | [Foundation](../../../../openjiuwen/core/foundation)；[Context API](../../../../openjiuwen/core/context_engine/__init__.py) |
| `core/workflow`、`graph`、`controller`、`multi_agent` | 图/组件执行、控制与另一套多 Agent API；不能与 AgentTeam 的对象和生命周期混用。 | [Workflow API](../../../../openjiuwen/core/workflow/__init__.py)；[Graph](../../../../openjiuwen/core/graph)；[Multi-agent](../../../../openjiuwen/core/multi_agent) |
| `core/memory`、`retrieval`、`security`、`sys_operation` | 记忆/检索、安全与本地/沙箱执行；核对存储隔离、路径/进程资源、错误和超时语义。 | [Memory](../../../../openjiuwen/core/memory)；[Retrieval](../../../../openjiuwen/core/retrieval)；[SysOperation API](../../../../openjiuwen/core/sys_operation/__init__.py) |
| `harness` | DeepAgent 装配、Rail、工具、任务循环、manifest、子代理、工作空间和权限引擎。 | [模块规则](../../../../openjiuwen/harness/AGENTS.md)；[Rail 规则](../../../../openjiuwen/harness/rails/AGENTS.md)；[Harness specs](../../../agent-docs/specs/harness) |
| `harness_protocol` | provider-neutral SPI，仅契约与不可变值对象；不容纳 vendor SDK、Team DB 或运行实现。 | [模块规则](../../../../openjiuwen/harness_protocol/AGENTS.md)；[协议 SPEC](../../../../openjiuwen/harness_protocol/SPEC.md)；[接入文档](../../../../docs/dev/harness_protocol_integration.md) |
| `harness_providers` | 协议内置实现、共享 Turn 骨架、IO adapter、manifest 工厂与三方 provider 发现。 | [模块规则](../../../../openjiuwen/harness_providers/AGENTS.md)；[DSH 规则](../../../../openjiuwen/harness_providers/dsh/AGENTS.md)；[Provider spec](../../../agent-docs/specs/harness/S_19_harness-providers.md) |
| `agent_teams` | Leader/Teammate、任务/消息、成员装配、runtime、调度、恢复与 SwarmFlow；宿主消费 Runner/spec 公共面。 | [模块规则](../../../../openjiuwen/agent_teams/AGENTS.md)；[Team specs](../../../agent-docs/specs/agent_teams)；[公共面 spec](../../../agent-docs/specs/agent_teams/S_01_public-api-and-spec-flow.md) |
| `extensions/observability` 与 `extensions/tracer_otel` | 前者为 Harness/Team 共用 GenAI 采集栈；后者为独立的旧 Workflow/Agent tracer，不因词汇重叠合并 wire 格式。 | [观测规则](../../../../openjiuwen/extensions/observability/AGENTS.md)；[Tracer 规则](../../../../openjiuwen/extensions/tracer_otel/AGENTS.md) |
| 其他 `extensions` | checkpointer、存储、沙箱、vendor 等可选基础设施；实现依赖公共抽象，检查 optional extras 与导入边界。 | [扩展目录](../../../../openjiuwen/extensions)；[扩展 API 文档](<../../../../docs/en/2.Development Guide/API Docs/openjiuwen.extensions.README.md>) |
| `agent_evolving` | 训练/评估、轨迹、Skill 经验与 RL；不同链路的数据、审批和持久化不可混为一套状态。 | [模块规则](../../../../openjiuwen/agent_evolving/AGENTS.md)；[轨迹规则](../../../../openjiuwen/agent_evolving/trajectory/AGENTS.md)；[经验规则](../../../../openjiuwen/agent_evolving/experience/AGENTS.md)；[持久化规则](../../../../openjiuwen/agent_evolving/checkpointing/AGENTS.md)；[RL 规则](../../../../openjiuwen/agent_evolving/agent_rl/AGENTS.md)；[在线 RL 规则](../../../../openjiuwen/agent_evolving/agent_rl/online/AGENTS.md) |
| `dev_tools`、`symphony`、`rsi`、`auto_harness` | 开发工具、能力资产与优化领域；按真实包职责检视，不把同名 evolution/harness 概念并成一个运行时。`auto_harness` 当前为迁入 RSI 后的兼容导出面。 | [开发工具](../../../../openjiuwen/dev_tools)；[Symphony 定位](../../../../openjiuwen/symphony/README.md)；[RSI 导出](../../../../openjiuwen/rsi/__init__.py)；[兼容导出](../../../../openjiuwen/auto_harness/__init__.py) |

## 公共 API、共享状态与基础执行

- `__init__.py` 导出、文档 API、示例和 README 调用都可能是公共使用面。检查签名、位置参数、默认值、异常/状态码、流式输出和取消/中断语义；不要只查仓内直接 import。
- 保持 Card/Config 分工：Card 表达静态身份与能力元信息；运行时资源、宿主句柄与会话状态不要塞入跨进程元信息。具体字段以各模块模型为准。
- `Runner.resource_mgr` 和 callback framework 涉及进程共享注册；审视注册 ID、所有者、实例命名空间、注销与多会话隔离，避免用改变 checkpoint 身份解决 live 工具冲突。
- 当前 `core.single_agent` 与 legacy、`core.multi_agent` 与 `agent_teams` 各有边界。不要将兼容入口当成新能力的默认扩展点，也不要在无迁移决策时批量删除历史 API。
- 模型、ContextEngine、session/checkpointer、工具和 workflow 的修改沿完整调用链核对；真实请求中的消息、工具调用/结果配对、上下文窗口与持久化恢复都属于可观察契约。

## Harness、Rail 与扩展装配

- 从 `harness/factory.py`、`schema/config.py`、`deep_agent.py` 追踪创建与 configure；遵守模块唯一公共构造路径，不旁路装配造第二套执行环。
- 对 Rail 回调同时检查 core 事件定义、实际触发方、DeepAgent 的内外层命名空间路由、注册与注销、`supported_events` 和优先级；init 顺序与 `callback_priority(event)` 可能不同。
- 模型可见能力、注册能力与实际可执行能力分别验证；测试需覆盖受影响的真实组装与热更新，保存 Rail 对象不代表其回调已进入执行链。
- 工具实际执行应经过权限引擎、interrupt/approval、SysOperation 与沙箱链；子代理、后台路径及成员不能绕过同一动作的约束。
- Manifest/provider、扩展包加载、配置快照与 live BuildContext 分开；检查初始化前模型调用、失败回滚、包路径边界、重复注册、权限/Skill 绑定及回收。
- 区分 workspace、cwd 和 project_root；检查工件持久化、文件访问、worktree/子进程清理与 session 身份。

相关规约按影响读取：[API/构造](../../../agent-docs/specs/harness/S_01_public-api-and-construction.md)；[Rail](../../../agent-docs/specs/harness/S_04_rails-contract.md)；[工具](../../../agent-docs/specs/harness/S_05_tools-contract.md)；[安全](../../../agent-docs/specs/harness/S_08_security-engine.md)；[声明式装配](../../../agent-docs/specs/harness/S_12_manifest-declarative-assembly.md)；[子代理生命周期](../../../agent-docs/specs/harness/S_18_subagents-and-lifecycle.md)。

## 三方 Harness 协议与 provider

- `harness_protocol` 保持厂商无关。区分 observation events、交互 request/response 控制面、hook 策略控制；事件读者不承担返回批准决定的职责。
- 按 `SPEC.md` 审视 capability 与 HostCapability 的真实端到端支持、unsupported 错误、协议版本及枚举/codec 兼容。新增可选能力不能悄悄变成所有实现的必选方法。
- 核对事件顺序、STARTED/terminal 配对、单消费者 cursor 的释放、continuous 与 turn view 的互斥、稳定 ID、结构化结果、未知事件和有界缓冲；暂停不是终态。
- checkpoint 是带版本与作用域的 opaque JSON；宿主和协议不能解释 provider 私有数据。检查幂等保存、序号、防陈旧覆盖、恢复策略与 pending interaction 清理。
- provider 复用 `SerializedTurnHarness`、共享缓冲和工厂；SDK 延迟导入。改能力/模型/认证路径需检查 provider card、工厂、adapter、Team runtime 与第三方 entry point 消费。
- 保持协议的 `Session > Turn > Step` 与 Team 自身术语区别；不要为统一名词而擅自更改历史事件语义。

## Team、调度、SwarmFlow 与恢复

- 遵守 `TeamAgentSpec.build()` 与 Runner facade 的公共生命周期入口；核对 spec → 可序列化 seed → 进程内 BuildContext/live handle → 成员装配，不能把内存句柄塞进跨进程模型。
- 追踪 runtime pool、session/checkpoint、spawn、interaction、coordination 与 stream 的实际所有权；检查 leader-only、不重复启动、cold/warm/switch、暂停/停止及恢复后的输入准入。
- 任务状态、成员状态、消息投递与调度判定分开；检查持久化意图、投递后确认、CAS/幂等、late vote、重启重放及自主/调度模式差异，不能把一种模式的规则机械套到另一种。
- Team 工具的 schema、描述、renderer、权限、语言、prompt 与外部工具网关一起核对。新增字段/状态/事件不仅检查模型输出，还查持久化、恢复、monitor、成员和宿主消费者。
- SwarmFlow 是 `agent_teams/workflow` 的脚本编排引擎，区别于 `core/workflow`。检查 run/session 身份、journal/WAL 命中、budget 单写者、并发票据成对释放、失败/取消与重放副作用，避免恢复后重复执行已完成动作。
- 成员三方 Harness 复用 `HarnessIOAdapter` 投影并只消费一次协议流；团队 runtime 管团队语义，不能重复实现 provider 或 adapter。模板快照与宿主磁盘发现分属不同边界，检查角色能力及包路径声明的实际解析结果。

进一步规则：[Runtime](../../../../openjiuwen/agent_teams/runtime/AGENTS.md)；[Agent](../../../../openjiuwen/agent_teams/agent/AGENTS.md)；[Coordination](../../../../openjiuwen/agent_teams/agent/coordination/AGENTS.md)；[Handlers](../../../../openjiuwen/agent_teams/agent/coordination/handlers/AGENTS.md)；[调度](../../../../openjiuwen/agent_teams/agent/scheduling/AGENTS.md)；[工具](../../../../openjiuwen/agent_teams/tools/AGENTS.md)；[交互](../../../../openjiuwen/agent_teams/interaction/AGENTS.md)；[Harness](../../../../openjiuwen/agent_teams/harness/AGENTS.md)；[Workflow](../../../../openjiuwen/agent_teams/workflow/AGENTS.md)；[Prompt](../../../../openjiuwen/agent_teams/prompts/AGENTS.md)；[CLI](../../../../openjiuwen/agent_teams/cli/AGENTS.md)。

关键 spec：[会话/恢复](../../../agent-docs/specs/agent_teams/S_04_session-and-recovery.md)；[成员启动/流](../../../agent-docs/specs/agent_teams/S_05_member-spawn-and-stream.md)；[运行池/派发](../../../agent-docs/specs/agent_teams/S_06_runtime-pool-dispatch.md)；[SwarmFlow 引擎](../../../agent-docs/specs/agent_teams/S_18_swarmflow-engine-and-worker.md)；[调度](../../../agent-docs/specs/agent_teams/S_22_scheduling-runtime.md)；[模板快照](../../../agent-docs/specs/agent_teams/S_23_agent-template-snapshot-loading.md)；[外部成员](../../../agent-docs/specs/agent_teams/S_27_external-harness-member-runtime.md)。同号规约存在，不能仅写 `S_18` 或 `S_23`。

## 观测、演进与可选依赖

- `extensions/observability` 的采集、semconv、provider demand、redaction 和 exporter 被 Harness/Team 共用；不要在调用方分叉同一语义。GenAI 标准键的生成文件按固定版本整体生成，不手工塞入项目私有键。
- `extensions/tracer_otel` 保持历史 wire 格式与独立场景；与 GenAI 栈的有限复用不表示两套键可以互换。变更写入键需核对 exporter、trajectory 消息重建和 swarm 前端等实际读者。
- 观测必须不阻塞或破坏业务流程；检查脱敏先于写入、异常隔离、全局 provider 所有权及多个运行时的 acquire/release。
- `agent_evolving` 中 Trajectory capture/drain、消息重建、归档、Signal、候选生成、审批、经验提交、Trainer checkpoint 与 RL 服务有不同生命周期。检查候选验证隔离、审批前无持久化、副作用归属、部分失败与恢复；不把受限 review agent 赋予主 Agent 写权限。
- RL、存储、沙箱和 vendor SDK 等 optional extras 不得因顶层 import 变成基础安装的强制依赖。检查公开导出、缺依赖错误、打包资源以及第三方实现的加载边界。

## 验证和文档口径

- 根 `pyproject.toml`、`Makefile` 与镜像测试目录是测试入口依据，按实际子系统选已有回归；单测不依赖真实网络/密钥，system tests 需要服务或硬件时如实说明。
- 本地禁止格式化与静态检查，模块中此类命令不构成执行授权。检视本身保持候选只读，文档漂移和模块归档要求作为问题交接给开发处理。
- 公共变化检查中英文 API/用户文档、示例与导出。Harness、AgentTeam 的 spec/feature 归档及拆提交规则各有适用范围，不将某模块规则泛化到全仓；检视不授予提交权限。
- 跨仓兼容需说明实际核对的下游版本；只读仓内测试不能声称 JiuwenSwarm 的 Web/TUI/SDK 已完成端到端验证。
