# F_04 按 v2 收敛已实现的契约

| 项 | 内容 |
| --- | --- |
| 类型 / 状态 | feature；PA-22，已完成 |
| 日期 | 2026-10-07 |
| 负责人 | Song Haoran |
| 范围 | 内部 `persistent_agent/{ports.py, schema/{execution,protocol,events,port_results,context}.py}`，以及外层 `__init__.py` 导出和对应单测；`schema/build_context.py`、`factory.py` 只修正过时的文档字符串。不改共享 harness / core 代码 |
| 测试基线 | 改动前：`tests/unit_tests/harness/persistent_agent` 132 passed，F_01～F_03 使用的共享回归集 213 passed。改动后：PA 包 161 passed，共享回归 213 passed，改动范围 Ruff check / format 通过（Python 3.11.17，uv 按 `uv.lock` 同步的 `.venv`） |
| 关联 | [v2 设计](../designs/persistent-agent-design-v2.md) §7.8、§7.9、§9.1～9.3、§11.2、§14、附录 E；[开发计划](../designs/persistent-agent-development-plan-v2.md) PA-22；规约 S_01～S_03 |

## 背景

PA-01 按旧版架构定义了执行上下文、宿主端口、事件和端口返回值（[F_02](F_02_execution-protocol.md)）。v2 改为：Persistent Agent 与 AgentServer 同进程运行，同一 Session 只有一个执行者；事件进入服务端按 Execution 维护的有序队列；跨 Execution 的状态由内部组件 `WindowStore` 保存在服务端注入的 `BaseKVStore` 中；崩溃续做靠执行日志。旧契约中的执行代次、执行权检查、控制轮询、提交结果查询、外部窗口存储和群资料端口都已没有使用方。

PA-04、05、06、09、12、14、16、21、23 都以新契约为开工前置。本特性只调整契约，也就是类型定义、端口协议和构造时的校验，不实现任何运行行为。

## 决策

### 1. 执行上下文

- 删除 `epoch` 和 `authority_handle`（v2 附录 E）。所有事件和端口返回值继承的 `InvocationRef` 随之去掉 `epoch`，`event_id` 改由完整调用归属和 `local_seq` 推导。
- 保留 `recovery_ref`。崩溃续做和非确认暂停后继续，都用它指向原 Request 中待接续的那次 Execution（v2 §11.2）。它不能等于本次的 `execution_id`。不新增 `continuation` 字段。
- `principal_ref`、`startup_scope`、`source_scope`、`policy_revision`、`capability_snapshot` 等附录 E 没有点名的字段保持不变。

### 2. 端口

| 端口 | 方法 | 约定 |
| --- | --- | --- |
| `CommitPort`（必需） | `record(execution, event) -> None` | 普通事件进入服务端有序队列后返回；`tool.intent`、`execution.paused`、`execution.final` 要等此前事件和本事件全部落盘才返回。任何失败都抛异常，调用方停止当前执行（v2 §9.1） |
| | `read_execution_log(execution, execution_id) -> tuple[AgentEvent, ...]` | 按产生顺序返回该次执行已保存的事件；服务端只允许读取同一 Session、同一 Request 下的执行。读取失败抛异常，没有事件时返回空元组，PA-16 两者都按“上一次记录缺失”处理 |
| `BudgetPort`（必需） | `admit` / `report` | 去掉 `unknown`：准入只有 `admitted / denied`，用量回报只有 `confirmed / rejected`；调用失败抛异常 |
| 窗口存储后端（必需） | `window_backend: BaseKVStore` | 构造时只检查是否为 `BaseKVStore` 实例，不读写存储；专用 SysOperation 后端不在本期 |
| `GroupChatPort`（可选） | `recent_messages(execution, cursor, limit)`、`search_messages(execution, query)` | 服务端按执行上下文里的 Session 定位群，不再单独传 `group` 参数。结果区分 `found / empty / incomplete` |
| `KnowledgePort`（可选） | `resolve` / `read` | 不变，继续使用 `MaterialQuery / MaterialSnapshot / MaterialReadResult`，由 PA-12 细化 |

删除 `ExecutionAuthority`、`ControlPort`、`ContextStore`、`HistoryPort`、`GroupMaterialPort`。构造时的必需依赖从“执行权 / 提交 / 控制 / 预算”改为“记账 / 预算 / 窗口存储后端”。

