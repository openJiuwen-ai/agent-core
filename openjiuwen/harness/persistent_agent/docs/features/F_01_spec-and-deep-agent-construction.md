# F_01 Persistent Spec 与 DeepAgent 装配接入

编号说明：本文保留开发当时的历史 PA 编号；与当前计划对照时，旧 PA-22 对应 PA-04，旧 PA-04～PA-21 对应 PA-05～PA-22，其余编号不变。旧版架构内容可从 Git 历史查阅。

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 / 状态 | feature；PA-01 契约层与 PA-02 DeepAgent 构造已独立验收完成；PA-04 执行闭环待后续开发 |
| 日期 | 2026-09-29 |
| 负责人 | yan_zj |
| 范围 | PA-01 / PA-02；定义 PA-03 / PA-04 接入边界，不代替其各自验收 |
| 测试基线 | Persistent Agent 132 项及共享 Harness 582 项定向回归通过；ReAct / 默认个人版回归 136 项通过 |
| 关联 spec | [S_01](../specs/S_01_public-api-and-execution.md)、Harness [构造规约](../../../docs/specs/S_01_public-api-and-construction.md) |
| 背景分析 | [迁移分析](../designs/jiuwenswarm-migration-analysis.md) |

## 1. 决策与范围

用户明确运行主体保持 `DeepAgent`。Persistent 是可组合的策略、Rails、工具和可靠交接能力，**不新增 PersistentAgent 类、DeepAgent 子类、Agent 包装门面或第二套任务循环**。

配置采用用户确认的继承结构：`PersistentAgentSpec(DeepAgentSpec)`。只继承配置描述，不继承运行类；一份扁平 Spec 提供通用及专属字段，不包含 `deep_agent` 子对象，也不要求调用方同时传两份 Spec。

本阶段已实现 Spec、调用级构造上下文、工厂、端口形状检查及工具 owner 接缝。窗口切换、历史扫描、故障恢复、飞书配置和实际投递不在本阶段实现。构造相关类型可通过外层包 import；执行适配仍未实现。

## 2. 入口与执行接入

```text
create_persistent_agent(spec: PersistentAgentSpec, *, context: PersistentBuildContext)
    -> DeepAgent
execute_persistent_request(agent: DeepAgent, input, *, context: PersistentBuildContext)
    -> AsyncIterator[AgentEvent]
```

第一项是工厂；第二项是后续 PA-04 的薄执行适配函数，负责来源 / 调用校验、只读判断或恢复前置、稳定事件与提交交接。其内部仍调用 DeepAgent 公开 `stream/invoke` 及控制入口，不另建模型循环。`input` 是宿主已保存的 Request 内容值，不新建业务 Request。

工厂返回值保留已有 DeepAgent API；原生 API 可调用不表示已通过 Persistent 业务协议。宿主的可靠链路必须走执行适配及受控能力；不能动态增加 Agent.execute、替换 stream 或把流结束伪装成已提交终态。构造和执行固定到同一调用绑定，恢复新 Execution 须新构造。

## 3. 可序列化 Spec

| 部分 | 字段与约束 |
| --- | --- |
| `schema_version` | 首版 `1`；未知版本拒绝 |
| 继承的通用字段 | 模型、Card、system_prompt、工具、MCP、子代理、Rails、Workspace、Skill、cwd / project_root 等直接继承 DeepAgentSpec，不在子类重复声明 |
| `enabled_features` | 首阶段只接受非空集合 `{construction}`；未知或未实现能力拒绝；不声明直接任务可靠闭环可用 |

当前 Spec 只增加有实际校验用途的 `schema_version` 与 `enabled_features`。Spec 不包含 Session 对象、宿主端口、锁、取消令牌、当前执行权或运行状态。未实现的运行时策略不提前做成配置项；队列、提交时限、工具收敛期限、群参与策略及快照兼容选项应在对应能力有消费者时再加入。DeepAgent 已有的模型 / 工具重试配置直接继承，不重复定义。

沿用父类通用字段及默认值；Persistent 额外校验只约束其启用能力与配置组合，不能改动父类供个人版使用的默认行为。序列化时通用和专属字段处于同一级，校验与配置指纹均须覆盖专属字段，不能转成父类后将它们静默丢弃。

每次构造使用配置副本和新工具 / Rail 实例，不修改宿主发布配置。指纹按双方约定的规范化发布描述核验，不散列运行句柄。Spec 可序列化不代表可以公开日志记录其中的模型敏感配置；飞书凭据通过宿主能力绑定，不加入此 Spec。

