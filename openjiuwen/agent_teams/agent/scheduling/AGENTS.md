# Scheduling — 调度模式决策引擎（F_62）

leader 侧的调度分发 runtime，与 `coordination/`（唤醒层）平齐：coordination 只唤醒、不决策；本包只决策、不直接触碰其他成员的 round——所有成员交接都是 **leader 身份的邮箱消息**。仅在 `spec.dispatch_mode == "scheduled"`（静态配置）的 leader kernel 上构造，团队建成后激活。

## 文件地图

| 文件 | 职责 |
|---|---|
| `scheduler.py` | `TeamScheduler`：事件粗筛 + 双幂等扫描（开工 / 验票）+ `SchedulerHost` 窄协议 |
| `verdict.py` | 纯函数投票判定（`judge`：pass 配额 `ceil(threshold×n)`，fail 不可达即败）——策略可整体替换，不碰票据存储与状态机 |
| `render.py` | 两类收件人两套机制：**成员**交接只组投递载荷 `meta_*`（`{template, refs, params}`，`content=""`），文案在 `prompts/<lang>/scheduler_*.md`、投递时渲染（F_63）；**leader** 摘要/升级走 `deliver_input` 直投，仍是 `i18n.py` 的 `scheduler.leader_*` 一行短串 |

## 核心不变量

1. **调度器不理解事件，只理解看板。** 任何触发（task/member transport 事件、`POLL_TASK`、`SCHEDULER_SCAN` 回声、激活）都跑同一对幂等扫描；恢复 = 激活扫描本身，无独立恢复路径。事件仅有两个例外职责：终态摘要与 `TASK_LIST_DRAINED` 收尾（事件只发一次，扫描推不出"刚刚发生"）。
2. **交接 = leader 身份邮箱消息，投递即启动。** `_send_as_leader` 先落邮箱行（持久，离线成员首次 sweep 补投）再 `host.auto_start_member`（复用 `UNSTARTED→STARTING` CAS，幂等）。成员在线与否**不是**开工前置条件，调度器不做在线过滤。
3. **发送存意图，投递才成文（F_63）。** 邮箱行的 `content` 恒空，`meta` 带模板 key + `refs`；文案由 `message_template.expand_message` 在**投递时刻**按收件人语言渲染，`{{task.*}}` 取的是**当时**的任务行——所以队列里躺了很久的交接不会投出过期的任务简报，leader 中途 `update_task` 也立刻对未投递消息生效。能表查的一律进 `refs` 现查；`params` 只放表答不出的瞬时值（某轮 fail feedback 聚合、解析后的轮数上限）。**不要**为了"省一次查询"把任务正文快照进消息。
4. **`SchedulerHost.deliver_input` 只准注入 leader 自身**（终态摘要 / 升级 / 收尾），对成员永不直投。
5. **判定在本包，票据在 DB。** `verify_task`（scheduled 团队）只追加票行；`verdict.judge` 三值判定；settle 经 `task_manager.settle_review` 的 `IN_REVIEW` 源态 CAS——单判定者 + CAS 保证不双结算。
6. **轮数升级与停摆升级共用一条注入路径**，按 `(task_id, review_round)` 去重；升级后任务留 `IN_REVIEW`，决定性迟票仍可正常 settle（不再重复升级）。
7. **leader 自发事件的可见性靠 `SCHEDULER_SCAN` 回声**：kernel 的 `_filter_self` 丢弃 self 事件时，若调度器激活且是 `task_*` 事件，改投一个 `SCHEDULER_SCAN` inner 事件——coordination 无 handler 监听它，调度器把它当纯扫描提示。
8. **异常语义与 coordination 对齐**：`on_event` 吞普通异常（log + 下次触发重试），绝不让 bus loop 挂掉。

## 生命周期（kernel 接线）

- `CoordinationKernel.setup` 仅在 `spec.dispatch_mode == "scheduled"` 的 leader 上构造（休眠态）；其余 kernel 不构造。
- 激活点两个：`notify_team_built()`（build_team 成功回调，先于任何 spawn / create_task）与 `kernel.start()`（team 行已存在——warm resume / 冷恢复）。
- wake 路径：`kernel._build_wake_callback()` 组合 "coordination dispatch → scheduler.on_event"。
- `pause()` / `stop()` 即 `deactivate()`；内存记账（送审去重 / 催办节流 / 升级去重 / 摘要去重）不持久化——重启后最坏重发一次送审或升级，board 真相在 DB。

## 配置消费

`TeamAgentSpec.verify_vote_threshold`（默认 2/3）、`default_max_review_rounds`（默认 3）、`review_stall_timeout`（默认 1800s）只在本包消费，不跨进程镜像。催办间隔 `_REVIEW_RENUDGE_SECONDS`（600s）是包内常量。

设计上下文见 `docs/features/F_62_scheduled-dispatch-runtime-and-review-voting.md`（调度器 + 投票）、
`docs/features/F_63_scheduler-message-templating-and-delivery-render.md`（交接消息的两阶段渲染）与
`docs/specs/S_22_scheduling-runtime.md`。
