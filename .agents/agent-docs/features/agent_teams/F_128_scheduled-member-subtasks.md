---
title: 调度模式：成员拆分子任务（父任务挂起 / 恢复）
author: alan_cheng
author_note: 按 Git 最早可追溯的文档新增提交署名；不代表代码 owner
id: F_128_scheduled-member-subtasks
type: F
modules:
- openjiuwen/agent_teams
files: []
designs: null
design_note: 历史设计关联待核实，不等同于没有关联设计
author_email: chengshuo5@huawei.com
---

# 调度模式：成员拆分子任务（父任务挂起 / 恢复）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-23 |
| 状态 | 已实现 |
| 适用范围 | **仅 `dispatch_mode == "scheduled"` 的非 leader、非人类成员**，且团队开启 `max_subtask_depth > 0`。默认关闭，关闭时行为与改动前逐字一致 |
| 范围 | `schema/blueprint.py` / `schema/team.py`（`max_subtask_depth`）、`schema/task.py`（`TaskGraphSpec` / `NewTaskSpec` / `TaskSummary` / `TaskDetail` 的父子字段）、`tools/models.py`（任务表 `parent_task_id` / `created_by` / `resume_pending` 三列）、`tools/database/engine.py`（加列迁移）、`tools/database/task_dao.py`（`mutate_dependency_graph(suspend_parent=)`、`get_subtask_statuses` / `get_parent_task_id` 投影、`resume_pending` 的置位与清除）、`tools/task_manager.py`（`add_graph(suspend_parent=)`、恢复不走计划闸、挂起父任务拒绝改派、取消级联、`task_depth`）、`tools/team.py`（`subtask_depth_limit` / `member_subtasks_enabled`）、`tools/tool_permissions.py`、`tools/tool_factory.py`（`_CREATE_TASK_CLASS` 加 role 维度）、`tools/tool_task.py`（`MemberSubtaskCreateTool`、`view_task` 显示父子关系）、`tools/locales/{cn,en}.py` 与 `descs/*/task/create_task_subtask.md`、`agent/scheduling/{scheduler,render}.py`、`prompts/*/scheduler_task_resume.md`、`prompts/*/dispatch_scheduled_{leader,teammate}.md` 及 `*_subtasks.md` 片段、`prompts/sections.py`、`rails/team_policy_rail.py`、`tools/tool_team.py`、`spawn/external_cli_spawn.py`、`external/{descriptor,client}.py`、`external/cli_agent/spawn.py`；文档：`docs/specs/{S_08,S_12,S_22}`、`prompts/AGENTS.md`、`tools/AGENTS.md`、`agent/scheduling/AGENTS.md` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/ --ignore=cli --ignore=observability` → 3901 passed / 36 skipped / 2 xfailed；`cli/` + `observability/` + `test_database.py` → 234 passed。其中新增 `tools/test_member_subtasks.py` 35 例、`test_database.py` 加列迁移 1 例 |
| Refs | #1435 |

## 背景

调度模式（F_62）下所有任务都由 leader 用 `create_task` 建立并指派，成员只能执行和完成自己名下的任务。
成员在执行中发现任务可以再拆分（比如一个"实现模块"任务里有几块可以并行的独立工作）时，只能写进完成说明，
或者发消息请 leader 拆分，多绕一轮，leader 也要承担本不必由它做的细粒度拆分。

直接把 `create_task` 开放给成员有几处会出错（评审讨论记录）：

1. **一成员一活跃任务（S_08 不变量 16）导致自锁**：成员在做 T1 时给自己建 T2，T2 要等 T1 结束才能开工，
   而 T1 在等 T2 的结果。
2. **跨成员的等待环**：A 做 T1 时给 X 建 T2 并等待；X 做 T2（或另一个 leader 任务）时给 A 建 T3 并等待——
   双方都占着名额等对方，谁也开不了工。这种环跨任务树，按"不能指派给祖先"之类的规则挡不住。
3. **收尾判定提前**：调度器的 `_all_done_announced` 只发一次（S_22 数据结构），任务板清空后再出现的任务
   不会再触发收尾。
4. **leader 收到不认识的完成摘要**：终态摘要只发 leader（S_22 不变量 6），成员建的任务也会塞给 leader。
5. **完成结果回不到建任务的人**：完成交接只通知 leader，等结果的成员无从得知。
6. **任务无限扩散**：成员互相建任务没有深度上限。

## 数据结构 / 状态机

任务表新增两列（按 session 的动态任务表，迁移照 `engine.py` 已有的 `reviewer` / `review_round` 加列写法）：

| 列 | 类型 | 语义 |
|---|---|---|
| `parent_task_id` | `TEXT NULL` | 父任务 id。leader 建的任务为空（顶层任务）；成员建的子任务为它建任务时**自己正在执行的那个任务**，由工具自动填写，**不是 LLM 参数** |
| `created_by` | `TEXT NULL` | 建任务的成员名。只用于审计与展示，**不参与任何权限或路由判断**（理由见「拒绝的方案」）。老数据为空，按 leader 处理 |

深度：`depth(t) = 0`（`parent_task_id` 为空），否则 `depth(parent) + 1`。

状态机**不新增状态**，只启用一条已存在但目前没有调用方的边 `IN_PROGRESS → BLOCKED`（`schema/status.py`
的 `TASK_TRANSITIONS` 已允许）：

```text
 父任务 T1                                         子任务 T2 / T3
 ───────────                                       ─────────────
 IN_PROGRESS ── 成员 create_task(子任务) ──▶ BLOCKED     PENDING(assignee) ── 调度器 start_task ──▶ IN_PROGRESS ──▶ COMPLETED / CANCELLED
                (同一事务：建子任务 + T1 depends_on 子任务        │
                 + T1 挂起)                                        │ 终态解依赖（COMPLETED 与 CANCELLED 都会解）
                                                                  ▼
 BLOCKED ── 依赖刷新（全部子任务终态）──▶ PENDING ── 调度器 start_task（恢复交接）──▶ IN_PROGRESS ──▶ …
