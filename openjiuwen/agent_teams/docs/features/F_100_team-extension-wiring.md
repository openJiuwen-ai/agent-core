# Team 扩展机制接入（契约层 + 成员侧接线）

- 日期：2026-10-24
- 范围：`agent_teams/extensions/`（新包）、`extensions/ops_build_handlers/`（平台目录）、`agent_teams/rails/`、`agent_teams/runtime/`、`agent_teams/tools/`、`agent_teams/spawn/`
- 需求来源：接入统一扩展框架（`core/events` 的 ExtensionEventBus + `core/extension` 的 Registry）的 Team 扩展机制——两个业务事件（`team.monitor_event` 监控事件通知、`team.build_policy` 协作规则构建）+ 只读 Team Ops 查询，并把各成员的构建与消费接到真实链路；工具扩展复用通用 `tool.call`（harness 侧尚不存在），本仓库无 Team 工具事件
- 事件契约文档：两个事件的插件侧契约手册（含使用方式与验证）见 core events 侧 [F_07 team.monitor_event](../../../core/events/docs/features/F_07_team-monitor-event.md) 与 [F_08 team.build_policy](../../../core/events/docs/features/F_08_team-build-policy.md)

## 背景 / Why

统一扩展框架（`ExtensionEventBus` + Registry）已落地，Team 需要定义两个业务事件的模型、匹配规则与
CORE 默认实现，提供只读 Team Ops 查询能力，并把各成员的 Policy 构建与监控事件消费接到真实链路上。
插件（USER/BUILTIN Handler）由此获得改写成员协作规则与订阅团队监控事件的能力。

## 已落地能力

### 契约层（`agent_teams/extensions/`，无 bus 时行为零变化）

- **模型与匹配**：`TeamMonitorEventInput/Output`、`TeamPolicyBuildInput/Output`、`TeamPolicyContext`
  （实际生效配置快照，含 base_prompt / swarmflow_enabled / expose_human_agents_to_teammates）；
  matcher 对齐框架 `MatchPatternFunc = (event_input, pattern) -> bool`，非法形态一律返回 False 不抛
  （bus 把 matcher 异常当注册错误）。
- **事件注册与校验器**：`register_team_event_types(bus)` 注册两个事件 + CORE；input/output validator
  落实事件的类型契约（监控事件允许 Output | None；Policy 构建必须返回 Output；空 Pattern 不能
  绕过 Input 类型校验）。handler 输出非法被框架隔离跳过、链落 CORE（遵循框架容错语义）。
- **默认 Policy 构建**：`build_default_policy_sections` 从 `TeamPolicyRail` 提取；leader 返回**全量**
  sections，配套双视图——`select_policy_prefix_sections`（bootstrap + extra，进系统提示词前缀）与
  `select_policy_disclosure_sections`（其余全部，经 `build_team` 披露）。插件追加的 section 落披露
  视图（前缀白名单保持最小）。`render_policy_sections` 与原 `build_leader_policy_disclosure` 同款渲染。
- **监控事件桥接**：`TeamMonitorEventBridge`——容量 1024 有界队列、非阻塞入队（coordination 回调
  不等待）、满丢弃计数、独立消费任务、stop 时 cancel+await+积压计丢弃；stop 自身被取消时经
  `Task.cancelling()` 区分并传播调用方取消。
- **Ops 与 resolver**：`team_ops_build_handler`（平台目录 `extensions/ops_build_handlers/team.py`）
  注册 `ops.build` BUILTIN（空 pattern——框架 ops.build matcher 只放行空 pattern），`ops.team` 九个
  只读方法（info/members/member/tasks/task/task_dependencies/messages/message/policy_context），含
  AND 筛选、timestamp+message_id 降序分页、服务端游标（关联 session/team/filters，不一致报错）、
  hide_dm 可见性、任务归属校验。数据源经 `TeamResourceResolver` 协议注入；`RuntimePoolResolver` +
  `BackendTeamReadView` 为生产实现（零重 import——pool 经结构化 Protocol 消费，避免 runtime→agent
  全链循环）。当前团队与 policy context 的调用范围绑定（contextvar，嵌套恢复/异常解除）。

