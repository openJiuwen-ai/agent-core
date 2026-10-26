# 调度模式：任务内联成员与成员退场策略（短生命周期成员）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-13 |
| 状态 | 已实现（v2 方案 + D7） |
| 适用范围 | **仅 `dispatch_mode == "scheduled"`**。autonomous 模式无此特性，其工具、提示词、handler 零改动 |
| 范围（预计） | 调度专属：`tools/tool_task.py`（`ScheduledTaskCreateTool`）、`agent/scheduling/scheduler.py`、`tools/locales/descs/*/task/create_task_scheduled.md`、`prompts/*/dispatch_scheduled_leader.md`；状态机事实（模式无关、autonomous 下不可达）：`schema/status.py`、`tools/member_options.py`、`tools/team.py`（`recover_member` / `shutdown_member`）、`tools/database/{member_dao,task_dao,message_dao}.py`、`agent/team_agent.py`（`shutdown_self`）、`agent/recovery_manager.py`、`runtime/manager.py`（`finalize_member`）；D7 交接链：`tools/models.py`（任务表 `note` 列）、`tools/database/engine.py`（`note` 列迁移）、`tools/task_manager.py`（`complete` / `_submit_for_review` 写 note）、`schema/task.py`（`TaskDetail.note`）、`message_template.py`（`upstream_brief` / `downstream_brief`）、`prompts/*/scheduler_task_start*.md`、`prompts/*/dispatch_scheduled_teammate.md`、`tools/locales/descs/*/task/view_task_scheduled.md`、`tools/tool_factory.py`（`_VIEW_TASK_DESC_KEY`）；文档：`docs/specs/{S_03,S_05,S_06,S_08,S_12,S_22}` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/ --ignore=cli --ignore=observability` → 3109 passed / 36 skipped / 3 xfailed；另 142 failed 全部为环境缺包（`opentelemetry.exporter` 134 例、`claude-agent-sdk` / `openai-codex` 8 例），与本特性无关。本特性新增 / 改动用例 41 个全部通过（见「验证」） |
| Refs | #1435 |

## 背景

调度模式下 leader 派活要三步：`spawn_teammate` 建人 → `create_task(assignee=...)` 派活 →
事后 `shutdown_member` 收人。成员被定义为"跟随团队长期存在的实体"，但"一个任务一个专家、
干完即走"的场景里这是多余的仪式——leader 要先想名册、再想任务、最后还得记得关人。

同一模式下 reviewer 已经走了另一条路：在 `create_task` 里内联 `{type, reviewer_id, instruction}`
声明，不预先 spawn，调度器需要时拉一次性 harness `run_once` 后 dispose（F_73）。用户诉求是把
"规划任务时顺手把人带出来"推广到 assignee，并给成员加退场策略，两档：

- **自动 shutdown**：等价于 leader 手动 `shutdown_member` 的终态。
- **自动休眠**：停止协调循环、不处理任何事件，但**仍在名册上**；只有被点名
  （`send_message(to=<name>)` 等显式指名）才唤醒。

### v1 → v2 的修订

v1 把退场判定挂在成员自己的 `StreamController._on_idle_settled`，并给 autonomous 的
`create_task` 也加了内联成员与唤醒。评审意见：特性只属于调度模式，v1 把两种模式共用的
idle 边、`TaskCreateTool`、提示词模板都牵进来了，耦合过重。v2 的边界：

- **决策归调度器**。`TeamScheduler` 只在 scheduled leader 上构造（S_22 不变量 1），退场判定
  作为它的第三个幂等扫描，与开工 / 验票并列。autonomous 的 kernel 里 `scheduler is None`，
  物理不可达。
- **执行复用 `shutdown_member` 的 graceful 链**。调度器不碰成员 round，只写请求状态；成员
  按既有机制 18 在真正空闲时自行落终态——终态是 `SHUTDOWN` 还是 `DORMANT` 由成员行上的
  策略决定。这条链两种模式本来就共用，v2 只是让它的终点多一个取值。
- **模式无关的改动只剩"状态机事实"**：一个新 `MemberStatus`、一个 options 字段、几个集合归位、
  `recover_member` 的 CAS 接受集。它们没有任何 `dispatch_mode` 分支；autonomous 下没人会写
  `lifetime`、也就没人会进 `DORMANT`，这些代码在那里是死路，不是耦合。

## 现状勘查（设计输入）

1. **reviewer 的"无名册临时 harness"不能搬给 assignee。** assignee 在四处要求真实
   `team_member` 行：`_validate_assignees`（`tool_task.py:125`）、`add_graph → _validate_task_owner`
   （`task_manager.py:158`）、`start_task`、`complete()`（`task_manager.py:615`）；更根本的是
   assignee 要跑完整任务循环（收邮箱、返工、汇报），那就是协调循环本身。内联成员**必须是
   真实名册成员**，差别只在"谁、何时写这一行"。
2. **注册与拉起分离（S_05 不变量 1）是现成的。** `spawn_member` 只写 `UNSTARTED` 行；调度模式
   下交接 = leader 身份邮箱消息 + `auto_start_member`（S_22 不变量 5）。内联成员只需在
   `create_task` 里先落行再落任务，之后的交接**一行不改**。
3. **调度器是"看板驱动"的决策引擎**（S_22 不变量 4）：任何触发都跑同一组幂等扫描到不动点。
   "某成员名下还有没有非终态任务"正是一个看板事实，天然属于扫描。
4. **成员退场已是"状态驱动"**（S_03 机制 18）：leader 写 `SHUTDOWN_REQUESTED`，成员在
   `_harness_input_blocked` / `_on_idle_settled` / `finalize_member` 三处读自己的 DB 状态决定何时
   落 `SHUTDOWN`，round 在飞就等它结束。这条链已经解决了"别在成员干活时拆它"。
5. **"只有点名才唤醒"存在一半。** `send_message` 对 `ERROR` 收件人只在单播 / 多播时经
   `recover_member`（`ERROR→RESTARTING` CAS）复活，广播不复活（`tool_message.py:140-176`）；
   调度器交接与 interact `@name` 走 `TeamAgent.auto_start_member`，同样落到 `recover_member`。
6. **唤醒后上下文不丢。** `restart_teammate` 先 `cleanup_teammate` 替换死 handle
   （`spawn_manager.py:274`），再以 `initial_message=None` + 同一 child AgentSession 重建
   （进程内续 DeepAgent session；外部 harness 经 provider checkpoint `RESUME_IF_AVAILABLE` 续
   Claude session / Codex thread）。
7. **名册投递不含 status**（`prompts/messages.py:159` 刻意排除）；`list_members` 工具才显示。
8. **三组状态子集回答三个问题，不可合并**（`status.py` 头注释 / F_74）。

## 核心判断

✅ 值得做。它消除的是真实的流程负担，而且机制几乎都在：新代码是"调度器多一个扫描、
`create_task` 调度形态多一个字段、graceful 退场链多一个终点、`recover_member` 多认一个源状态"。

- **数据结构**：退场策略是**成员的属性**（`team_member.options.lifetime`），不是任务的属性——
  一个成员可以先后领多个任务，判定只能问"名下还有没有非终态任务"。内联建的成员与
  `spawn_teammate` 建的成员在运行时**没有任何区别**，区别只在出生那一刻。
- **复杂度**：不新增事件、工具、handler、启动路径、请求状态。
- **风险点**：`DORMANT` 必须在所有成员集合里归位；调度器的退场扫描必须避开"刚被唤醒、消息
  还没读"的窗口。

## 数据结构 / 状态机

### 1. 成员退场策略：`TeamMemberOptions.lifetime`

```python
class TeamMemberOptions(BaseModel):
    ...
    lifetime: Literal["shutdown", "dormant"] | None = None
