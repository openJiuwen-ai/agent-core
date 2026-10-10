# F_15 写操作执行前同步记账意图

| 项 | 内容 |
| --- | --- |
| 类型 / 状态 | feature；agent-core 实现进行中，JiuwenSwarm 持久化契约尚待验证 |
| 日期 | 2026-10-10 |
| 负责人 | Yan Zhijun |
| 范围 | `persistent_agent/schema/spec.py`；`persistent_agent/tools/effect_policy.py`；`persistent_agent/execution/tool_intent_rail.py`；Persistent Agent 与 Harness 定向测试 |
| 测试基线 | PA 包 325 passed、1 skipped；共享 ToolCallResilienceRail 11 passed；Ruff 检查与格式检查通过。2 个既有 Pydantic 弃用警告，JiuwenSwarm 持久化联调未验证 |
| 关联 | [架构设计](../designs/persistent-agent-architecture.md) §9.1、§9.2、§11；[开发计划](../designs/persistent-agent-development-plan.md) PA-04、PA-15、PA-16；[S_04 工具副作用与办公空间规约](../specs/S_04_tools-and-workspace.md)；[F_14 按调用授权读取办公空间文件](F_14_workspace-file-read.md) |

## 背景

PA-15 要保证：具有副作用的工具开始执行前，`tool.intent` 已经由宿主确认持久化。若进程恰好在外部操作执行后崩溃，续做时至少能发现有一项操作已开始、但还没有结果；不能因为日志缺失就把它当作从未执行。

开发计划要求以工具副作用属性区分只读和写操作，未声明的一律按写操作处理。当前 `ToolCard` 有既存的 `idempotent` 字段，但没有只读 / 有副作用字段。PA-15 采用 agent-core 内部维护的只读工具名称白名单作为唯一分类来源：仅按模型可见工具名做精确匹配，命中白名单才按只读处理，其余工具统一按有副作用处理。方案依赖每个模型可见工具名在一次调用中解析到唯一能力，不再额外检查工具实例或实现类。白名单是显式登记，不通过名称关键词、前缀或参数内容推测副作用。

事件结构和关键事件处理已经具备基础：`ToolIntentPayload`、`ToolCompletedPayload` 已定义；`EventEmitter` 已把 `tool.intent` 列为关键事件，等待该事件的 `CommitPort.record` 成功后才返回。PA-15 补上的是工具调用回调、分类、稳定关联和错误边界，不再新增一套事件通道或修改事件负载。

## 决策

### 1. 只读工具名称白名单是唯一的副作用分类来源

agent-core 内部维护精确工具名集合，例如：

```python
READ_ONLY_TOOL_ALLOWLIST = frozenset({"read_file", "list_files"})
```

初始条目对应当前 PA-14 暴露给模型的办公空间读取和列举工具。判断时只读取 Harness 实际暴露给模型、并出现在 `ToolCallInputs.tool_name` 中的工具名，按完整名称精确匹配；不检查 AbilityManager 当前绑定的工具实例或实现类。方案依赖 Harness 按工具名唯一暴露能力的注册语义；如果未来一个可见名称可以同时指向多个可选能力，再重新评估此分类约定。不得做大小写折叠、别名猜测、前后缀匹配或按名称包含 `read` / `search` 推断。

| 工具名称情况 | PA-15 分类 | 处理 |
| --- | --- | --- |
| 精确命中只读工具名称白名单 | 只读 | 不产生 `tool.intent`；调用结束仍产生 `tool.completed` |
| 已注册但未列入白名单 | 有副作用 | 执行前必须同步记账 `tool.intent` |
| 未知、自定义、MCP 或宿主新增工具 | 有副作用 | 执行前必须同步记账 `tool.intent` |
| 工具名称缺失或无法取得稳定调用身份 | 有副作用但无法安全记账 | 阻止工具执行并使当前 Execution 失败关闭 |

只有经过审查、确认调用不改变外部状态的工具能力才能加入白名单。PA-12、PA-23 等后续特性新增只读工具时，在相应特性的设计 / 实现中登记其确切暴露名称；登记之前按有副作用处理。JiuwenSwarm 不维护第二份运行时白名单。

### 2. 不修改 ToolCard 或 PersistentAgentSpec 公共字段

不新增 `ToolCard` 副作用字段，也不新增 Spec 配置项或宿主端口。Persistent Agent 构造时统一挂载 `PersistentToolIntentRail`；普通 DeepAgent 不受影响。这样所有经 Persistent Agent 工具调用回调执行的工具都经过同一策略，不能通过遗漏配置绕过默认写操作保护。

实现边界：