### 3. 事件

事件类型从 13 种收敛为 v2 §9.2 的 11 种：

| 事件 | 负载 | 与旧版的差异 |
| --- | --- | --- |
| `participation.decided` | 不变 | — |
| `execution.started` | `goal`、可选的 `decision_event_id` | 私聊和定时任务不做参与判断，`decision_event_id` 改为可选 |
| `progress.updated`、`reply.delta`、`model.completed`、`output.proposed` | 不变 | — |
| `tool.intent` | `operation_id`、`tool_call_id`、`tool_name`、可选的 `target_ref`、`params_digest`、`args_summary` | 增加可读参数摘要（敏感参数脱敏）和 `tool_call_id`；删除旧恢复协议用的 `tool_version`、`args_ref`、`authorization_ref` |
| `tool.completed` | `tool_call_id`、`tool_name`、`args_summary`、`status`、`result_summary`、`result_refs`、可选的 `operation_id`、`reason` | 所有工具都发；只读工具没有登记意图，所以 `operation_id` 可选；删除 `receipt_ref`。成功时需要结果摘要或结果引用，失败和未知需要原因 |
| `window.switched` | `archive_ref`、`window_seq` | 取代 `window.activated`；只包含旧窗归档引用和新窗序号，不定义完整归档结构 |
| `execution.paused` | `reason`、`intercepted`、`detail` | 合并 `control.waiting` 和 `execution.suspended`。`reason` 固定为五种：`awaiting_confirmation / stopped / budget_denied / context_unfit / error`；只有 `awaiting_confirmation` 携带被拦截的调用，且至少一项；`budget_denied`、`context_unfit`、`error` 必须说明原因 |
| `execution.final` | 不变，状态改为 `completed / ignored / cancelled / failed` | `failed-final` 改名为 `failed` |

删除 `checkpoint.proposed`。

`execution.paused(awaiting_confirmation)` 的 `intercepted` 是列表，每项带 `tool_call_id`、`tool_name` 和可读参数（敏感值脱敏）。原因是一轮模型回复里可能有多个并行工具调用同时被拦截，而确认恢复的输入要按 `tool_call_id` 与它们对应（v2 附录 C 第 2 条）。多个调用在卡片上怎么展示，由 AgentServer 决定。

`tool_call_id` 同时出现在 `tool.intent` 和 `tool.completed` 中。窗口里的工具调用、执行日志和续做时补齐的工具结果（v2 §11.2），都通过它对应起来。

事件的 `schema_version` 仍为 1。旧契约的事件从未在生产环境落盘，没有需要兼容的旧数据。`progress.updated` 和 `tool.completed` 中“每条约 500 字”的要求，由 PA-04、PA-14 在生成事件时控制；DTO 只保留现有的长度上限。

### 4. 数据结构的删改

- 删除 `CommitResult`、`AuthorityResult`、`ControlCommand`、`ControlResult`，以及 `schema/context.py` 中带版本和条件更新的 8 个窗口结构。
- 新增 v2 §7.9 的群消息结构：
  - `GroupMessage`：发送者身份（稳定 ID、显示名、人或 Agent）、服务端接收时间、正文、@ 的对象、回复的消息、附件引用、状态、是否属于排队中的请求。已撤回的消息不带正文。
  - `GroupMessageQuery`：关键词、时间范围、发送者、游标、条数。
  - `GroupMessagesResult`：`found` 至少带一条消息，可以带下一页游标；`empty` 表示已查完且确定没有；`incomplete` 表示没查完或查询失败，必须说明原因，可以带已拿到的部分消息，不能当作“没有”。
- 会话状态记录和归档的结构不在本项定义，分别由 PA-09、PA-10 定义。

## 拒绝的方案及原因

| 方案 | 不采用的原因 |
| --- | --- |
| 保留 `epoch` 留给将来跨进程部署 | 现在没有任何使用方。跨进程需要的是执行者代次加超时查询的整套协议（v2 附录 C），届时再整体设计 |
| 把 `record` 拆成“入队”和“落盘”两个方法 | v2 §14 规定是同一个接口，等待方式由事件类型决定、由服务端实现；拆开反而要求 Agent 自己判断该调哪个 |
| `GroupChatPort` 显式传入 `group` | 执行上下文里没有群标识，新增字段等于要求服务端再传一份可以由 Session 推出的信息；按 Session 定位与其他端口一致 |
| PA-22 一并定义会话状态记录和归档结构 | 没有使用方；会在 PA-09、PA-10 确定白名单字段和序列化方式之前就把格式定死 |
| 预算端口保留 `unknown` | 同进程调用失败直接抛异常，与 v2“不设计结果未知状态”的前提一致 |
| 把 `CommitPort` 改名为记账或日志端口 | v2 中它仍是“记账”接口，保留名称可以减少无关改动 |