```

- `None`：今天的行为，常驻，退场由 leader 显式 `shutdown_member`。
- `"shutdown"`：调度器判定名下任务清空后请求退场，成员落 `SHUTDOWN`。
- `"dormant"`：同一触发，成员落 `DORMANT`；行留在名册。

落在既有 `options` JSON 列（与 `builtin_model` / `worktree` 同路），不加物理列。

### 2. 新状态 `MemberStatus.DORMANT = "dormant"`

```text
READY/BUSY ──(调度器: shutdown_member)──▶ SHUTDOWN_REQUESTED ──(成员空闲时自落; lifetime=dormant)──▶ DORMANT
                                                              └──(lifetime≠dormant, 或 force)──▶ SHUTDOWN
DORMANT ──(被点名: recover_member CAS)──▶ RESTARTING ──▶ READY
DORMANT ──(leader shutdown_member)──▶ SHUTDOWN                 （无 runtime，直落，同 ERROR / passive 路径）
RESTARTING ──(重建失败回滚)──▶ DORMANT                         （与 ERROR 回滚 ERROR 对称；CAS 路径）
```

`MEMBER_TRANSITIONS` 增加：`SHUTDOWN_REQUESTED → DORMANT`、`DORMANT → [RESTARTING, SHUTDOWN]`。

与近邻状态的语义边界（写进 `status.py` docstring）：

| 状态 | runtime | 名册 | 谁能叫醒 |
|---|---|---|---|
| `PAUSED` / `STOPPED` | 拆 | 在 | 下一个 run cycle 的 `recover_team` **自动**复活 |
| `ERROR` | 拆 | 在 | 点名消息 / 调度器交接 / 冷恢复 |
| **`DORMANT`** | 拆 | 在 | **仅点名**：单播 / 多播 `send_message`、调度器交接、interact `@name`、群聊 `@name` |
| `SHUTDOWN` | 拆 | 在（终态） | 无 |

### 3. 状态子集归位

| 集合 | 含 `DORMANT`？ | 理由 |
|---|---|---|
| `MEMBER_DEPARTED_STATUSES` | 否 | 休眠不是放手；它名下无任务，锁语义无影响 |
| `MEMBER_UNREACHABLE_STATUSES` | 否 | 消息**要**投递：落邮箱行，唤醒后首次 `POLL_MAILBOX` 排空 |
| `MEMBER_SETTLED_STATUSES` | **是** | 休眠 = 干完了，不得挡 `is_team_completed` |
| `MEMBER_QUIESCENT_STATUSES` | **是** | 休眠 = 没在动，不得压 `team.idle` |
| `runtime.manager._MEMBER_FINALIZED_STATUSES` | **是** | 终态已由成员自己写，`finalize_member` 只收 kernel |
| `RecoveryManager.recover_team` 跳过集 | **是** | 冷恢复不得自动叫醒（与 `SHUTDOWN` 同列） |
| `MemberDao.claim_member_restart` 接受集 | **是** | 这是唤醒的 CAS |
| `has_unread_messages` 排除集（完成判定） | **是** | 休眠成员的未读广播不是"活没干完"，唤醒时再读 |

### 4. `create_task`（**仅调度形态**）节点新增 `member` 对象

```jsonc
{
  "task_id": "t-api",
  "title": "...", "content": "...",
  "member": {                       // 与 assignee 二选一；assignee 同名时宽容放行（见文末「落地后调整」）
    "member_name": "api-dev-1",     // 同 spawn_teammate 的命名约束
    "display_name": "...",
    "desc": "...",
    "prompt": "...",                // 可选，私有
    "model_name": "...",            // 可选
    "lifetime": "shutdown" | "dormant"   // 必填
  },
  "reviewer": [...], "depends_on": [...]
}
```

- 五个公开参数与 `spawn_teammate` 逐字相同，参数描述串复用 `spawn_teammate.*`，只新增
  `create_task.task.member` / `create_task.task.member.lifetime` 两个 key。
- `lifetime` 必填：内联的全部意义就是"任务内的人"，默认常驻等于把 `spawn_teammate` 换个位置写。
- `team_mode == "predefined"` 时 `member` 属性与其散文一起消失（与 `spawn_teammate` 被摘掉同一
  个门，capability 槽模式）。
- 只支持普通 LLM teammate；human / bridge / external_cli 各有能力门，本期不碰。
- `TaskCreateTool`（autonomous）**不改**。形态差异由类的选择吸收（F_57），`invoke` 里零模式分支。

## 决策

### D1. 内联成员 = `ScheduledTaskCreateTool` 内先 `spawn_member` 再 `add_graph`，assignee 即 member_name

模块级纯函数 `_materialize_inline_members(backend, tasks) -> list[str] | error`，只被调度形态调用：

1. 写库前批内校验：`member` 与 `assignee` 互斥；`member_name` 过 `_MEMBER_NAME_PATTERN`；
   批内不重名；`member_exists` 为假；不等于 leader。
2. 逐个 `backend.spawn_member(..., lifetime=...)` 落 `UNSTARTED` 行。
3. 把 spec 的 `assignee` 置为 `member_name`；之后 `_validate_assignees(required=True)` /
   `_validate_reviewers` / `add_graph` / 调度器交接**原样走**。
4. `add_graph` 失败（依赖环、`depended_by` 指向批内等）→ 补偿：`TeamBackend.delete_unstarted_member`
   删行（`WHERE status == 'unstarted'` 守卫，仓库第一条删成员路径），并在可演进工作区开启时释放
   `spawn_member` 准备的成员工作区（链接 + 动态真实目录，含 identity md）、清掉它预热的 cache
   字段——与 `spawn_member` 互为精确逆操作。两条注册路径（`spawn_teammate` / 内联）共用
   `tool_member.register_teammate`，card id / mode / options 只在一处推导。

不把 spawn 推迟到 `add_graph` 之后：`add_graph` 校验 owner 存在，先任务后人在现有契约里走不通。
不做跨表事务：member 是静态表、task 是按 session 动态表，补偿删除覆盖的是可枚举的"任务图校验
失败"，足够。

### D2. 退场判定 = 调度器第三个幂等扫描 `_reconcile_retirements`

`_scan` 的不动点循环里加一项，与 `_reconcile_starts` / `_reconcile_reviews` 并列。触发集不变
（`task_*` / `member_*` 事件、`POLL_TASK`、`SCHEDULER_SCAN`、激活）。

```python
async def _reconcile_retirements(self, task_manager) -> bool:
    candidates = await backend.list_retirable_members()        # options.lifetime 非空 且 status == READY
    if not candidates:
        return False
    open_counts = await task_manager.open_task_counts_by_assignee()   # 一次 GROUP BY，status ∉ {COMPLETED, CANCELLED}
    unread = await message_manager.members_with_unread(names)         # 一次查询（直达 + 广播水位）
    acted = False
    for member in candidates:
        if open_counts.get(member.name, 0) or member.name in unread:
            continue
        result = await backend.shutdown_member(member.name)           # 写 SHUTDOWN_REQUESTED + 通知 + 事件；幂等
        acted = acted or result.ok
    return acted