## 4. 调用级构造上下文

PersistentBuildContext 复用 / 扩展已有 BuildContext，是不参与 JSON 的运行载体，不是 Agent 类。

| 部分 | 内容 |
| --- | --- |
| `execution` | 不可变 ExecutionContext：namespace、Agent、Session、Request、Execution、原配置版本 / 指纹、主体、启动权限上限、来源范围、epoch 和恢复引用 |
| `session` | 宿主管理的真实 Session，ID 必须与 execution 一致；不用隐式 default Session |
| `ports` | PersistentPorts：执行权、提交、控制、预算及按启用能力提供的 ContextStore、History、知识、群资料和办公空间访问 |
| `tool_owner_id` | 按可信执行身份分配的独立注册归属；Card 不变，owner 不复用其他活动调用 |
| 调用绑定 | agent 与 context 固定关联同一 Session / Execution / 配置；执行适配核验关联，不能替换 context 重绑身份 |

动态授权和控制经端口查询，不原地修改 execution。BuildContext.derive 是浅复制：须复制 extras 容器并新建调用可变载体，只有经验证安全的只读依赖可共享。工厂不授予 / 续租执行权，不关闭宿主 Session。

## 5. 装配顺序与最小公共接缝

```text
宿主已受理 Request、固定配置、取得执行权
  → PersistentBuildContext
  → create_persistent_agent：静态校验身份 / 配置 / 能力组合
  → PersistentAgentSpec.build：在副本上声明能力、绑定运行依赖
  → super().build(context)：复用 DeepAgentSpec 的装配
  → 既有 resolve_deep_agent_parts / apply_deep_agent_parts
  → DeepAgent
```

当前 DeepAgentSpec.build 与 create_deep_agent 共用 parts / Config 装配，并非直接调用关系。PersistentAgentSpec.build 委托专用工厂；工厂在 JSON 往返副本上通过 `DeepAgentSpec.build(snapshot, context=working)` 复用父类路径，并要求构造绑定已被本次工厂 claim。不在 Persistent 内增加直接 new DeepAgent 分支，也不先建模板再复制第二个对象。程序式构造仍走原工厂。

构造不调用模型、不读群公告、不安装 Skill、不创建 Request / Window、不提交业务事件或投递。已有工具注册若部分失败，按 owner 回收本次新增资源，不全局清理或删除持久化数据；不借 auto_create_workspace 隐式迁移生产目录。执行前通过异步端口复核当前授权和执行权；同步结构校验不是有效租约证明。Rail 首次异步初始化沿用原生命周期。

已给通用 BuildContext 增加可选 tool_owner_id（默认 None），DeepAgentSpec.resolve_parts 在注册前传入 parts / Config。实际工具 ID 资格化发生在 AbilityManager.add_ability：stateful 使用 name_owner，stateless 保持共享 ID。BuiltinToolSpec 的 tool_id 保留参数不转交 provider，因此不靠 provider 前缀隔离。未设置 owner 保持原 Card 行为；不修改 Card 或构造后的私有字段。

## 6. 依赖、错误和降级

| 能力 | 必须验证的保证 | 不满足时 |
| --- | --- | --- |
| 直接任务可靠闭环 | 执行权、事件确认、控制、预算、可信来源和配置；窗口 / 历史依赖按启用范围明确提供 | 缺必需端口拒绝；超时 / 未知暂停，不伪造完成 |
| 正式窗口 | ContextStore 不可变版本、head 条件更新、有效执行权围栏、稳定写入查询 | 禁用相应能力；旧 JSONL 写成功不等价 |
| 历史 / 群资料 | 当前 Session 私有读取；跨群取 Agent 与当前主体的授权交集，有界引用和完整性 | 越权拒绝；不可用不转空数据 |
| 知识 | KnowledgePort 一致版本及撤权传播 | 启用但不可用时明确失败，不用失效知识 |
| 写工具 / 恢复 | 操作 intent / receipt、围栏、未知结果查询；恢复需原配置、Checkpoint、完整事件尾部及有效 head | 不开放对应写能力 / 自动恢复，不从新任务重跑 |

端口由可信宿主注入，不是模型参数。后端的能力声明用于配置检查，不能代替故障测试。只读阶段还须限制 shell、CLI、MCP 和子代理副作用，不能只过滤几个写工具名称。

错误区分无效归属、配置不匹配 / 不可用、能力未实现、端口缺失、并发 / 重复使用绑定及执行权失效，映射已有 StatusCode。构造失败无业务事件；有效执行开始后的错误按 S_02 交接。