| 组件 | 职责 |
| --- | --- |
| `persistent_agent/tools/effect_policy.py` | 保存唯一只读工具名称白名单，按精确暴露名称判定 |
| `persistent_agent/execution/tool_intent_rail.py` | 在工具回调中登记写意图、完成结果并处理调用内去重 |
| `PersistentAgentSpec.resolve_parts` | 为 Persistent Agent 装配该 Rail，并将当前 `ExecutionBinding` 交给 Rail |
| 既有 `EventEmitter` / `CommitPort` | 继续作为唯一事件持久化通道；不修改事件 schema |

内部模块不新增公共导出。工具事件只能从 PA-04 的正式执行入口使用；若 Rail 发现当前绑定没有可用 emitter，白名单工具仍可按只读流程执行，其他工具必须阻止执行。

### 3. 写工具的调用顺序

```text
收到工具调用
  → PermissionInterruptRail 等既有授权 / 确认 Rail 完成判定
  → 精确查只读白名单
      ├─ 命中：执行工具
      └─ 未命中：生成 operation_id、参数摘要和参数哈希
                 → await EventEmitter.emit(tool.intent)
                 → CommitPort 确认本条意图已持久化
                 → 执行工具
  → 工具最终返回或最终失败时，产生 tool.completed
```

权限 Rail 要求确认、拒绝调用或请求结束当前执行时，不应提前为尚未获准执行的工具记一条“即将执行”的意图。`PersistentToolIntentRail` 使用 `callback_priority(event)` 单独安排回调位置：`before_tool_call` 排在权限 / 中断判定之后，作为最后一道 pre-dispatch 屏障；它检查是否已有 force-finish 请求。只有调用仍会进入工具执行路径时，才设置本次 `AgentCallbackContext.extra` 中的 dispatch 标记。标记仅在只读身份判定通过或写意图成功落盘后设置。确认恢复后，只有当本次工具调用实际进入执行路径时，才走同一个意图屏障；获准的写操作仍须先落盘 `tool.intent`。

`tool.intent` 使用已有 `ToolIntentPayload`：

- `tool_call_id`：原生工具调用 ID；缺失时写操作失败关闭。
- `operation_id`：由当前 `request_id` 和 `tool_call_id` 稳定派生，使同一 Request 内的回调重试和确认恢复能复用同一标识；不使用每次 Execution 都变化的随机值。
- `tool_name`：模型实际调用的工具名。
- `target_ref`：能从受控参数中可靠识别目标时填写；无法识别时为 `None`，不通过猜测填入错误目标。
- `params_digest`：对规范化工具参数做 SHA-256，供同一调用重试时校验参数未变化。
- `args_summary`：复用现有参数脱敏规则生成可读摘要；敏感字段值不写入事件。

同一 `tool_call_id` 再次进入回调时，先校验工具名和 `params_digest`。相同调用已成功登记意图时，不重复发出 `tool.intent`；名称或参数哈希变化时视为调用身份冲突，阻止执行，不把它静默当成原操作的重试。新的模型工具调用 ID 表示新的操作，使用新的 `operation_id`。

### 4. 意图失败必须 fail-closed

调用 `EventEmitter.emit(TOOL_INTENT, ...)` 必须 `await`。只有 `CommitPort.record` 返回成功，Rail 才放行工具执行。若记账失败：

1. 依赖既有 `EventEmitter` 将关键事件失败登记到 `ExecutionControl.fatal` 并抛出异常；
2. 不调用底层工具，不把异常改写成普通工具失败后继续执行；
3. 即使 Harness 原生回调层捕获了异常，后续工具 / 模型调用仍被 Persistent 控制 Rail 阻止，执行入口最终重新抛出原始 fatal 错误；
4. 不把失败降级成 `execution.paused(error)`，也不在 PA-15 中自动重试工具或查询外部结果。

这条规则依赖现有 `EventEmitter` 和执行入口的 fatal 传播机制；PA-15 要增加回归验证，确保工具调用回调不会吞掉关键错误。

### 5. `tool.completed` 记录最终工具调用结果

经 PA-04 正式执行入口调用时，所有工具（包括白名单只读工具）最终都按现有 `ToolCompletedPayload` 产生 `tool.completed`。只读工具的 `operation_id` 为 `None`；写工具关联其前序意图的 `operation_id`。低层绑定不带正式事件 emitter 时，白名单只读调用仍可运行但不会产生持久事件；其他工具失败关闭。事件是普通记录，提交失败按既有架构只记诊断，不改变已完成工具调用的业务结局。

结果状态按以下规则确定：

| 状态 | 判定 |
| --- | --- |
| `succeeded` | 工具正常返回；摘要反映返回结果，不将工具返回直接视为 Request 已完成 |
| `failed` | 能确认工具未产生副作用，或工具返回明确的业务失败结果 |
| `unknown` | 超时、断连或异常发生在外部调用可能已发出之后，无法确认是否生效 |