### leader 侧接线

- **bus 注入**：`TeamRuntimeManager.set_extension_bus(bus)`（可选，None = 无扩展零开销）；冷路径
  统一经 `_inject_extension_bus` 把 bus 种进 `spec.build_context.extras`（`TeamHandleKey.EXTENSION_BUS`），
  derive 复制携带到 rail 与 tool 工厂；COLD_RECOVER 依赖 `runtime_spec.build_context` 回填机制覆盖。
- **桥接生命周期**：`ActiveTeam.monitor_event_bridge` 字段；`_apply_action` 冷路径挂载（listener 注册
  先于消费任务启动）、`stop_team` 与 `finalize` stop 分支拆除（pause 保留——pause 不是 stop）；
  `delete_team` force 路径走 `stop_team` 自动覆盖。
- **rail 惰性构建门**：`TeamPolicyRail(extension_bus=...)`——无 bus 构造期同步构建（与原行为逐字
  一致）；有 bus 构造期只存目标与快照，`before_model_call` 经 `build_team_policy_sections`（bind
  快照 + trigger + 校验）构建，lock + ready 双检幂等，失败上抛（成员准备失败，无静默回退）。
- **披露接链**：`BuildTeamTool(extension_bus=...)`——invoke（async）内经构建入口组装披露（context
  快照带本次建队落定的实际值，如 `hitt_enabled` 从结果读回），渲染文本存 `data["policy_disclosure"]`，
  同步的 `render_for_llm` 只展示；无 bus 走原现场渲染。压缩重注入不受影响（历史文本原样重注入）。
- **外部 CLI / 三方 harness 成员接链**：`external_cli_spawn._build_member_system_prompt`
  从 `spec.build_context` extras 读 bus（leader 构建时注入、随 spec 走，零新传递链）；有 bus 时 sections
  经 `build_team_policy_sections`（`workspace_prompt_variant="external"`）构建，插件改写随
  `build_team_member_system_prompt(sections=...)` 渲染进 CLI 成员的 system prompt；无 bus 保持原
  现场装配。外部成员的 TeamAgent shell 走 `setup_agent` 的 `member_runtime` 分支——**不构建
  DeepAgent、不挂任何 rail**，因此 `_build_member_system_prompt` 是外部成员 Policy 的唯一构建点，
  不存在与 shell rail 的重复触发。
- **inprocess teammate 接线**：`inprocess_spawn` 用 leader 的**同一个 spec 对象** configure 成员，
  manager 种进 `spec.build_context.extras` 的 bus 经 configurator 的 per-member `derive` 天然到达
  成员的 rail / tool 工厂——无需专属通道，本项工作为验证收口（全链合同测试：manager 注入 →
  `spec.build()` → rail 惰性构建；derive 保 bus 单测）+ 在 `inprocess_spawn` 落注释保护该隐式链
  （在该处重建 spec 会静默丢掉 bus）。宿主未给 spec 设 `build_context` 时保持无扩展（本地默认）。
- **宿主接线面**：`Runner.set_team_extension_bus(bus)` classmethod——宿主在 bus 初始化（事件注册 +
  ops.build）后一次调用即达全部冷构建路径；`GlobalRunnerPoolResolver`——Ops namespace 的惰性 resolver，
  resolve 时才读全局 Runner 的 manager 槽位（`ops.build` 冻结早于 manager 惰性创建，故不能持引用），
  manager 未创建时一切解析为不可访问（此时无活跃团队，语义正确），且不代创建（避免绕过 Runner 自身的
  org wiring）。

## 关键决策