```

三条判定条件，各堵一个洞：

| 条件 | 堵什么 |
|---|---|
| `status == READY` | 已请求退场的（`SHUTDOWN_REQUESTED`）、已退场的、`UNSTARTED`（行已落、任务还没落的窗口）、`RESTARTING`（正在唤醒）都不碰；**`BUSY` 也不碰**——round 中途请求退场，成员正在等的回复会落到一个即将自落的成员身上，而发给 `SHUTDOWN_REQUESTED` 成员的消息唤不醒任何人。`READY` 的状态事件在 round 结束时本来就会再触发扫描，`BUSY` 没有任何收益 |
| 名下无非终态任务 | **含 `IN_REVIEW` / `PENDING` / `BLOCKED`**。提交后进 `IN_REVIEW` 时成员保持 `READY`，返工消息照常叫醒它；验收通过后 `scheduler_verified_report` 让它汇报一轮，汇报完 → 名下清空 → 退场。零新分支 |
| 本成员无未读消息 | 堵"刚被点名唤醒、`READY` 已写、消息还没读"的窗口——否则机制 18 会在无 round 在飞时直接落终态，那条消息永远没人读 |

**为什么退场请求复用 `shutdown_member` 而不是调度器直写 `DORMANT` + 拆 handle**：调度器看到的
`READY` 与成员真正空闲之间永远有窗口（drain 刚标已读、`BUSY` 还没写）。机制 18 的
请求 / 自落两阶段正是为这个窗口设计的——round 在飞就等它结束，不在飞才落终态。再造一条
"leader 直写 + 拆 runtime"的路径就是第二套 graceful 退场。

**为什么是扫描而不是事件**：`TASK_VERIFIED` 在调度模式下是 leader 自发的，被 `_filter_self`
吞掉只剩 `SCHEDULER_SCAN` 回声；`TASK_CANCELLED` 同理。扫描对所有触发一视同仁，恢复也不需要
独立路径（激活扫描即恢复，S_22 不变量 4）。

### D3. 终态由成员行上的策略决定：graceful 退场链的终点多一个取值

今天三处把 `SHUTDOWN_REQUESTED` 落成 `SHUTDOWN`：机制 18 无 round 分支 → `shutdown_self()`；
`_on_idle_settled` → `close_stream` → `finalize_member` 的 `SHUTDOWN_REQUESTED` 分支；
`MemberHandler` 的 `force` → `shutdown_self()`。收敛为一个解析点：

```python
def retire_target(lifetime: str | None, *, force: bool) -> MemberStatus:
    return MemberStatus.DORMANT if (lifetime == "dormant" and not force) else MemberStatus.SHUTDOWN
