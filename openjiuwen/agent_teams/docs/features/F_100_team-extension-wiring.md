# Team 扩展机制接入（契约层 + 成员侧接线）

- 日期：2026-10-24
- 范围：`agent_teams/extensions/`（新包）、`extensions/ops_build_handlers/`（平台目录）、`agent_teams/rails/`、`agent_teams/runtime/`、`agent_teams/tools/`、`agent_teams/spawn/`
- 需求来源：接入统一扩展框架（`core/events` 的 ExtensionEventBus + `core/extension` 的 Registry）的 Team 扩展机制——两个业务事件（`team.monitor_event` 监控事件通知、`team.build_policy` 协作规则构建）+ 只读 Team Ops 查询，并把各成员的构建与消费接到真实链路；工具扩展复用通用 `tool.call`（harness 侧尚不存在），本仓库无 Team 工具事件

## 背景 / Why

统一扩展框架（`ExtensionEventBus` + Registry）已落地，Team 需要定义两个业务事件的模型、匹配规则与
CORE 默认实现，提供只读 Team Ops 查询能力，并把各成员的 Policy 构建与监控事件消费接到真实链路上。
插件（USER/BUILTIN Handler）由此获得改写成员协作规则与订阅团队监控事件的能力。

## 已落地能力（基础设施部分）

本文件按三个 PR 组织归档：本 PR（基础设施）交付 bus 注入通道、Ops 查询底座与 resolver；
`team.monitor_event` 与 `team.build_policy` 两个事件 PR 在此基础上各自增补章节。

- **bus 注入通道**：`TeamRuntimeManager.set_extension_bus(bus)`（可选，None = 无扩展零开销）；冷路径
  统一经 `_inject_extension_bus` 把 bus 种进 `spec.build_context.extras`（`TeamHandleKey.EXTENSION_BUS`），
  derive 复制携带到工厂；COLD_RECOVER 依赖 `runtime_spec.build_context` 回填机制覆盖。
  `Runner.set_team_extension_bus` classmethod 是宿主在 bus 初始化后的唯一公开注入调用。
- **Ops 查询底座**：`team_ops_build_handler`（平台目录 `extensions/ops_build_handlers/team.py`）注册
  `ops.build` BUILTIN（空 pattern——框架 ops.build matcher 只放行空 pattern），`ops.team` 八个只读方法
  （info/members/member/tasks/task/task_dependencies/messages/message），含 AND 筛选、
  timestamp+message_id 降序分页、服务端游标（关联 session/team/filters，不一致报错）、hide_dm 可见性、
  任务归属校验。
- **resolver**：数据源经 `TeamResourceResolver` 协议注入；`RuntimePoolResolver` +
  `BackendTeamReadView` 为生产实现（零重 import——pool 经结构化 Protocol 消费，避免 runtime→agent
  全链循环）。`GlobalRunnerPoolResolver`——Ops namespace 的惰性 resolver，resolve 时才读全局 Runner 的
  manager 槽位（`ops.build` 冻结早于 manager 惰性创建，故不能持引用），manager 未创建时一切解析为
  不可访问（此时无活跃团队，语义正确），且不代创建（避免绕过 Runner 自身的 org wiring）。
- **调用范围绑定**：`bind_team_scope` / `current_team_name`（contextvar，嵌套恢复/异常解除）——事件
  入口为一次调用绑定当前团队，Ops 查询可省略 `team_name`；省略≠猜测（无绑定时明确报错）。

## 关键决策

1. **bus 放 `TeamRuntimeManager`**：与 `TeamInfra` setter 注入同构、桥接生命周期所有权对齐、多
   Runner 兼容；排除进程级全局 holder（违背框架"宿主持有"模型，多 bus 场景含糊）。
2. **`TeamAgentSpec` 不加 bus 参数**——Spec 不持运行时引用（模块铁律）；bus 经 `BuildContext.extras`
   （live handle 通道，messager/backend 同款）注入。
3. **resolver 惰性读 manager 槽位、不代创建**：`ops.build` 在进程启动时冻结 handler，而 manager 是
   Runner 惰性创建的；直接创建会绕过 `_get_team_runtime_manager()` 的 org wiring。
4. **Ops 错误响亮、不猜测**：无 session、无当前团队绑定、团队不可访问均明确报错；即使会话内只有
   一个团队，省略 `team_name` 也不自动选中——运行时偶然性不得决定查询指向。

## 拒绝的方案

- Team 侧进程级 `set_event_bus`/`get_event_bus` 全局 holder——框架刻意不做全局 bus（"宿主必须串行
  Build"），全局 holder 会让多 Runner / 子进程边界含糊。
- matcher 内抛异常表达非法 Pattern——框架把 matcher 异常当注册错误（EVENTS_MATCH_INVALID），
  Team matcher 全部防御式返回 False。
- resolver 在注册时持有 manager 引用——时序上 manager 尚不存在；代创建则绕过 Runner 的 org wiring。

## 验证基线

- `tests/unit_tests/agent_teams/extensions/` + `tests/unit_tests/extensions/ops_build_handlers/`：
  Ops 查询全行为（fake resolver）、内存 DB + 真 pool 的 resolver、调用范围绑定、Runner façade 注入、
  惰性全局 resolver。
- 全量 `agent_teams` 回归：失败分布与基线一致（存量环境问题），零新增。

## 已知遗留

- `team.monitor_event` / `team.build_policy` 两个事件契约与接线条线由后续 PR 交付（各自增补本文件
  章节）。
- **子进程 teammate**：依赖统一框架的"进程分发"（spawn payload 携带扩展加载指令 + 子进程自 init
  Registry），agent-core 单侧不可闭环；fast path 保证其行为正确（本地默认）。
- **通用 `tool.call` 接入**：事件在 harness 侧尚不存在，Team 侧无从接入。
- **错误码分类打磨**：task/message 查无、归属不符、team 不可访问等"查无此物"场景现统一报
  `AGENT_TEAM_STATE_INVALID`（132006），语义不贴但 reason 明确、测试不锁码；待观测分类有真实需求
  时新增 `AGENT_TEAM_RESOURCE_NOT_FOUND` 并分类收拾。
- 平台侧设计仓的实现记录回填随该仓自行同步（本仓库不引用其文档编号）。