## 对契约与状态的影响

- **外层公开导出**：
  - 删除 `ContextStore`、`HistoryPort`、`GroupMaterialPort`，8 个窗口结构，`CheckpointPayload`、`WaitingPayload`、`SuspendedPayload`、`WindowPayload`，以及 `CommitResult`、`AuthorityResult`、`ControlCommand`、`ControlResult`。
  - 新增 `GroupChatPort`、`GroupMessage`、`GroupMessageQuery`、`GroupMessagesResult`、`PausedPayload`、`InterceptedToolCall`、`WindowSwitchedPayload`。
- **不变的部分**：`create_persistent_agent` 的签名和行为不变，仍返回原生 `DeepAgent`；`spec.py`（`permissions` 归 PA-08）、`execution/binding.py`、`execution/stream_lifecycle.py` 不改；个人版不受影响。
- **对外部调用方的要求**：不再传入执行权和控制端口，改为必须提供 `window_backend`。

## 验证

按以下计划验证，实际结果登记在本节末尾：

1. **协议测试**：
   - 执行上下文拒绝 `epoch`、`authority_handle`，以及等于自身 `execution_id` 的 `recovery_ref`。
   - 事件只接受 11 种类型，被删除的 4 种类型被拒绝。
   - 五种暂停原因及其负载规则；`failed-final` 被拒绝。
   - `tool.intent` 拒绝旧字段；`tool.completed` 的成功证据规则。
   - 群消息的撤回规则，以及结果三种状态的区分。
   - 预算结果不再接受 `unknown`。
   - 删除的符号不再从外层包导出。
2. **构造测试**：
   - 缺少记账、预算端口或窗口存储后端时，在 DeepAgent 装配前失败。
   - 后端不是 `BaseKVStore` 时失败；可选的群聊端口形状不对时失败。
   - 构造过程不读写窗口存储后端。
3. **绑定测试**：
   - 测试替身去掉 `epoch` 和 `authority_handle`；原来依赖 `epoch` 制造“上下文不一致”的两个用例改用 `request_id`。
   - 其余行为不变。
4. **回归**：PA 包、F_01～F_03 使用的共享回归集、改动范围的 Ruff check 与 format 检查。

实际结果：

| 验证 | 结果 |
| --- | --- |
| `tests/unit_tests/harness/persistent_agent` | 161 passed：协议 87、构造 40、绑定 34（绑定测试数与 F_03 一致） |
| 共享回归集（`test_leaf_spec_build`、`test_spec_ownership`、`test_factory_model_anomaly_detection_rail`、`test_deep_agent_cold_build`、`test_deep_agent_workspace`、`test_ability_manager_add_ability`、`tests/unit_tests/agent/react_agent`） | 213 passed，与改动前相同 |
| Ruff check / format --check（`openjiuwen/harness/persistent_agent`、对应测试目录） | 通过 |
| 外层包导出 | 31 个导出全部可以解析；仓库中没有本包以外的代码引用被删除的符号 |
| mypy（改动的源文件） | 2 个报错，均为改动前已有：`events.py` 中按事件类型选负载模型的 `model_validate` 调用，以及 `build_context.py` 中 `derive` 的返回类型。与本特性无关，未修改 |

构造测试用一个所有读写方法都抛错的 `BaseKVStore` 替身作为窗口存储后端，证明构造过程不读写存储；绑定测试使用 `InMemoryKVStore`。

## 已知遗留

- 事件映射、记账调用和暂停收尾由 PA-04 实现。
- 意图记账由 PA-14 实现；工具只读 / 有副作用的声明来源仍待确认。
- 续做时补齐悬空工具结果由 PA-17 实现。
- 会话状态记录和归档结构由 PA-09、PA-10 定义。
- 容量边界的设计尚待在 v2 中补充（见计划 §3.4）。
