# F_02 执行事件与宿主端口结果协议

| 项 | 内容 |
| --- | --- |
| 类型 / 状态 | feature；PA-01 的调用、宿主端口、窗口和资料版本契约已完成；PA-04 运行时交接尚未实现 |
| 日期 | 2026-09-29 |
| 负责人 | yan_zj |
| 范围 | PA-01 调用身份、事件 / 端口结果、窗口与资料版本接口；不是 PA-04 执行闭环 |
| 关联规约 | [S_01](../specs/S_01_public-api-and-execution.md)、[S_02](../specs/S_02_events-and-result-handoff.md) |
| 验证基线 | Persistent Agent 132 项、共享 Harness 定向回归 582 项、ReAct / 默认个人版 136 项通过；全仓测试仍有依赖缺失导致的收集错误 |

## 背景与已确认范围

用户确认第二步实现统一执行事件、明确端口结果、稳定编号及确认规则，并补协议测试。代码放在嵌套 `persistent_agent/` 内，同级 docs 同步；不修改个人版、不实现模型循环、群消息归档、数据库或飞书投递，不提交代码。

第一阶段 ports 方法仅返回 object，构造只做形状检查。这次替换为有类型的事件和结果，不在构造时调用端口。

## 设计与拒绝方案

- 值对象使用 frozen Pydantic 模型、禁止额外字段；嵌套负载也为 frozen 模型，集合使用 tuple / frozenset，避免重传时原地变更。
- AgentEvent 使用完整调用归属、epoch、local_seq、UTC occurred_at、建议 visibility、稳定关联引用和按类型校验的负载。event_id 从完整调用归属、epoch、local_seq 确定性生成；完整提案摘要覆盖时间、负载和可见范围，重传须复用整个事件。
- 事件类型沿用 S_02；不把任意 dict 或聊天 role 当作执行事件协议。工具参数仅携带摘要与受控引用，不保存秘密或隐藏推理。
- CommitResult 区分 confirmed / rejected / conflict / unknown / not_found。confirmed 必须有宿主 request_event_seq；not_found 必须提供完整查询声明及水位；拒绝 / 冲突 / 未知必须给原因，不能带成功序号。
- Authority、Control、Budget 的结果都携带调用归属；控制还绑定稳定 control_id，预算绑定 model_call_id。未知不转 denied / empty / 零用量；允许范围不能超出启动权限上限。
- 不提供生产默认端口、不实现第二份业务日志，不用 bool 表示提交结果，不在流式输出和端口各写一遍。

## 提交路径与后续边界

CommitPort.submit 定义为事件提案入口，query 按原事件标识核对结果；同一事件不能在流式消费和端口各独立持久化一遍。后续有界交接流如何连接 submit、如何并行消费及区分观察输出，在 PA-04 按已有架构实现和验收，本次不改架构传输设计。宿主负责校验、去重、围栏和持久化；接口返回 DTO 不等于已经实现这些后端保证。

超时在后续交接适配中映射 unknown，再 query；本次只定义结果和查询匹配校验，不增加超时重试执行器、有界队列或模型输出适配。local_seq 由后续执行器递增分配，值对象只校验正数及编号一致性。

输出仍为候选：output.proposed 具有 output_id、target_ref、share_mode 与正文 / 文件引用；不能当作已公开或送达。终态不含 delivered=true。工具 unknown 不允许被协议校验解释为工具没有执行。

## PA-01 关闭范围（契约层）

本轮补齐 Request 来源及宿主执行权引用，并定义窗口和资料版本端口的结构化协议。`source_ref` 是宿主为请求来源分配的稳定引用，随事件及端口结果绑定；`authority_handle` 仅由可信宿主持有并传给端口，不进入模型输入或业务事件。静态构造只验证二者存在且格式有效；有效执行权仍须由 PA-04 在运行前通过 `ExecutionAuthority` 核查。

`ContextStore` 定义活动 head、不可变 Window 版本、同窗保存、head 条件推进、条件建窗并激活，以及按稳定操作标识核对未知写入。`HistoryPort`、`KnowledgePort` 与 `GroupMaterialPort` 以带来源、范围、版本、授权修订和完整性标记的 `MaterialSnapshot` 返回资料；`empty / forbidden / partial / unavailable / unknown` 不可互相折叠。端口只声明协议并做方法形状检查，不提供后端、窗口恢复、上下文装配、资料授权判定或写入围栏；这些行为分别留给 PA-04、PA-05、PA-09～PA-12。

四个基础宿主端口仍是 Persistent 构造上下文的必需接口。窗口 / 资料端口按能力注入，不要求 construction-only Agent 提供尚未启用的能力；若显式注入则须完整满足异步接口。这样 PA-02 可在进入 DeepAgent 装配前清楚拒绝缺失或错误接口，而不误报窗口、历史或知识实现已经可用。

## 实现顺序与验收