```

- `shutdown_self(force=False)` 与 `finalize_member` 的 `SHUTDOWN_REQUESTED` 分支都经它取终态
  （`lifetime` 从本成员 `options` 窄投影读一次）。
- `force=True` 永远 `SHUTDOWN`：force 本来就是"跳过一切收尾"的兜底。
- 终态为 `DORMANT` 的请求**不发告别通知**：成员无 round 即自落，通知只会躺着未读，且会被
  下面的休眠例外当成"有真实输入"而永远推迟自落。
- **休眠例外**（机制 18）：`SHUTDOWN_REQUESTED` 且无 round 在飞时，若终态为 `DORMANT` **且本成员有
  未读消息**，闸门放行而不自落——那是请求之后到达的真实输入（发给 `SHUTDOWN_REQUESTED` 成员的
  消息唤不醒任何人），放行的一轮结束后 `_on_idle_settled` 关流、finalize 再落 `DORMANT`。
- **成员自落的终态记在内存**（`TeamAgentState.retired_status`），`finalize_member` 先看它再看行：
  唤醒可能在旧 run cycle finalize 之前就把行 CAS 成 `RESTARTING`，读行会把旧 runtime 误判成
  "pause + 重标 READY"，与新 runtime 撞车并泄漏旧 harness。
- `lifetime=None`（所有 autonomous 成员、所有 `spawn_teammate` 建的成员）→ 永远 `SHUTDOWN`，
  行为与今天逐字一致。

**推论**：leader 对一个**正在运行**的 `lifetime=dormant` 成员调 `shutdown_member`，它会落 `DORMANT`
而非 `SHUTDOWN`；要彻底移除，对已 `DORMANT` 的成员再调一次（D4 直落），或 `force=True`。
这写进 `shutdown_member.md`：「退场终态由成员的 lifetime 决定；休眠成员再次 shutdown 即彻底关闭」。

### D4. 唤醒 = `recover_member` 多认一个源状态；规则是"点名就醒，撒网不醒"

`TeamBackend.recover_member` 的 CAS 从 `ERROR→RESTARTING` 扩成 `{ERROR, DORMANT}→RESTARTING`
（`claim_member_restart` 读当前行判断是否在接受集），重建失败回滚**原状态**。现有调用方自动获得唤醒：

| 入口 | 今天 | 本设计 |
|---|---|---|
| leader `send_message(to=name)` / `targets=[...]` → `_recover_members` | 复活 ERROR | **+ 唤醒 DORMANT** |
| 调度器 `_send_as_leader` → `auto_start_member` | 拉 UNSTARTED、复活 ERROR | **+ 唤醒 DORMANT**（给休眠成员派新任务即开工） |
| interact `@name` / 群聊 `@name` → `auto_start_member` | 同上 | 同上 |
| `send_message(to="*")` 广播 | 不复活 ERROR | 不唤醒（不变） |
| `autostart_unstarted()` / round-idle 对账 / `recover_team` 冷恢复 | 不碰 ERROR | 不唤醒 |

判据一条：**调用方明确写出了这个名字**才是意图；批量拉起与冷恢复不是。与 F_81（解散只认显式
意图）同一思路。

被唤醒的成员 `lifetime` 仍是 `dormant`：处理完消息、再次满足 D2 三条件 → 调度器再次请求 →
再次休眠。没有"醒了就常驻"的特例；要它常驻，给它派任务即可。

`shutdown_member` 对 `DORMANT` 成员：无 runtime 消费 `MEMBER_SHUTDOWN`，与 passive human / ERROR
同一条直落 `SHUTDOWN` 分支（`team.py:1238` / `:1271`），仍发 `MemberShutdownEvent`。temporary 团队
收尾时 leader 对休眠成员各调一次再 `clean_team`；`clean_team` 的"全员 SHUTDOWN"前置**不放宽**。

### D5. 不新增事件、不新增工具

休眠 / 自动 shutdown 经既有 `MEMBER_STATUS_CHANGED`（`new_status` 是自由串，monitor 无需改）；
唤醒经既有 `MEMBER_RESTARTED`；leader 看状态用既有 `list_members`（显示 `status=dormant`）。

### D6. 提示词与描述只动调度专属文件

- `create_task_scheduled.md`（cn/en）：加 `member` 字段说明；「assignee 必须已存在」改为
  「二选一：已存在成员名，或内联 `member` 对象」；强制流程第 1 条同步。
- `dispatch_scheduled_leader.md`（cn/en）：「spawn_teammate first, then create_task」改为
  「常驻成员先 spawn；任务内专家在 `create_task` 里内联并指定 lifetime」；加一节「休眠与唤醒」：
  点名即醒、广播不醒、休眠成员需 `shutdown_member` 后才能 `clean_team`。
- `shutdown_member.md`（cn/en）：加 D3 推论一句。
- `send_message.md` / `leader_workflow*.md` / `lifecycle_*.md` / `build_team.md`：**不动**——
  它们是两种模式共用的模板，S_09 1b 要求按模式分化的内容进该模式的模板。

### D7. 交接消息自带上下游简述；`note` 落盘；`view_task` 按 dispatch 分形态

勘查内联成员的首启输入时发现三处与本特性同一条链上的漂移，一并处理。

**现状**

- 调度交接消息（`scheduler_task_start.md`）已全文带 `task.content`，而
  `dispatch_scheduled_teammate.md` 第 6 条仍要成员"按需 `view_task(action=get)` 补看任务详情
  与依赖"。`get` 能多给的只有 `blocked_by` / `blocks` 两串 task_id——开工时上游必然已
  `COMPLETED`，一串已完成的 id 对执行没有信息量。这条指令唯一的真实价值是"看别人的任务"，
  文案却写成"补看自己的"。
- `view_task.md` 的"成员工作流"一节是自主模式的认领循环（list 找活 → `claim_task`）。调度
  成员没有 `claim_task`，系统提示词又明令"不要轮询 view_task 找活"，同一成员同时读到两段
  相反的指令。`claim_task` / `member_complete_task` / `verify_task` 都已按 dispatch 分形态，
  `view_task` 漏了。
- `member_complete_task(note=...)` 的描述要求写"产物文件路径和关键决策"，但 `invoke` 只把
  note 回显给调用者自己（`tool_task.py:1126`），`task_manager.complete()` 不收、任务行不存、
  事件不带。下游成员、reviewer、leader 谁都看不到。交接消息想带"上游产物摘要"，今天没有
  数据可带。

**决策**

1. **`note` 持久化到任务行**：任务表新增 `note` 列（nullable text）；`complete()` /
   `_submit_for_review()` 写入；`TaskDetail` 回带；`view_task(get)` 渲染。`TASK_COMPLETED` /
   `TASK_SUBMITTED_FOR_REVIEW` 事件**不**带正文（事件是唤醒信号，正文从行里读——与 F_63
   "能表查的一律现查"同一原则）。
   **DDL 迁移**：`note` 是动态任务表的新列，老 session 的已有表没有它。在
   `engine._ensure_dynamic_table_indexes` 的 `is_task` 分支里，紧接 F_62 的
   `max_review_rounds` 之后加一段同形的探测 + `ALTER TABLE "<table>" ADD COLUMN note TEXT`
   （nullable，无默认值），与 F_59 / F_62 给 `reviewer` / `review_round` / `max_review_rounds`
   补列是同一条路、同一个函数、同一次 session 建表时执行（`engine.py:463-477`）。不走 JSON
   列塞字段：`note` 是 `view_task(get)` 与交接模板都要读的一等字段，藏进 JSON 会让两个读点
   各自解一次。
2. **交接消息加上下游简述**：`task.*` 命名空间新增两个计算字段 `upstream_brief` /
   `downstream_brief`，由 `_resolve_namespaces` 在投递时多查一次依赖表与相关任务行：
   上游每条 `task_id · title · assignee · note`，下游每条 `task_id · title · assignee`；空集渲染
   为固定短句（"无"）。仍满足 F_63 三铁律：投递时现查、单遍替换不二次扫描、字段白名单。
   `scheduler_task_start.md` / `scheduler_task_start_plan.md`（cn/en）加「上下游」一节。
   `scheduler_rework.md` 不加——返工时上下文已在成员历史里。
3. **`dispatch_scheduled_teammate.md` 第 6 条改写**：「交接消息已含目标、验收标准与上下游
   简述；只有需要了解**其他成员**任务的内容时才 `view_task(action=get, task_id=<别人的>)`」。
4. **`view_task` 按 dispatch 分 desc_key**（`view_task` / `view_task_scheduled`，与
   `_VERIFY_TASK_DESC_KEY` 同一张形态表）：调度版删掉"成员工作流"认领循环与 `claimable`
   场景，保留 list / get 的输出说明；行为不变，只分描述。

**与内联成员的关系**：内联成员起来后的首条输入与普通成员完全相同（`enqueue_initial_mailbox_poll`
投递交接行，`INITIAL_POLL_TASK` 在调度子类是 no-op），本特性不为内联成员加任何首启注入。
D7 让这条首条输入自足，内联成员"一个任务干完即走"的模式才不需要额外的看板巡视。

**为什么并进 F_118 而不是独立特性**：三处都在"调度交接 → 成员开工"这一条链上，与内联成员
共用同一份模板、同一个 teammate 提示词、同一张形态表；拆开落地会让同一文件被两次特性各改
一半。`engine.py` 的任务表迁移是本特性多出的一处共用代码，但它是纯 schema 事实，与 dispatch
无关，和 `MemberStatus.DORMANT` 归一类。

## 拒绝的方案

### A. 像 reviewer 一样用无名册的临时 harness 跑 assignee
assignee 四处要名册行，且要跑完整协调循环。复制一套"无名册 teammate"等于第二个 `TeamAgent`，
违反铁律 2。

### B.（v1）退场判定放成员自己的 `_on_idle_settled`
一处覆盖两种模式——但特性只有调度模式有，这个"覆盖"就是把 autonomous 的共用 idle 边、
`StreamController` 注入点、成员侧新方法全部牵进来。调度器已经是这个模式专属的决策引擎，
且退场判定本来就是看板事实。v1 被评审否决，原因正是耦合。

### C. 调度器直写 `DORMANT` + `cleanup_teammate` 拆 handle（仿 `kernel.pause` 的 `_mark_live_teammates`）
`READY` 可见与成员真空闲之间有窗口，会把刚开始的 round 拆掉；要堵窗口只能再造一套请求 / 自落
两阶段——而机制 18 已经是那套。

### D. 新增 `DORMANT_REQUESTED` 请求状态
三处落终态、机制 18 表、四组集合全要复制一份。请求是一个（"请退场"），终态才分两种；终态由
成员行属性决定，一个解析函数即可。

### E. 休眠成员保持 runtime 存活，在 `_harness_input_blocked` 上按"有无点名未读"放行
进程模式下 LLM 进程空转；EventBus 继续消费只为丢弃；唤醒要在循环内部检测，而 leader 侧又有
`recover_member`——两套唤醒必然漂移。

### F. 复用 `PAUSED` / `STOPPED` 表示休眠
两者契约是"下个 run cycle `recover_team` 自动复活"（S_03 机制 17），休眠恰恰要求不自动复活。

### G. `lifetime` 挂任务行 / 内联对象里 `lifetime` 可选默认常驻 / 新增 `wake_member` 工具
分别：多任务成员无法判定；只是把 `spawn_teammate` 挪个位置；唤醒的意图就是"给它发消息"。

### H. autonomous 也支持内联成员
autonomous 没有调度器，退场判定无处安放（leader 收不到自发 `TASK_*`，`_filter_self` 后无回声）；
硬做就是 v1 的成员侧路径。按评审意见，本特性限定调度模式。

## 对既有契约的影响（需同步修订的 spec）

| spec | 修订点 |
|---|---|
| `S_22` | 新增不变量：第三个幂等扫描 `_reconcile_retirements` 及其三条件；不变量 5 的 `auto_start_member` 语义扩为"拉起 UNSTARTED / 复活 ERROR / 唤醒 DORMANT"；接口契约段补 `SchedulerHost` 无新增（仍只有 `deliver_input` / `auto_start_member`）；消息面「占位符标准」表 `task.*` 白名单加 `upstream_brief` / `downstream_brief`（投递时现查依赖表）；注明 `note` 不进事件 |
| `S_12` | `MemberStatus.DORMANT` 与转移表；四组集合归位；`TeamMemberOptions.lifetime`；`has_unread_messages` 排除集；任务行新增 `note` 列与 `TaskDetail.note`；顺带修正 `reviewer` 列仍写"JSON member 名列表"的过时描述 |
| `S_08` | 调度形态 `create_task` 新增 `member`（与 `assignee` 互斥、predefined 门控、补偿删除）；`send_message` 单播 / 多播唤醒 DORMANT；`shutdown_member` 终态按 lifetime、对 DORMANT 直落；`member_complete_task` 的 `note` 语义从"回显"改为"落盘并随 `view_task(get)` 可见"；`view_task` 加入形态表（`view_task` / `view_task_scheduled`）；顺带把 `tools/AGENTS.md` create_task 行"reviewer 须是真实成员"改正 |
| `S_05` | "拉起"之外新增"唤醒"：`recover_member` 接受集 `{ERROR, DORMANT}`；状态机图加 DORMANT |
| `S_06` | `finalize_member` 表加 DORMANT 行（已 finalized）；`SHUTDOWN_REQUESTED` 分支终态按 `retire_target` |
| `S_03` | 机制 18 的"落终态"改为 `retire_target`；表加 `DORMANT` 行（休眠无 runtime，理论不可达，按状态驱动写明"丢弃投递"保持完备）；机制 17 注明 PAUSED/STOPPED 与 DORMANT 的复活差异 |

勘查中顺带发现、**本期不修**的过时项：`S_22` 不变量 8 仍引用已删除的 `verdict.judge` /
`verify_vote_threshold`；`S_17` 状态图仍有 `PLAN_APPROVED`；`TeamRole.WORKER` docstring 声称写 DB
行但 worker backend 不写；`scheduler.py:466` 日志缺占位符；`_handle_undecided` 对临时 reviewer 发
催办邮件无人消费。

## 验证

单测镜像源码路径，`MemoryDatabaseConfig` + `spawn_mode="inprocess"`：

| 文件 | 用例要点 |
|---|---|
| `tools/test_tool_task.py` / `tools/test_tool_variants.py` | 调度形态：内联 `member` 落行 + assignee 为其名；与 `assignee` 同给报错；重名 / 已存在 / 非法名 / leader 名报错；`add_graph` 失败后补偿删除；predefined 下无 `member` 属性且偷传被拒；**autonomous 形态 schema 无 `member`，偷传被拒** |
| `agent/test_team_scheduler.py` | `_reconcile_retirements`：有 `IN_REVIEW` 不请求；全终态 + 无未读 → `shutdown_member` 被调一次且幂等；有未读不请求（唤醒窗口）；`UNSTARTED` / `SHUTDOWN_REQUESTED` / `RESTARTING` 不碰；交接给 DORMANT 成员触发 `auto_start_member` |
| `test_member.py` / `test_status.py` | `SHUTDOWN_REQUESTED→DORMANT`、`DORMANT→RESTARTING`、`DORMANT→SHUTDOWN` 合法；`READY→DORMANT` 直达非法；四组集合归位 |
| `agent/test_team_agent.py` / `runtime/test_manager.py` | `shutdown_self` 与 `finalize_member` 按 `retire_target`：lifetime=dormant → DORMANT，force → SHUTDOWN，None → SHUTDOWN；`finalize_member` 对 DORMANT 只收 kernel |
| `test_team.py` | `recover_member` 对 DORMANT CAS 成功并重建、失败回滚 DORMANT；`autostart_unstarted` 不碰 DORMANT；`shutdown_member(DORMANT)` 直落；`is_team_completed` 不被 DORMANT 成员及其未读广播阻塞；`delete_unstarted_member` 只删 UNSTARTED |
| `tools/test_tool_message.py` | 单播 / 多播唤醒 DORMANT；广播不唤醒 |
| `agent/test_recovery_manager.py` | `recover_team` 跳过 DORMANT |
| `agent/test_member_activity.py` | DORMANT 计入静止集 |
| `tools/test_tool_variants.py::test_every_toolset_assembles` | 新 locale key / 片段两语言齐全 |
| `test_task_manager.py` | `complete(note=)` 落盘；`view_task(get)` 回带；`IN_REVIEW` 路径同样落盘 |
| `test_message_template.py` | 有上游 / 无上游 / 上游带 note 三种渲染；依赖行被删时降级不炸；填充值含 `{{` 不二次展开 |
| `tools/test_tool_variants.py` | `view_task` 两 desc_key 装配；调度版无 `claimable` 场景文案 |
| `test_team_policy_rail.py` / `test_policy.py` | 调度 teammate 提示词不再含"补看任务详情" |
| `tools/database/test_engine.py`（或既有迁移用例所在文件） | 对无 `note` 列的旧任务表执行 `create_cur_session_tables` 后列存在；对已有列的表幂等不报错 |

各层用例均已落地（`test_member.py` 2 例、`test_member_options.py` 1 例、`runtime/test_manager.py` 2 例、
`test_team.py` 8 例、`tools/test_tool_message.py` 3 例、`tools/test_tool_variants.py` 10 例含参数化、
`agent/test_team_scheduler.py` 5 例、`agent/test_recovery_manager.py` 1 例、`test_message_template.py` 3 例、
`test_task_manager.py` 2 例、`test_database.py` 1 例、`test_policy.py` 2 例含参数化）。调度器退场扫描的
单测用 mock backend 让候选在被请求后离开候选集（与真实行翻成 `SHUTDOWN_REQUESTED` 同构），验证不动点收敛。

端到端（inprocess，scheduled，`build_team → create_task(member=..., lifetime=dormant)` → 交接 → 完成 →
验收 → DORMANT → 点名唤醒 → 再次 DORMANT → `shutdown_member` → `clean_team`）需要真实模型，
归 `tests/system_tests/`，本次未加。

## 代码审查（high）后的修正

落地后用 `/code-review high` 自审出 10 条，全部修掉：

1. **进程模式下休眠成员会被健康检查自动重启**：`on_teammate_unhealthy` 原把 `DORMANT` 放进"可直接重启"集合；改为与 `SHUTDOWN` 同列跳过（`test_unhealthy_dormant_member_is_not_restarted`）。
2. **退场扫描候选含 `BUSY`**：改为仅 `READY`（见 D2）。
3. **请求与自落之间的消息被搁浅**：休眠类退场不发告别通知 + 机制 18 休眠例外（见 D3；`test_message_handler_gate.py`）。
4. **回滚只删行不清工作区**：`delete_unstarted_member` 释放工作区并清 cache（见 D1）。
5. **`task_links` 对每条模板消息都查 2+N 次**：先加载模板、只在正文含 `task.upstream_brief` / `task.downstream_brief` 时解析；行按 `get_tasks_by_ids` 一次 `IN` 取回。
6. **唤醒与旧 runtime 的 finalize 赛跑**：`shutdown_self` 把终态记进 `TeamAgentState.retired_status`，`finalize_member` 先看它（见 D3）。
7. **`typing.Dict` / `Optional` 新代码**：改为 `dict[str, int]` / `str | None`。
8. **内联注册复制 `spawn_teammate`**：抽 `tool_member.register_teammate`，命名校验复用 `tool_member.validate_member_name`。
9. **`members_with_unread` 复制广播水位逻辑**：`has_unread_messages` 与它共用 `_direct_unread_recipients` / `_broadcast_unread_recipients`。
10. **临时 reviewer 的 `view_task` 仍是自主文案**：改用 `view_task_scheduled`。

## 已知遗留（设计阶段即预见）

- `SHUTDOWN` 成员的名字不可复用（`spawn_member` 拒绝已存在行）。`lifetime=shutdown` 用过的名字
  在本团队内永久占用，leader 自行编号。放开需要删成员或改名路径，本期不做。
- teammate 发给休眠同事的直接消息不唤醒：`recover_member` 是 leader 门控的，消息落邮箱行等
  leader 唤醒时一并读到；与今天 teammate 消息不能复活 ERROR 同事一致。
- 休眠期间看板 / 成员事件不缓冲（本来就从看板重推导，唤醒时 `INITIAL_POLL_TASK` 在调度子类
  是 no-op，交接消息才是开工信号）；邮箱消息完整缓冲。
- 外部 harness 成员休眠再唤醒依赖 provider checkpoint 存在；首次 checkpoint 未落盘就休眠的极端
  窗口会以全新 session 醒来，与今天 ERROR 重启一致。
- 退场扫描的"无未读"条件把 leader 发给它的任何消息都当作"还有事"，所以 leader 对一个本该
  退场的成员多问一句，它会多活一轮再退——这是期望行为，不是缺陷。
- （2026-10-15 追补，B_04）"autonomous 下没人会写 `lifetime`"的前提被共享文案打破：
  `create_task.task.assignee` 与 `shutdown_member` 描述两种形态共用，都提到内联成员，autonomous
  的 leader 照着传了 `member`，`TaskCreateTool` 静默丢弃后落了无主任务。修复见
  `docs/bugfix/B_04_autonomous-create-task-inline-member-leak.md`：参数描述分命名空间、
  `shutdown_member` 的 lifetime 段改 capability 槽、autonomous `create_task` 显式拒绝 `member`。

## 已定的决策点（v1 评审）

1. 唤醒范围按"点名就醒"：单播 / 多播 `send_message`、调度器交接、interact `@name`、群聊 `@name`。
2. `lifetime` 本期不暴露给 `spawn_teammate`。
3. `clean_team` 不把 `DORMANT` 视作可清理，休眠成员仍需 `shutdown_member`。

## 落地后调整（2026-10-14）：同名 `assignee` 宽容放行

实测 leader 常在写了 `member` 的节点上顺手再填一个同名 `assignee`，原实现一律报
`give either 'assignee' or 'member', not both`，整批失败、白耗一轮。两者指向同一个人时不存在歧义，
拒绝只是在惩罚冗余。

- **决策**：`_validate_inline_members` 只在 `assignee` 与 `member.member_name` **不同**时报冲突
  （错误里点名两者并给出二选一的修法）；相同则放行，`_redundant_assignee_note` 在绑定内联名之前
  收集这些节点，成功结果带 `data["note"]`，`render_for_llm` 追加一行「下次只填 `member`」。
  手法与 `send_message` 对 `targets == [to]` 的宽容一致。
- **拒绝**：静默放行不提醒——模型学不到规范写法，冗余会一直重复；异名时取 `member` 覆盖
  `assignee`——那是真实意图冲突，猜哪个都可能把任务交错人。
- **描述**：`create_task_inline_member` 片段仍写「二选一」——规范写法不变，宽容只是容错。
- **验证**：`test_scheduled_create_task_tolerates_assignee_repeating_inline_member` /
  `test_scheduled_create_task_inline_member_alone_carries_no_note`；异名用例改断言
  `conflicts with inline member`。