写工具异常时，无法证明“没有副作用”就按 `unknown`，不能仅凭异常类型把它写成 `failed`。失败 / 未知状态填写非空 `reason`。每种状态都提供非空 `result_summary`，摘要控制在约 500 字以内；结果引用可选，不能替代摘要。参数与结果摘要均限长并脱敏，不写入凭证或原始大段输出。

`after_tool_call` 只在 dispatch 标记已设置时产生 `tool.completed`，防止权限中断或前置记账失败后把未执行的调用写成已完成。该回调应在工具结果转换回调之前读取原始工具结果，并确保自己的普通事件记录失败不阻断工具调用结局。每次工具调用使用独立的回调上下文状态，不用 Rail 实例上的共享可变字典，避免并行工具互相覆盖。原生 `@rail` 重试会跳过中间尝试的 `after_tool_call`，最终成功或最后一次失败才进入收尾回调。PA-15 在最终结果处只产生一条 `tool.completed`，不把一次可重试的中间超时记成已结束结果。执行被取消而原生框架跳过收尾回调时，前序 `tool.intent` 保留为未决；PA-15 不伪造结果，由 PA-18 续做流程查证。

### 6. 与原生幂等重试的边界

只读白名单只决定是否需要 `tool.intent`，不承担幂等性声明。继续使用现有 `ToolCard.idempotent` 作为重试安全声明：仅显式声明 `idempotent=True` 的工具允许由 `ToolCallResilienceRail` 重试；默认 `False`、未声明或无法取得 ToolCard 的工具不重试。PA-15 不新增 ToolCard 字段。

同一写工具调用的安全重试必须沿用同一 `operation_id` 和同一参数哈希，不重复记意图。若当前 Resilience Rail 对缺失 ToolCard 的工具仍视为可重试，应在本特性范围内调整为默认不重试，并增加测试；不得影响个人版 DeepAgent 的既有行为。PA-15 不实现外部查询、取消、补偿或未知结果自动核对，这些归后续恢复特性处理。

### 7. JiuwenSwarm 适配边界

**白名单分类本身不要求 JiuwenSwarm 增加适配器或维护副作用表。** 未在 agent-core 白名单的 JiuwenSwarm 工具会按有副作用处理，安全上仍要求先记意图；代价是它的只读工具也会多产生意图记录。当前 PA-14 工具名称为 `read_file`、`list_files`，由 agent-core 初始白名单按名称识别。

**完整 PA-15 保证要求宿主满足同步持久化契约。** JiuwenSwarm 继续通过既有 `PersistentPorts.commit.record(execution, event)` 接收事件：

1. 对 `tool.intent`，`record` 返回前必须确认本条事件已可靠持久化；若失败须抛出异常，令 agent-core 阻止对应工具执行。
2. 同一事件重放按 `event_id` 幂等处理，避免宿主收到重试时重复建账。
3. 对 `tool.completed` 等普通事件，允许按架构设计入队后返回；不需要把它升级为执行屏障。
4. 事件按既有 Session / Request / Execution 归属保存。服务端不得把 `tool.intent` 解释为操作已成功；最终结果以对应 `tool.completed` 为准，缺少结果时由 PA-18 处理。

如果 JiuwenSwarm 当前 `CommitPort` 实现已经对关键事件做同步持久化，则不用为白名单再改宿主代码；如果它只是把事件放入内存队列后立即返回，就必须补上关键事件持久化确认。这是可靠意图记账的宿主前提，与“工具分类表放在哪里”是两个问题。JiuwenSwarm 暂时不需要 PA-14 的办公空间端口适配才能开发或验证 PA-15。

## 拒绝的方案及原因

| 方案 | 不采用的原因 |
| --- | --- |
| 给 `ToolCard` 新增只读 / 有副作用字段 | PA-15 当前只需确定工具是否要先记意图；扩展公共 Card 会影响工具创建者、序列化和已有集成方。选择一份 agent-core 内部白名单即可满足当前需求 |
| 只把已知写工具列入黑名单 | 新工具、MCP 工具或宿主自定义工具容易漏列，可能绕过意图屏障；默认写的白名单规则更稳妥 |
| 按 `read`、`search` 等名称关键词推断 | 名称不能证明行为，混合读写工具也可能误放行；只接受精确登记 |
| 白名单同时校验工具实现类 | 当前按工具名唯一解析能力的注册语义即可分类；实现类校验会把 PA-15 与 PA-14 的内部实现耦合，现阶段不增加这层检查。若注册约定改变，再重新评估 |
| 让 JiuwenSwarm 单独传入分类表 | 会形成两份分类来源，宿主配置遗漏时行为不同；本方案由 agent-core 一处维护唯一表 |
| `tool.intent` 记账失败后继续执行或降级为普通错误 | 会重新出现“操作已经发生却没有任何意图记录”的崩溃窗口 |
| 在 PA-15 内自动重试或查询写结果 | 工具可能已在超时前产生副作用；未知结果查证和恢复由 PA-18 负责 |
| 用工具异常直接断言写操作失败 | 超时 / 断连不表示外部操作没有发生；无法确认时必须记录 `unknown` |