1. 先写事件与端口结果测试并确认失败。
2. 实现 schema/protocol.py、events.py、port_results.py，更新 ports.py 类型及外层懒导出。
3. 验证 JSON 往返、稳定编号、类型负载匹配、真不可变、归属错配、重复提案 / 冲突、完整 not_found、控制目标、预算调用、unknown 与权限收紧。
4. 跑第一阶段及现有 Harness 定向回归；独立审查后修复关键问题；同步 S_01 / S_02 和开发计划。

## 执行记录

Pre-flight: events 与 port_results 共用调用归属 / ModelUsage；ports 消费两者。事件模型不会反向 import ports，避免循环依赖。

Ruling: 本次仅固定类型化 submit / query 契约，不实现事件流拓扑 — 保留架构中的并行生产 / 消费和确认要求；PA-04 须保证唯一提交路径，不能沿用“流结束后才保存”或重复持久化。

Ruling: 本次只实现协议值对象 / 纯校验，不提供提交器或持久化测试后端 — 用户要求不改归档及个人版；端到端去重、旧 epoch 拒绝和真实超时仍须在宿主及 PA-04 故障测试证明。

Ruling: reply.delta 使用独立 ReplyDeltaPayload，保留非空白字符以外的合法流片段 — 实测普通 Text 的非空白限制会拒绝空格、换行和制表符，不能无损映射原生流；3 个回归测试先 RED 后修复。

截至 2026-09-28 的阶段记录：当时值对象和端口结果子范围完成，PA-01 / M0 整体仍进行中。2026-09-29 已补齐来源、ContextStore 和版本化资料契约，PA-01 / M0 在接口定义与构造校验范围完成；PA-04 仍未开始。

## 验证记录

1. 初始 49 个协议测试在缺少协议 API 时失败，实现后通过；补充大小、UTF-8、时区、输入别名和结果证据等 11 项测试通过。
2. 独立审查发现 reply.delta 会拒绝合法纯空白片段；3 个新增回归测试确认 RED，独立 ReplyDeltaPayload 修复后 GREEN，审查复核无剩余重要问题。
3. 最终定向回归：167 passed，2 项已有 Pydantic 弃用警告。包括 63 项协议、27 项第一阶段构造，以及 77 项原 Harness / 工具回归。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit_tests/harness/persistent_agent tests/unit_tests/harness/test_leaf_spec_build.py tests/unit_tests/harness/test_spec_ownership.py tests/unit_tests/harness/test_factory_model_anomaly_detection_rail.py tests/unit_tests/harness/test_deep_agent_cold_build.py tests/unit_tests/harness/test_deep_agent_workspace.py tests/unit_tests/core/single_agent/test_ability_manager_add_ability.py --tb=short
```

4. 本步 6 个 Python 文件 Ruff check 与 format --check 均通过；git diff --check 无空白错误（有仓库 CRLF 提示）。
5. 实际运行 `.venv/Scripts/python.exe -m pytest -q --tb=short`：收集 20155 项，82 个收集错误、15 skipped，退出码 1；包含缺少 opentelemetry.exporter、prompt_toolkit 的模块。未执行全仓测试用例，不能声称全仓通过；未为此安装依赖或修改无关模块。
6. JiuwenSwarm checkout 仍只有原有未跟踪 local_api_server/；本步未修改个人版、共享 Harness 代码或架构文件，未提交 / 推送。

2026-09-29 补充验证：新增来源绑定、窗口 / 资料 `unknown` 与完整空结果区分、必需端口缺失及 ContextStore 错形状提前失败等测试，先 RED 后 GREEN。`tests/unit_tests/harness/persistent_agent` 132 passed；共享 Harness 定向回归 582 passed；`tests/unit_tests/agent/react_agent` 136 passed。Ruff check 和 format --check 通过。一个包含 `test_deep_agent_terminal_stream.py` 的初始回归因缺少 `opentelemetry.exporter` 在收集阶段失败；移除该文件后相应定向集通过；完整全仓测试仍不可声称通过。

## 已知遗留与下一步

- PA-01 / M0：接口契约范围完成，但不包含真实宿主实现、运行时授权 / 执行权调用、资料 / 窗口后端，也不验证第三方端口的可靠性。
- PA-03：实例内绑定 / 调用隔离已另行验收，见 [F_03](F_03_invocation-binding-and-isolation.md)；这不等于正式请求入口和 task-loop 集成完成。
- PA-04：复用 DeepAgent 公共流 API，递增分配 local_seq、映射事件、并行交接和等待确认；接入真实授权、预算及控制。
- 外部边界须重新 model_validate 结果，再匹配并显式判断 status。DTO 不证明 epoch 当前有效、端口调用幂等、宿主查询完整或渠道送达。
- 本步没有归档 / 数据库 / 文件存储，也没有执行工具、超时重试或故障恢复后端；这些不能计作完成。工具参数与审批仅是受控引用，实际引用解析和权限核验仍待实现。