```

挂起的父任务是 `BLOCKED`，不在活跃集合 `{PLANNING, IN_PROGRESS, IN_REVIEW}` 里，所以**不占成员的活跃任务名额**。

## 决策

### D1：父任务"挂起让出名额"，而不是"占着名额等子任务"

成员调用 `create_task` 建子任务时，在**同一次** `mutate_dependency_graph` 事务里完成三件事：

1. 子任务落库：`parent_task_id = 调用者当前 IN_PROGRESS 任务`，`created_by = 调用者`，按调度形态 seed
   `PENDING(assignee)`（有批内依赖则刷新成 `BLOCKED`）；
2. 加边：父任务 `depends_on` 每一个新子任务；
3. 父任务 CAS `IN_PROGRESS → BLOCKED`（`WHERE status='in_progress' AND assignee=调用者`），CAS 未命中则整批回滚、
   返回真实原因（父任务刚被取消 / 改派 / 送审等）。挂起在加边校验**之前**执行：执行中的任务不能新增依赖
   （`TASK_DEPENDENCY_REJECT_STATUSES`），挂起后的 `BLOCKED` 任务可以。

之后全部复用现有机制：子任务终态时 `_terminate_task_in_session` 解开依赖边（COMPLETED 与 CANCELLED 都会解）→
依赖刷新把父任务 `BLOCKED → PENDING` → 调度器 `_reconcile_starts` 重新 `start_task` → 交接消息带上游摘要
（`{{task.upstream_brief}}` 已存在，F_118 / F_119）。

由此：

- **自锁消失**：父任务挂起后名额空出，子任务可以指派给自己。
- **跨成员等待环消失**：等待中的任务不占名额，不存在"双方都占名额等对方"；父子依赖由 DAO 的环检测
  （`_check_cycle_and_compute_new_edges`）兜底，也不可能成环。
- **收尾判定天然正确**：成员只能在持有 `IN_PROGRESS` 父任务时建子任务，父任务未终态则任务板不可能已清空，
  所以 `_all_done_announced` 的"只发一次"不会被提前触发。**不改收尾逻辑。**
  （追补：B_09 把"只发一次"改为"每次看板清空只发一次"——leader 在收尾之后自己建的任务会让扫描复位标志；
  成员子任务仍按上面的理由不会触发复位，本节结论不变。）

### D2：工具形态——成员用独立的 `MemberSubtaskCreateTool`

- `create_task` 加入 `MEMBER_ONLY_TOOLS_SCHEDULED`；非 leader 角色在 `member_subtasks_enabled()` 不为真时在工厂里
  减掉（与 fork / plan_mode 的减法同处），工具不存在而不是存在但报错。
- `_CREATE_TASK_CLASS` 从按 `dispatch_mode` 一维查表改成按 `(dispatch_mode, "leader" | "member")` 查表，新增一格
  `("scheduled", "member") → MemberSubtaskCreateTool`。`("autonomous", "member")` 一格指向 `TaskCreateTool`，只为让每个
  角色都能完成装配——`all_tools` 对所有角色都构造一遍再按权限集过滤，autonomous 成员的权限集里没有 `create_task`。
  与 `send_message` 的二维表同一手法（S_08 不变量 18）。
- `MemberSubtaskCreateTool` 与另两个形态**各自独立**，只共享模块级纯函数（基础 schema 字段、批次校验、
  assignee 校验）。schema：`tasks[]` 每项 `title` / `content` / `task_id?` / `depends_on?` / `assignee`（必填）。
  **不提供** `member`（内联成员，见「拒绝的方案」）、`reviewer`、`max_review_rounds`、`depended_by`，也**没有
  `parent_task_id` 参数**——父任务由工具从调用者的当前任务推出，模型无从写错。偷传上述字段在任何写库之前报错拒绝
  （MCP 客户端不过 schema，同 B_04 的处置）。
- 描述独立一份 `create_task_subtask.md`（cn / en）：讲清"调用后你的当前任务会挂起，本轮应立即结束，子任务全部
  结束后调度器会把结果交还给你继续"。

### D3：校验规则（工具边界，按顺序）

1. 调用者必须恰好有一个 `IN_PROGRESS` 任务（`PLANNING` / `IN_REVIEW` 时不能拆）；否则拒绝并说明原因。
2. `depth(当前任务) + 1 <= max_subtask_depth`；否则拒绝。
3. `assignee` 必填，必须是已存在、非 leader 的成员，**可以是自己**；**不能是在队人类成员**——人类持有的任务
   leader 不可取消（HITT 锁，S_07 运行约束 3），会和 D5 的取消级联冲突。
4. 批内校验沿用 `_validate_task_batch`（标题 / 内容必填、批内 id 不重复）；`depends_on` 允许指向批内任务或
   看板已有任务（只是让子任务多等一个上游，不改变已有任务的依赖）。

### D4：调度器——恢复交接、恢复优先、摘要只给顶层任务

- **"是否恢复"看父任务行上的 `resume_pending` 标记**：挂起时置真，下一次开工消费，改派 / 重置清掉（初版按"有没有子任务"
  推断，审查后改掉，见文末「审查后修复」）。
- **恢复交接用独立模板** `scheduler_task_resume`（cn / en）：开工扫描在 `start_task` 之前读出候选行的 `resume_pending`，
  传给 `_send_task_start(task, resume=...)` → `render.meta_task_start(task, resume=True)`；交接补发扫描一律发开工模板。正文说明"你拆出的子任务已全部结束（含被取消的），继续完成本任务"，
  复用 `{{task.upstream_brief}}` 列出子任务完成摘要（子任务就是父任务的直接上游）。`is_task_start_meta` 的模板集合
  同步加入它，B_06 的交接补发扫描照常工作。
- **恢复不重走计划闸**：`TeamTaskManager.start_task` 对 `resume_pending` 为真的任务一律落 `IN_PROGRESS`，计划已在首次开工时批过。
  判断放在 manager 而不是调度器传参：开工落点是 manager 的规则，调度器之外的开工路径也应一致。
- **恢复优先**：父任务挂起期间名额空出，调度器可能给该成员开工别的任务，父任务恢复会被推迟到那个任务结束。
  `_reconcile_starts` 选候选时 `resume_pending` 为真的 `PENDING` 任务排在普通任务前（再按 `updated_at` / `task_id`），
  标记就在候选行上，不额外查询。这只缩短延迟，不影响正确性。
- **终态摘要只给顶层任务**：`_digest_task_done` 对 `parent_task_id` 非空的任务不给 leader 发摘要；子任务的结果经
  恢复交接回到父任务执行者。收尾摘要（`render_leader_all_done`）照常统计全部任务。
- **挂起本身不发新事件**：子任务的 `TASK_CREATED` 已经足以唤醒调度器扫描；不复用 `TASK_UPDATED`——它会触发成员
  的 `on_task_updated`，让刚挂起的成员"重看任务后继续"，正好与挂起语义相反。

### D5：leader 对父子任务的操作

- **取消级联**：`TeamTaskManager.cancel` 取消一个任务后，依次取消它所有未终态的子任务（递归，深度受
  `max_subtask_depth` 约束），每个被取消的任务照常发 `TASK_CANCELLED`；整个级联结束后才检查一次任务板是否清空。**先父后子**是正确性要求：先取消子任务会解开
  父任务最后一条依赖边，调度器可能抢先把一个马上要被取消的父任务恢复开工。级联不在同一事务里——每一步都是已有的
  单任务取消，中途失败留下的只是"父已取消、部分子未取消"，子任务仍可被 leader 单独取消，不会出现卡死。
- **挂起中的父任务拒绝改派**：父任务 `BLOCKED` 且有未终态子任务时，`TeamTaskManager.reassign` 拒绝（工具与其他
  调用方都经过它），提示"等子任务结束或先取消它们"。`TASK_REASSIGNABLE_STATUSES` 把 `BLOCKED` 当作"有主但没开工"（F_82）——挂起的父任务
  已经做了一半，产物在原执行者手里，换人与 F_82 排除两个闸的理由相同。普通（从未开工的）`BLOCKED` 任务不受影响。
- **编辑照常允许**：挂起中的父任务可以改标题 / 内容，恢复交接投递时按当时的任务行渲染（F_63），新内容自然生效。
- 成员**不能**改 / 取消自己建的子任务（成员仍没有 `update_task`），见「已知遗留」。

### D6：开关与提示词

- 静态 spec 开关 `TeamAgentSpec.max_subtask_depth: int = Field(default=0, ge=0)`，一个字段同时是门控（0 = 关闭）与深度上限；不进
  `build_team` 覆盖（成员工具在 spawn 时装配，理由同 F_126 决策 1）。`TeamSpec` 镜像该字段随 spawn 载荷到达成员
  进程；外部 CLI 成员经 `TeamJoinDescriptor` 透传。门控信号只有一个：`TeamBackend.member_subtasks_enabled()`
  （`max_subtask_depth > 0`），工具工厂、`TeamPolicyRail`、`build_team` 准则披露三处都读它（同 F_126 决策 2）；深度
  上限另由 `subtask_depth_limit()` 交给工具校验。外部 CLI 成员的提示词由 `external_cli_spawn` 直接读 spec 字段。
- 两份 `dispatch_scheduled_*` 模板加 capability 槽 `{{member_subtasks}}`，填 `dispatch_scheduled_{leader,teammate}_subtasks.md`，
  关闭时为空串。**槽位挂在一个列表项的行尾，填充值以换行开头**：开启时规则作为一个新列表项接在后面，关闭时不留空行。
  `{{member_messaging}}` 那样独占一行的槽在 leader 版里位于正文中间，关闭时会留下一个空行洞。leader 片段：成员可能把
  你派的任务拆成子任务，父任务显示为 `blocked` 是在等子任务；子任务的完成不会单独通知你；取消父任务会一并取消
  未完成的子任务；等待中的父任务不能改派。
- `view_task`：`list` 每行带父任务 id（有的话），`get` 带父任务与子任务列表。

## 拒绝的方案

- **只加 `created_by`，靠它解决权限和路由**：能把完成摘要发回建任务的人、给"改 / 取消自己建的任务"做权限，但解决
  不了自锁与等待环（根源是活跃名额），也解决不了收尾判定提前。而且 leader 改派父任务后，按创建者路由会把结果发给
  已不负责的人；按父任务当前执行者路由才对。所以 `created_by` 只留作审计。
- **禁止成员把子任务指派给自己**：只挡住长度为 1 的环，挡不住跨成员的环（背景 2）。
- **"不能指派给任何祖先任务的执行者" + 深度上限**：只覆盖同一棵任务树内的环；成员同时持有 leader 派的另一个任务时
  形成的跨树环挡不住。
- **深度固定为 1（成员建的任务不能再拆）**：能保证不成环，但这是靠"不让用"换来的；挂起方案下环已不可能出现，深度
  只用来控制扩散，没必要锁死在 1。
- **新增 `WAITING` 状态表示等子任务**：语义更直白，但要改所有状态子集（活跃 / 可改派 / 可终止前驱 / 开放任务计数）、
  依赖刷新、恢复扫描与看板渲染；`BLOCKED` 本来就是"等依赖"，父任务等子任务正是一种依赖，复用现有边与刷新逻辑改动小
  得多。代价是 F_82 的"BLOCKED = 没开工"假设被打破，由 D5 的改派规则补上。
- **子任务不占名额、父任务保持 `IN_PROGRESS`**：等于让一个成员同时有多个活跃任务，破坏 S_08 不变量 16，而那条不变量
  是改派、取消、停滞检测共同依赖的前提。
- **让模型显式传 `parent_task_id`**：给模型一个能写错的参数而没有任何收益——父任务只能是调用者正在执行的那个任务。
- **子任务支持内联成员（成员建子任务时顺带创建新成员）**：评审中设计过一版后决定不做。成员进程没有 leader 才有的
  三样东西——模型分配器（只在 leader 上构造）、专家目录（只注入 leader）、外部 CLI 路由（登记方进程的内存映射
  `TeamBackend._external_cli_specs`，leader 拉起时读的是自己的映射）——要支持就得引入"成员登记、leader 拉起时补做
  模型分配"的延迟分配、新成员继承创建者权限等额外机制，并改动拉起路径。名册扩充仍只归 leader；成员需要新的承担者
  时请 leader 建人，或把子任务指派给已有成员（包括自己）。
- **成员建任务时由工具直接拉起子任务的执行者**：拉起是 leader-only 的漏斗（S_05 不变量 1），而调度模式的交接本来就
  归调度器（S_08 不变量 23）。子任务的 `TASK_CREATED` 到达 leader 后由调度器开工并懒启动执行者，成员侧零拉起代码。

## 验证

- 新增 `tests/unit_tests/agent_teams/tools/test_member_subtasks.py`（35 例，真实内存 SQLite + 真实 `TeamScheduler`）：
  - 形态选择：开关关闭时成员无 `create_task`；开启时成员拿到 `MemberSubtaskCreateTool`，schema 不含
    `member` / `reviewer` / `max_review_rounds` / `depended_by` / `parent_task_id`；leader / autonomous 成员不变；
    cn / en 描述无残留槽。
  - 挂起同事务：建子任务后父任务为 `BLOCKED`、依赖边存在、子任务 `parent_task_id` / `created_by` 正确；子任务反向依赖
    父任务（成环）时整批回滚、父任务仍 `IN_PROGRESS`。
  - 校验：无执行中任务拒绝、超深度拒绝、leader / 人类 / 不存在 / 空 assignee 拒绝、偷传
    `member` / `reviewer` / `max_review_rounds` / `depended_by` / `parent_task_id` 拒绝、指派给自己成功。
  - 恢复：子任务全部完成 → 父任务恢复 `IN_PROGRESS` 并投递 `scheduler_task_resume`；被取消的子任务同样解开父任务；
    plan_mode 父任务恢复直落 `IN_PROGRESS`；恢复的父任务先于排队的普通任务开工。
  - 不卡死：dev-1、dev-2 各自给对方建子任务，全部任务最终 `COMPLETED`。
  - leader 侧：子任务完成不产生 leader 摘要，父任务完成照常产生；取消父任务级联取消未完成子任务、已完成的不动；
    挂起中父任务拒绝改派、普通 `BLOCKED` 任务仍可改派；`view_task` 的 list / get 显示父子关系。
  - 提示词：两角色 × 两语言的 dispatch 段只在开启时带子任务规则，规则作为列表项紧接上一行、关闭时无空行洞；leader
    准则披露随开关变化。
  - spec 默认 0、负数拒绝；descriptor 经 JSON 往返保留深度。
- `test_database.py` 新增加列迁移用例：旧任务表补出两列、重复执行无副作用、老数据两列为空。
- `test_team_policy_rail.py` 的 `_FakeTeamBackend` 补了 `member_subtasks_enabled()`（rail 在 init 时探测它）。
- 既有 `test_tool_variants.py` / 调度器 / 改派 / 取消测试全部通过，开关关闭时行为逐字不变。

## 已知遗留

- 成员不能改 / 取消自己建的子任务（只能请 leader 处理）。后续可给成员一个只作用于"自己当前任务的子任务"的
  `update_task` 形态，权限按父任务当前执行者判断。
- 单次调用可建子任务的数量暂不设上限，只靠深度控制扩散；评审时确认是否需要。
- swarm 侧开关（实验特性「调度模式」下的子项）需在 swarm 仓库单独落地，依赖本改动合入后 bump core `rev`。

## 审查后修复（2026-10-25）

代码审查（high）提出的 9 条里，7 条以一个 `fix(swarm)` 提交修掉；"编辑挂起中的父任务会唤醒执行者"改成按需逐层调整子任务，
另起一份特性文档；测试代码的规范性不再检查，那一条不改。

1. **恢复判定改为显式标记 `resume_pending`**。初版用"这个任务有没有子任务"判断是否恢复，但子任务关系是永久的：父任务恢复后
   被改派，交接补发扫描会给新执行者发"你拆出的子任务都结束了，继续吧"；重置后再指派给计划审批模式的成员，会绕过计划闸。
   恢复只属于挂起它的那一次，所以改成父任务行上的布尔列：挂起 CAS 置真、开工 CAS 消费、`reassign_task` / `reset_task` 清掉。
   `has_subtasks`、`parent_task_ids` / `get_parent_task_ids` 随之删除（`parent_task_id` 列保留，父子关系仍靠它）。
   - 拒绝"按 `created_by` 是否等于当前执行者判断"：重置后再指派回原执行者时仍会误判为恢复。
2. **级联取消只发一次 `TASK_LIST_DRAINED`**：单任务取消（含递归进子任务）抽成 `_cancel_with_subtasks`，不再检查任务板；
   公开的 `cancel()` 在整个级联结束后检查一次。
3. **禁传字段按"有没有给值"判断**：`spec.get(name) is not None`，只有显式 `null` 视为没传，`member: {}`、`reviewer: {}`、
   `max_review_rounds: False` 都会被拒绝。
4. **`task_depth` 沿父链只读 `parent_task_id` 一列**（`TaskDao.get_parent_task_id`），不再读整行、不再还原溢出到文件的正文。
5. **调度器不再为每次交接查询子任务**：随第 1 条，恢复标记就在候选行上。
6. **承担者校验整批三次读取**：leader 名、名册投影（`list_member_roster`，补上调用者自己）、`live_human_agent_names`，
   替代每个子任务一次 `member_exists` 加一次 `is_live_human_agent`。错误文案不变。
7. **深度校验改为字段约束** `Field(default=0, ge=0)`，从验收参数的校验函数里移走。

验证：`test_member_subtasks.py` 补了恢复后改派得到普通开工交接、待恢复时改派给计划审批成员仍走计划闸、重置后重新开始、
级联取消只发一次清空事件，以及 `{}` / `False` 两种禁传值；`test_database.py` 的迁移用例补上 `resume_pending`。
`pytest tests/unit_tests/agent_teams/` → 4022 passed / 36 skipped / 2 xfailed。