## 对契约与状态的影响

- 不修改 `ToolCard`、`AgentEvent`、`ToolIntentPayload`、`ToolCompletedPayload`、`PersistentPorts` 或 `ExecutionContext` 的公共字段。
- 不新增 Spec 开关；Persistent Agent 统一安装意图 Rail，普通 DeepAgent 不变。
- 新增 agent-core 内部只读工具名称白名单，初始只包含 PA-14 已暴露的 `read_file`、`list_files`；名单外全部视为写操作。
- `tool.intent` 与 `tool.completed` 继续使用现有事件类型和统一 `CommitPort.record` 通道；关键 / 普通事件等级沿用架构 §9.1。
- 只读工具也会产出 `tool.completed`，写工具的完成事件带回对应 `operation_id`。
- `ToolCard.idempotent` 继续只描述可否安全重复调用，不用于副作用分类。
- PA-14 不是 PA-15 的硬前置；PA-15 的硬前置仍是 PA-04。PA-16 的办公空间修改只有在 PA-14 授权读取与 PA-15 意图记账都可用后才能开放。
- 在 JiuwenSwarm 的 `tool.intent` 事件确认持久化未验证前，不应宣称跨进程崩溃下的写操作具备可靠意图记账，也不应开放依赖该保证的写工具。

## 验证

已运行的验证：

| 验证项 | 覆盖内容 |
| --- | --- |
| 白名单判定 | `test_effect_policy.py` 覆盖 `read_file` / `list_files` 精确匹配及未知、自定义、MCP、大小写和近似名称的默认写分类；分类只按模型可见工具名 |
| 写前顺序、授权恢复与失败关闭 | `test_entries.py` 覆盖意图持久化先于工具调用、确认等待和拒绝时不记账、确认后先记账，以及关键事件持久化失败时工具调用次数为零并由入口抛出 fatal |
| 并行与关联 | `test_tool_intent_rail.py` 覆盖并行兄弟调用等待事件锁期间遇到执行 fatal 后不 dispatch、operation_id 跨 Execution 稳定、参数 JSON 顺序规范化、同调用重试不重复记账及身份 / 参数变化阻止重试 |
| 重试与完成事件 | `test_tool_intent_rail.py` 覆盖只有明确 `idempotent=True` 才保留原生重试请求；`test_entries.py` 验证真实原生超时重试复用同一意图且只产生最终完成事件。还覆盖只读 / 写事件关联、成功 / 失败 / 未知状态、摘要限长与敏感字段脱敏，以及普通完成事件失败不替换工具结果 |
| Rail 装配边界 | `test_workspace_read_rail.py` 验证 Persistent Agent 始终装配该 Rail，普通 DeepAgent 不装配；`test_binding.py` 验证低层直接绑定的写工具在缺少 emitter 时失败关闭 |
| 测试结果 | `tests/unit_tests/harness/persistent_agent`：325 passed、1 skipped；`tests/unit_tests/core/single_agent/test_tool_call_resilience_rail.py`：11 passed。Ruff lint / format 检查通过 |
| 未覆盖 / 对端待验 | 取消后未决意图由 PA-18 查证；JiuwenSwarm `CommitPort.record` 的真实持久化确认、跨进程崩溃恢复及完整端到端联调仍未验证 |

## 已知遗留

- agent-core 内部白名单初始只登记 `read_file`、`list_files`；JiuwenSwarm 各部署环境中其他只读工具仍按有副作用处理，若需免记意图须先在 agent-core 审核登记。
- agent-core 已实现 PA-15 Rail 并完成本地单测；目前尚未验证 JiuwenSwarm `CommitPort.record` 对 `tool.intent` 是否提供真正的持久化确认，也未完成跨仓联调。
- 低层 `invoke_bound` / `stream_bound` 不带正式事件 emitter 时，只读白名单工具仍可执行且没有事件记录；生产入口须使用 PA-04 正式执行 API。该入口约束和 fail-closed 行为已有本地覆盖。
- PA-15 不负责检查写入后的外部回执、查询未知结果、恢复未完成写操作、文件冲突或任务完成交接；分别由 PA-18、PA-16 等后续特性覆盖。
- PA-15 的 agent-core 组件实现及单测通过，不代表 JiuwenSwarm 已支持跨进程持久化保证，也不代表 PA-16 可开放。待 JiuwenSwarm 关键事件确认与 PA-18 查证流程联调后再评估放行。