1. **CORE 直读绑定而非经 `ops.team.policy_context()`**：两者读同一份 contextvar 快照；直读使 CORE
   不依赖 team Ops namespace 是否注册（设计字面是"CORE 和 Handler 通过 ops 读取"，语义等价）。
2. **`TeamPolicyContext` 十字段**（设计列七字段 + base_prompt / swarmflow_enabled /
   expose_human_agents_to_teammates）：三项同样是"实际生效配置"，CORE 构建必需，Ops 暴露无害。
3. **去掉 `(EventInput)` 继承**：框架的 `EventInput` 是 `object` 别名，继承等于继承 object（pylint
   R0205）；类型关系由注解表达。
4. **一次构建、两个视图**：CORE 对 leader 返回全量，rail 取前缀视图、`build_team` 取披露视图——
   F_76"同一套 sections、两个投递时机"的直接实现；插件 section 落披露（前缀白名单仅 bootstrap/extra）。
5. **同步 render 约束的解法**：`render_for_llm` 是同步契约、构建链是 async——构建挪进 invoke（设计
   "建队时等待 Policy 构建完成"正是该时点），render 只拼接。
6. **bus 放 `TeamRuntimeManager`**：与 `TeamInfra` setter 注入同构、桥接生命周期所有权对齐、多
   Runner 兼容；排除进程级全局 holder（违背框架"宿主持有"模型，多 bus 场景含糊）。

## 拒绝的方案

- Team 侧进程级 `set_event_bus`/`get_event_bus` 全局 holder——框架刻意不做全局 bus（"宿主必须串行
  Build"），全局 holder 会让多 Runner / 子进程边界含糊。
- `TeamAgentSpec` 加 bus 参数——Spec 不持运行时引用（模块铁律）；bus 经 `BuildContext.extras`
  （live handle 通道，messager/backend 同款）注入。
- matcher 内抛异常表达非法 Pattern——框架把 matcher 异常当注册错误（EVENTS_MATCH_INVALID），
  Team matcher 全部防御式返回 False。
- 桥接 stop 用 `except CancelledError: pass`——无法区分"自己请求的 consumer 取消"与"stop 自身被
  取消"，后者被吞会丢失调用方取消信号；用 `Task.cancelling()` 区分。

## 验证基线

- `tests/unit_tests/agent_teams/extensions/`（契约 + wiring）+ `tests/unit_tests/extensions/ops_build_handlers/`：
  含真 `ExtensionEventBus` 端到端（注册 → USER handler → trigger → 链/短路/`to(CORE)`/校验器/容错）、
  桥接全生命周期（含取消传播）、内存 DB + 真 pool 的 resolver、leader rail 惰性构建与前缀视图、
  `build_team` 披露经链（插件 section 到达 leader）。
- 全量 `agent_teams` 回归：失败分布与 stash 基线一致（存量：coordination/observability 环境缺依赖
  等），零新增；`test_team_policy_rail.py` 74 个存量用例不动即绿（无 bus 分支逐字一致）。
- ruff / pylint 干净（存量告警除外）。

## 已知遗留

- **子进程 teammate**：依赖统一框架的"进程分发"（spawn payload 携带扩展加载指令 + 子进程自 init
  Registry），agent-core 单侧不可闭环；当前 fast path 保证其行为正确（本地默认）。
- **通用 `tool.call` 接入**：事件在 harness 侧尚不存在（dev guide 已写目标位置示例），Team 侧无从接入。
- **错误码分类打磨**：task/message 查无、归属不符、team 不可访问等"查无此物"场景现统一报
  `AGENT_TEAM_STATE_INVALID`（132006），语义不贴但 reason 明确、测试不锁码；AGENT_TEAM 段缺
  NOT_FOUND 形态码（其他段有 `AGENT_TOOL_NOT_FOUND` 先例），待观测分类有真实需求时新增
  `AGENT_TEAM_RESOURCE_NOT_FOUND` 并分类收拾。
- 平台侧设计仓的实现记录回填随该仓自行同步（本仓库不引用其文档编号）。