## 7. 第一阶段开发与验收清单

| 顺序 | 产物 | 验收 |
| --- | --- | --- |
| A | 继承型 Spec、专属字段、构造上下文、端口协议、测试替身 | 扁平字段序列化往返；专属字段参与指纹 / 校验；句柄不进入 Spec；不修改调用方对象 |
| B | owner 贯穿 Spec 的公共接缝 | 相同 Card 的两个 owner 不覆盖工具；缺省 owner 的旧消费者行为不变 |
| C | 工厂和能力 providers | 严格返回 DeepAgent；无模型 / 投递；独立 Rails / 工具；构造失败只清理本调用 |
| D | 执行接入协议及测试骨架 | 事件消费与确认并发、有界；不死锁；不提前标记 PA-04 完成 |

构造已验证 Session / Card / 配置错配、context 替换、浅复制污染、owner 清理范围及默认 Spec 回归。隐藏副作用入口、原生流与业务终态区分、未知提交和执行前动态校验在后续阶段验证，不列为本阶段已完成。

## 8. 拒绝的方案与遗留

拒绝新 Agent 门面 / 子类、复制全部 DeepAgentSpec 字段、构造后补 owner、将运行端口放 Spec 或 query：分别导致执行入口重复、配置漂移、注册碰撞和授权边界失效。

早先的嵌套组合与两份平级 Spec 方案不再作为当前设计：用户选择单份扁平配置，采用配置继承即可沿用通用字段。此配置子类不改变最终 Agent 类型，工厂仍返回原生 DeepAgent。

轮末切窗与 S_03 安全点切窗的产品策略差异仍独立待确认，本次不改时机。工厂完成也不表示窗口、群判断、恢复、学习已接入，或宿主端口具备生产保证。

## 9. 本次执行记录（第一阶段）

按 A → B → C 顺序采用测试驱动实现，D 的可靠执行适配留给后续阶段。保持用户指定的当前分支，不提交代码。

Ruling: 第一阶段 `enabled_features` 仅接受 `construction`，不声称支持直接任务可靠闭环；否则工厂会暗示尚未实现的事件确认与执行权围栏已经生效。后续新增能力须先实现并验证相应端口使用。

Ruling (2026-09-29): 删除了此前草案中的 `participation_policy`、`event_queue_capacity`、`event_commit_timeout`、`tool_shutdown_timeout` 和 `supported_snapshot_versions`。代码中只有构造时保存、没有运行时消费者；等对应的决策、交接、取消收敛或快照恢复能力实现后，再按需补回最小配置。

Ruling: 发布配置以可 JSON 序列化的完整 Spec 规范化并计算 SHA-256；不可序列化的通用扩展配置拒绝构造，而不是静默丢弃。配置版本由宿主提供。

构造端口先定义最小异步形状：authority.check、commit.submit/query、control.poll、budget.admit/report；工厂只检查形状，不调用这些方法。业务事件及返回值的可靠性契约仍在 PA-04 落地，不提供成功型默认实现。

自动生成唯一 tool_owner_id，不允许调用方通过此上下文复用 owner；同一构造上下文只能尝试构造一次。失败只回收本次 parts 中已经注册的独立 stateful 工具，不清理共享工具或宿主 Session。自定义 provider 必须是可信、构造期无外部业务副作用的装配代码。

Workspace 由宿主预先提供；Persistent 配置存在 workspace 时要求 auto_create_workspace=False 且根目录已存在，避免构造隐式创建或迁移生产目录。普通 DeepAgent 默认值不变。

进度：已按先 RED 后 GREEN 验证构造行为；新增 27 项构造单测通过，扩大后的定向回归 104 项通过。独立审查及最终验证见第 10 节。

## 10. 实现结构与验证证据

```text
openjiuwen/harness/persistent_agent/
├── __init__.py                 # 5 个构造符号的懒加载导出
├── persistent_agent/
│   ├── __init__.py
│   ├── factory.py              # 静态校验、配置副本、原 Spec 构造与失败回收
│   ├── ports.py                # 宿主异步端口形状，无默认成功实现
│   └── schema/
│       ├── __init__.py
│       ├── spec.py             # PersistentAgentSpec(DeepAgentSpec)
│       ├── build_context.py    # 调用载体、一次性绑定与不可变策略快照
│       └── execution.py        # 不可变身份值；不是执行器
└── docs/                       # 本 feature、S_01、designs 的进度同步
```

