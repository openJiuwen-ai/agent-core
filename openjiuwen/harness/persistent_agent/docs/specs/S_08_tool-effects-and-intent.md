# S_08 工具副作用、意图屏障与结果规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | S_08 / tool-effects-and-intent |
| 关联模块 | [effect_policy.py](../../persistent_agent/tools/effect_policy.py)、[tool_intent_rail.py](../../persistent_agent/execution/tool_intent_rail.py)、[Spec 装配](../../persistent_agent/schema/spec.py) |
| 最近一次修订日期 | 2026-10-12 |
| 关联 feature | [F_15](../features/F_15_tool-intent-before-side-effect.md) |
| 设计依据 | [架构设计：写意图及确认](../designs/persistent-agent-architecture.md#62-事件传递确认与流量边界)、[外部操作](../designs/persistent-agent-architecture.md#82-外部操作与重试条件) |

本规约从 S_04 分离，唯一维护所有 Persistent 工具的副作用分类、写前记账、调用内去重、原生重试及完成事件产生条件。

## 1. 范围、术语与适用条件

PersistentToolIntentRail 和名称白名单已有实现，Spec.resolve_parts 统一安装，无额外开关或宿主分类端口。普通 DeepAgent 不安装。能力基于 Harness 的工具回调和按名称唯一暴露能力的注册语义，不另建工具运行时或权限引擎。

正式执行 emitter 由 S_01 提供；事件字段、可靠性及 CommitPort 确认由 S_02 唯一维护。本模块只决定何时产生事件及何时放行工具；办公空间授权和路径由 S_04 负责，取消 / 续做查证由 S_05 负责。宿主未验证 tool.intent 的真实持久化确认前，不宣称跨进程崩溃下意图可靠。

## 2. 操作身份与事实归属

白名单仅包含模型可见的精确工具名 read_file、list_files，定义在 effect_policy.py。不折叠大小写、不匹配前后缀、不按 read / search 关键词或实现类猜测；未知、自定义、MCP 或宿主新增名称都按写操作处理。白名单维护在 agent-core 一处，宿主不维护第二份表；后续只读名称须在相应实现特性审查登记。

| 事实 | 定义与持有者 |
| --- | --- |
| tool_call_id | 原生调用身份，缺失或工具名非法时不能安全放行 |
| operation_id | op_ + namespace / Agent / Session / Request / tool_call_id 规范 JSON 的 SHA-256；不含 execution_id，可跨同 Request 的确认恢复稳定复用 |
| params_digest | 规范化参数的 SHA-256，用于同调用身份冲突检查，不是可恢复参数正文 |
| args_summary | 脱敏、限长的可读摘要；不保存明文凭证 |
| 调用内状态 | 独立回调上下文 extra 中的已登记、已派发、已完成等事实，不放 Rail 共享可变字典 |
| 外部效果 | 由文件 / 外部系统实际状态及可查证回执决定；意图、日志和摘要均不能独立证明 |

ToolIntentPayload / ToolCompletedPayload 只使用 [S_02](S_02_events-and-result-handoff.md#2-数据结构与事实归属) 的字段，不新增 tool_version、args_ref、authorization_ref 或外部能力 DTO。目标能从受控参数可靠识别时才填写 target_ref，不猜造目标。新的模型工具调用 ID 表示新操作，不承诺自动识别为原业务操作。

当前参数与结果摘要各截取最多 500 字符；键名不区分大小写，含 password、secret、token、key、credential、authorization 的值替换为 `***`，嵌套对象与列表递归处理。摘要只用于展示和冲突核对，不能由脱敏摘要反推出重试参数，也不能把这套键名过滤写成对任意正文秘密的完整检测保证。

## 3. 不变量

1. **I-1 默认写：** 未精确命中白名单的调用按有副作用处理，不能因分类遗漏绕过意图屏障。
2. **I-2 授权在先：** PermissionInterruptRail 等权限 / 确认及已有 force-finish 判定完成后，工具仍将实际 dispatch 时才记写意图；拒绝 / 等待不预记未执行意图。
3. **I-3 写前落盘：** 对写调用 await emitter.emit(tool.intent) 成功后才允许底层执行；失败登记 fatal、阻止工具，不改写成可继续的普通失败。
4. **I-4 同调用一致：** 重入时工具名与 params_digest 必须一致；已登记意图不重复发出，冲突失败关闭。安全重试保留同一 operation_id，不生成随机新操作绕过核对。
5. **I-5 完成真实：** 只有通过 dispatch 屏障的调用才能产生完成事件；未知外部结果不能写成明确未执行。

## 4. 接口与协作协议

### 4.1 分类与低层调用

| 条件 | 行为 |
| --- | --- |
| 精确命中白名单且调用身份有效 | 不记意图，实际执行后按 emitter 情况记完成 |
| 名称未列入白名单 | 写意图先落盘；只有可靠确认后才放行 |
| 调用 ID / 名称非法，参数无法规范化 | 失败关闭，阻止工具 |
| 无正式 emitter | 只读白名单工具可运行且不产生持久事件；写工具失败关闭 |

低层 invoke_bound / stream_bound 没有 emitter 时不能作为可靠写入口。白名单只判断是否需要意图，不等于 ToolCard.idempotent 声明或授权。

### 4.2 回调顺序与意图失败

before_tool_call 排在权限 / 中断和模型 Rail 停止检查之后，检查当前 force-finish / fatal / stop；等待记账期间兄弟调用产生 fatal 时，dispatch 前还需复核。只读判定通过或写意图成功落盘后才设置本调用 dispatch 标记。

记账使用唯一 EventEmitter / CommitPort；关键失败由 emitter 保留 DEEPAGENT_RUNTIME_ERROR。即使原生回调捕获异常并形成错误结果，后续模型及工具仍被阻止，入口重抛原 fatal。不得将其降级为 paused(error) 或在本 Rail 自动重新执行写工具。

### 4.3 `tool.completed` 的产生与结果

after_tool_call 在工具结果转换之前读取原始结果，仅在 dispatch 标记已设置、尚未完成且有 emitter 时提交一条最终完成事件。只读操作 operation_id 为空，写操作关联前序意图 ID；所有状态均有非空白结果摘要，failed / unknown 另有 reason。结果和参数摘要限长、脱敏，不能由可选结果引用替代可读摘要。

| 实际观察 | 状态 |
| --- | --- |
| 工具正常返回，且没有显式失败结果 | succeeded；不表示 Request 已结束 |
| 工具返回明确业务失败结果；或只读调用异常 | failed |
| 写工具异常，无法确认外部效果 | unknown，不能仅凭异常类型断言未执行 |

tool.completed 是普通日志，构造或 record 失败只诊断，不能替换已完成的原生工具结果或阻止依赖步骤等待“完成回执落盘”。取消使原生框架跳过收尾回调时，不伪造结果，前序意图保持未决，由 S_05 查证。

### 4.4 原生重试

只允许能取得 ToolCard 且 idempotent **显式为 true** 的工具保留 ToolCallResilienceRail 的重试请求。默认 false、未声明、缺少 ToolCard 或当前 fatal 时清除重试请求。原生重试跳过中间尝试的 after_tool_call；最终成功或最后失败才产生最终完成事件，同一重试沿用意图和参数哈希。

本规则不声明外部系统提供幂等键、查询、取消或补偿能力；写异常虽可在明确幂等时安全重试，仍不能把未知结果自动改成“未执行”。个人版 DeepAgent 的既有重试行为不由本 Rail 改写。

## 5. 副作用与取消生命周期

实际流程为：权限 / 确认判断 → 检查停止与调用身份 → 分类 → 写操作同步意图 → 复核能否派发 → 底层工具 → 最终完成事件。意图可靠保存与实际外部执行之间没有跨系统事务；有意图无结果可以是取消、崩溃或普通日志缺失，不推断操作未执行。

程序级外部查询、取消、补偿、精确重放与文件冲突不属于 PA-15。取消不自动授予补偿权，也不撤回在途请求；后续继续须按 S_05 查证源状态，查不清询问发起人。子代理执行模式仍未开放，不能据此承诺父子事件 / 预算及外部副作用已完整汇聚。

## 6. 当前限制与替代关系

按名称分类依赖 Harness 当前注册语义，不验证实现类；注册语义变化时须重新评估。宿主其他只读工具未登记前也会记录意图，这是默认写的边界。PA-15 已有本地组件实现不代表 PA-16 可开放，后者仍依赖授权与文件修改适配。

本文替代原 S_04 的通用工具声明、恢复参数保存、租约围栏及“结果记账后才能推进”协议；当前只维护实际 DTO、名称白名单和单进程调用屏障。旧扩展方案不作为现行实现承诺，历史理由见 F_15。

## 7. 关联规约与验证索引

S_02 定义意图与完成等级及其 Schema；S_01 提供 emitter 和 fatal 传播；S_07 统一装配与 owner；S_04 提供初始只读工具；S_05 负责取消及未决操作继续策略。

- 分类精确匹配：[test_effect_policy.py](../../../../../tests/unit_tests/harness/persistent_agent/test_effect_policy.py)。
- 写前等待、重入去重、冲突、未知状态和显式幂等：[test_tool_intent_rail.py](../../../../../tests/unit_tests/harness/persistent_agent/test_tool_intent_rail.py)。
- 确认后记意图、原生重试、fatal 和普通完成失败：[test_entries.py](../../../../../tests/unit_tests/harness/persistent_agent/test_entries.py)。
- 缺 emitter 的低层写调用及普通 DeepAgent 边界：[test_binding.py](../../../../../tests/unit_tests/harness/persistent_agent/test_binding.py)、[test_workspace_read_rail.py](../../../../../tests/unit_tests/harness/persistent_agent/test_workspace_read_rail.py)。
- 真实 CommitPort 持久化确认、跨进程崩溃与未决操作续做仍需宿主验证，完整证据只在 F_15 / 唯一开发计划维护。