公共用法为 `create_persistent_agent(spec, context=context)` 或 `spec.build(context=context)`，均返回原生 DeepAgent。Session 和宿主端口需由调用方提供，不能用示例成功端口代替生产确认协议。窗口 / 执行 / 恢复目录没有空建，`execute_persistent_request` 没有提供占位实现。

| 验证 | 实际结果 |
| --- | --- |
| 首次新增构造测试 | 19 项按预期失败（接口和 owner 接缝尚不存在）→ 实现后 19 项通过 |
| 重入失败清理边界 | 测试先失败，证实重复构造错误地清理了正在构造的 inventory；加入本次 claimed 守卫后通过 |
| 构造及针对性回归 | 104 passed：27 项新增 + leaf spec / spec ownership / factory / cold build / workspace / AbilityManager 现有回归 |
| 新模块与新增测试 Ruff check / format check | 通过 |
| 共享 deep_agent_spec.py Ruff | HEAD 基线即存在 I001 / F401；本次只新增 owner 透传，不顺便整理原文件 |
| 全 Harness 测试 | 收集阶段中断：5 个现有测试依赖缺失的 opentelemetry.exporter，不能声称全量通过；未安装额外依赖 |
| git diff --check | 通过（仅 CRLF 转换提示） |

全 Harness 收集错误文件：observability/{test_rail,test_run_span,test_span_context,test_subagent}.py 和 rails/evolution/test_symphony_graph_evolution_rail.py。

独立审查发现 1 个 Important：纯 stateful ToolCard 引用绕过 owner 资格化，导致不同构造仍引用同一个全局有状态工具。已新增失败测试并修复：仅 Persistent 顶层 tools 拒绝这种引用，要求 fresh BuiltinToolSpec provider；stateless ToolCard 仍共享。对应拒绝测试 RED → GREEN，补充 stateless 保留测试后定向回归 104/104 通过；未改个人版 ToolCard 行为。审查没有其他确认的 Critical / Important / Minor。

审查未判断内容的裁定：

- Ruling: 可靠执行、动态授权围栏、事件确认、窗口、恢复和策略执行仍留后续阶段 — 本次仅交付构造；若直接投入可靠执行将缺少生产保证。
- Ruling: 端口返回 DTO 和后端可靠性仍待 PA-01 / PA-04 — 形状检查不能证明提交语义；不能以现有端口检查代替后端故障验收。
- Ruling: 自定义 provider 必须可信且构造期无外部业务副作用、返回独立有状态实例 — 本模块不隔离任意 Python 扩展；违规 provider 会破坏构造隔离。
- Ruling: 惰性子代理的运行隔离在执行接入阶段单独验证 — 当前未执行子代理；不能把父构造隔离等同于完整子代理隔离。
- Ruling: 不声称全 Harness 正确性已经证明 — 定向回归通过但全量收集被环境依赖阻挡；该验证缺口保留，未为本次改动安装额外依赖。

截至 2026-09-28 的阶段记录：当时 PA-01 仅身份 / 端口形状、PA-02 仅构造子范围已实现，PA-03 / PA-04 未关闭。该状态随后由协议补齐、实例隔离接缝和定向验证更新。

## 2026-09-29 独立验收更新

- PA-01 在“调用与宿主端口契约”范围完成：来源引用和宿主执行权引用进入执行上下文；事件 / 提交结果及新增窗口、资料版本协议区分 `unknown` 与有完整查询依据的 `empty / not_found`；缺少必需构造端口或端口接口错形状时可在 DeepAgent 装配前失败。实现见 `schema/execution.py`、`schema/protocol.py`、`schema/context.py`、`schema/events.py`、`schema/port_results.py`、`ports.py`。
- PA-02 构造验收完成：有效输入得到原生 `DeepAgent`；构造只做校验和装配，不调用模型或宿主端口；必需端口缺失及注入的 ContextStore 错形状在进入父类构造前拒绝。正式宿主 provider 和请求执行不属于本特性。
- 验证：`tests/unit_tests/harness/persistent_agent` 132 passed；共享 Harness 定向回归 582 passed；`tests/unit_tests/agent/react_agent` 136 passed；改动文件 Ruff check 与 format --check 均通过。现有 Pydantic 弃用警告不影响退出码。
- 尚待后续：运行时真实执行权 / 授权判定、事件提交及未知结果核对由 PA-04 和宿主适配实现；窗口与资料协议没有存储、搜索或装配后端，分别留 PA-05、PA-09～PA-12。PA-03 的实例隔离验收记录见 [F_03](F_03_invocation-binding-and-isolation.md)。

没有修改 JiuwenSwarm、恢复 stash、提交或推送。
