---
id: B_20261104_2030_cancellation-cause-lifecycle
title: 压缩取消一律误报 failed 及标记生命周期残留窗口缺陷
type: B
author: CharlesWang2030
author_email: 93426525@qq.com
modules:
  - openjiuwen/core/context_engine
  - openjiuwen/harness
  - openjiuwen/agent_teams
files:
  - openjiuwen/core/context_engine/context/cancellation.py
  - openjiuwen/core/context_engine/context/context.py
  - openjiuwen/core/context_engine/context/processor_state_recorder.py
  - openjiuwen/core/context_engine/context/context_state.py
  - openjiuwen/core/context_engine/context_engine/__init__.py
  - openjiuwen/harness/deep_agent.py
  - openjiuwen/agent_teams/harness/native_harness.py
  - openjiuwen/agent_teams/runtime/manager.py
  - openjiuwen/extensions/observability/runtime.py
  - tests/unit_tests/core/context_engine/test_cancellation_cause.py
  - tests/unit_tests/core/context_engine/test_context_compression_state_stream.py
  - tests/unit_tests/harness/test_deep_agent_abort_cancellation_cause.py
  - tests/unit_tests/agent_teams/runtime/test_manager.py
  - tests/unit_tests/agent_teams/harness/test_abort.py
designs: []
design_note: c4e52b7c 引入 cancellation cause 注册表属于常规缺陷修复，未归档独立设计；本次为对该提交检视发现的标记生命周期缺陷的后续修复，沿用注册表现有契约（TTL、非消费式 resolve、前缀继承）。
---

# 压缩取消一律误报 failed 及标记生命周期残留窗口缺陷修复记录

## 现象与影响

用户在上下文压缩过程中点击暂停/取消时，`asyncio.CancelledError` 不携带来源信息，
`ContextProcessorStateRecorder` 一律以 `failed` / `processor_cancelled` 上报
`ContextCompressionState`，前端无法区分"用户主动暂停/取消"与"压缩内部失败"，
把用户的正常操作展示为失败。

c4e52b7c 引入按 session id 键控的 cancellation cause 注册表修复了上述误报，
但检视发现其标记生命周期存在三处缺陷（残留窗口误报，方向与原缺陷相反）：

1. `DeepAgent.abort()` 无条件打 CANCEL 标记：无在飞目标的 no-op abort 也会留下标记，
   30 秒 TTL 内后续内部失败导致的取消被误报为 `cancelled` / `user_cancel`；
2. 团队级 PAUSE 标记 resume 后不清理：`TeamRuntimeManager.pause()` 按 team session id
   打标记，但 `RESUME_FROM_PAUSE` 恢复路径全程无 `clear_cancellation_cause`，
   `NativeHarness.resume()` 只清自身 harness session id，恢复后 TTL 窗口内的内部取消
   经 `<parent>:` 前缀继承被误报为 `paused` / `user_pause`；
3. `resolve_cancellation_cause` 不消费标记且文档未注明该语义，调用方无从得知
   resume 方必须显式 clear 的义务；
4. `NativeHarness.abort()` 覆写 `DeepAgent.abort()` 且未调用 `super()`：父类新增的
   CANCEL 打标在原生 harness 上永不执行，而团队主场景（`TeamHarness.abort`、
   `ProtocolAdapter._interrupt_turn`、`StreamController` 的停止/中断）恰都走这条
   覆写实现，与同文件 `pause()` 已打标的写法不对称（pause 打、abort 不打），
   导致团队场景用户停止时压缩仍报 `failed`；
5. `NativeHarness.abort()` 补标后为无条件打标：`_on_abort` 在 IDLE / PAUSED /
   无活跃 round 时是纯 no-op（暂停落定后 round 经 `paused_query` 停放、
   `_st.active` 已置 None），这些路径的 abort 留下 30 秒残留标记，把后续真实
   内部取消误报为 `cancelled` / `user_cancel`，与 PR 目标反向；
6. `stop_team` 对 team session 打 CANCEL 标记，但冷路径（COLD_RECOVER /
   NEW_TEAM_IN_SESSION / CREATE）均无清除点：同 session 30 秒内重建团队时，
   残留标记经前缀继承把新团队的内部失败误报为用户操作；
7. `DeepAgent._mark_cancellation_cause` 忽略 `abort(session=...)` 的目标会话：
   守卫检查 `proposal_session`，标记却固定落在绑定会话（`_resolve_context_session_id()`），
   二者不一致使异会话 abort 路径的修复失效（当前全仓无该形态调用方，属契约级缺陷）；
8. `NativeHarness.pause()` 无条件打 PAUSE 标记（c4e52b7c 存量）：`_on_pause` 在
   非 RUNNING 或无活跃 round 时是 no-op，这些路径的 pause 留下 30 秒残留标记，
   后续真实内部取消被误报为 `paused` / `user_pause`；
9. `NativeHarness.abort()` 从 PAUSED 丢弃停放 round 后不清除 PAUSE 标记：
   丢弃不产生任何 CancelledError，标记失去用途成为纯残留，同 session 新 round
   在 TTL 窗口内的内部失败被误报为用户暂停；相关测试曾以断言固化该残留行为；
10. `NativeHarness` 经 `send(PAUSED)` 暖恢复/中断恢复绕过 `resume()`：停放
    round 被原地续跑但 PAUSE 标记不清理，恢复后 round 内的内部失败仍会解析为
    `user_pause`；
11. `TeamRuntimeManager.pause()` 在条目已 PAUSED 的幂等 no-op 分支仍刷新 PAUSE
    标记，延长陈旧标记的 TTL 窗口；
12. `resolve_cancellation_cause` 的继承扫描按 dict 插入顺序返回首个前缀匹配：
    父级标记（如团队 `s1 → PAUSE`）会遮蔽后插入的更具体子级标记
    （`s1:member-a → CANCEL`），深层子会话（`s1:member-a_sub_x`）取消原因
    错标，且两个标记均为用户真实操作、日志难以定位遮蔽来源。

## 关联设计与当前契约

无独立 D。当前契约以 c4e52b7c 实现为准：

- 注册表（`openjiuwen/core/context_engine/context/cancellation.py`）：mark /
  resolve / clear 三操作，30 秒 TTL，条目上限 1024，线程安全；
- 未标记的取消保持 legacy 行为（`failed` / `processor_cancelled`），注册表不会
  自行把失败改标；
- 子会话前缀继承：`<parent>:<agent>`（team protocol adapter）与
  `<parent>_sub_<id>`（subagent）；
- 标记不消费：一次用户操作可取消多个处理器（含子会话上下文），每次 resolve 都
  需要看到同一 cause。本次修复在模块与 resolve docstring 中显式注明该契约及
  resume 方的 clear 义务。

## 根因与修复决策

原缺陷根因：`CancelledError` 无来源信息，取消处理器只能一律按失败上报。

生命周期缺陷根因：标记的移除依赖 clear / 覆盖 / TTL 过期三条路径，但
(1) `abort()` 在无法实际取消任何目标时也打标记；(2) 团队 resume 路径遗漏 clear；
(3) 非消费式 resolve 的残留窗口语义未文档化，新增调用方容易重蹈覆辙。

修复决策与备选：

1. `abort()` 增加 `_has_in_flight_abort_target(session)` 守卫，精确对应 abort 实际
   会取消的三类目标：活跃交互 round（`_active_interaction_round`）、流式任务循环
   后台任务（`_stream_process_task` 存活）、该 session 的 subagent 控制项
   （`_subagent_controls`）。备选"abort 结束后检测未取消则回滚标记"被拒绝：无法
   可靠检测"实际发生了取消"。
2. `TeamRuntimeManager._apply_action` 的 `RESUME_FROM_PAUSE` 分支在
   `_pre_run_with_inputs` 之前调用 `clear_cancellation_cause(team_session.get_session_id())`，
   与 `NativeHarness.resume()` 形成对称闭环。
3. docstring 注明残留窗口语义（不改行为）；备选"resolve 精确命中主 session 时消费
   标记"被拒绝：会破坏子会话多次 resolve 同一标记的场景。
4. `NativeHarness.abort()` 在 `_require_alive()` 之后、投递 `_CmdAbort` 之前补
   `mark_cancellation_cause(self.session_id, CancellationCause.CANCEL)`，与同文件
   `pause()` 对称。备选"覆写内调用 `super().abort()`"被拒绝：父类实现走交互 round
   取消链，与 native harness 的 supervisor 命令队列模型不匹配。
5. `NativeHarness.abort()` 打标增加 `self._st.active is not None` 守卫：IDLE /
   PAUSED / 无活跃 round 三条 no-op 路径被同一条件精确排除（暂停落定后
   `_st.active` 已置 None），RUNNING / PAUSING 的真实取消路径不受影响。决策 4 中
   "no-op 残留由 TTL 兜底"的权衡据此收紧为守卫前置。
6. `TeamRuntimeManager._apply_action` 冷路径分支入口统一
   `clear_cancellation_cause(session_id)`：三条 kind 一次覆盖，CREATE 全新 session
   时为无害 no-op；旧团队在 `stop_team` 中已 await `stop_coordination` 拆除完毕，
   入口清理不会剥夺旧团队的取消归因。
7. `_mark_cancellation_cause` 增加可选 `session` 参数，`abort()` 传入
   `proposal_session`，使标记落点与守卫检查的会话一致；未传时维持绑定会话回退，
   既有调用方（`cancel_round` 路径）行为不变。
8. `NativeHarness.pause()` 打标增加与 `abort()` 对称的 `self._st.active is not None`
   守卫：非 RUNNING 或无活跃 round 的 no-op pause 不再打标（PAUSING 中重复
   pause 会刷新既有标记，语义仍为用户暂停，可接受）。
9. `NativeHarness.abort()` 增加 `elif phase is PAUSED: clear_cancellation_cause`
   分支：abort 是 PAUSED 的另一出口（resume 之外），丢弃停放 round 时同步清除
   PAUSE 标记，与"标记者即清理者"对称原则闭环；对应测试断言从固化残留
   （`resolve is PAUSE`）改为防残留（`resolve is None`）。
10. `_on_send` 的 PAUSED 分支（暖恢复与中断恢复两条路径）在启动 round 前调用
    `clear_cancellation_cause(self.session_id)`：send 恢复与 resume、abort 一样
    是 PAUSED 的出口，标记清理在 supervisor 侧完成、无竞态。
11. `TeamRuntimeManager.pause()` 打标加 `entry.state is not RuntimeState.PAUSED`
    守卫：幂等 no-op 暂停不再刷新标记 TTL；真实 RUNNING→PAUSED 转换照常打标。
12. `resolve_cancellation_cause` 继承扫描改为最长匹配 key 优先：收集全部前缀
    命中后取 key 最长者返回，成员级标记不再被团队级标记按插入顺序遮蔽；
    精确命中路径与近失配（near-miss）行为不变。

## 回归证据与适用范围

候选版本：本分支 `fix/mdfile_decode_fail` 工作区（基于含 c4e52b7c 的 develop）。
环境：Windows，`uv run pytest`。

- `tests/unit_tests/core/context_engine/test_cancellation_cause.py`：
  13 passed（含新增 `test_resolve_does_not_consume_the_mark`：双次 resolve +
  子会话继承 + clear 后归零）；
- `tests/unit_tests/harness/test_deep_agent_abort_cancellation_cause.py`：
  13 passed 中含本文件 5 例——no-op abort 不打标记、活跃 round 打 CANCEL、
  存活 stream task 打 CANCEL、已结束 stream task 不打标记、subagent 控制项存在
  时打 CANCEL；
- `tests/unit_tests/agent_teams/runtime/test_manager.py`：26 passed（含新增
  `TestResumeFromPauseClearsCancellationCause`：PAUSED 池项经 `activate()` 恢复后
  团队级 PAUSE 标记被清理）；
- `tests/unit_tests/core/context_engine/test_context_compression_state_stream.py` +
  `tests/unit_tests/harness/test_deep_agent_interaction.py`：43 passed，
  确认 c4e52b7c 原有 paused/cancelled 端到端行为与交互回归无破坏；
- `tests/unit_tests/agent_teams/harness/test_abort.py`：9 passed（含
  `test_abort_marks_cancel_cause_on_harness_session` 覆写路径打标，
  `test_idle_abort_does_not_mark_cancel_cause`、
  `test_abort_from_paused_discards_round_without_residual_marks`：
  IDLE no-op abort 不打标、PAUSED abort 丢弃 round 后 CANCEL 与 PAUSE 标记
  均无残留，及 `test_idle_pause_does_not_mark_pause_cause`：IDLE no-op pause
  不打标）；
- `tests/unit_tests/agent_teams/harness/test_pause.py`：10 passed（含本轮新增
  `test_send_during_paused_clears_pause_cause`：send 暖恢复后 PAUSE 标记清除）；
- `tests/unit_tests/core/context_engine/test_cancellation_cause.py`：10 passed
  （含本轮新增 `test_inheritance_prefers_most_specific_ancestor`：最长前缀
  优先，正反两组父/子标记组合及裸父级继承）；
- `tests/unit_tests/agent_teams/runtime/test_manager.py`：29 passed（含本轮新增
  `TestIdempotentPauseDoesNotRefreshMark` 两用例：RUNNING→PAUSED 照常打标、
  已 PAUSED 幂等 no-op 不再刷新标记）；
- `tests/unit_tests/harness/test_deep_agent_abort_cancellation_cause.py`：
  6 passed（含本轮新增 `test_abort_marks_target_session_not_bound_session`：
  异会话 `abort(session=...)` 标记落在目标会话、绑定会话无标记）；
- `tests/unit_tests/agent_teams/runtime/test_manager.py`：27 passed（含本轮新增
  `TestColdActivationClearsCancellationCause`：残留 CANCEL 标记经 CREATE 冷路径
  `activate()` 后被清理）；
- `tests/unit_tests/agent_teams/harness/test_pause.py` +
  `tests/unit_tests/core/context_engine/test_cancellation_cause.py` +
  `tests/unit_tests/core/context_engine/test_context_compression_state_stream.py`：
  26 passed，pause / resume / 注册表 / 压缩状态流无回归；
- `tests/unit_tests/core/context_engine/test_compression_state_modified_messages.py`
  中 4 例在 Windows 本机失败（`OSError: [Errno 22]`），已验证基线 d51ec659
  同样失败，属环境既有问题，与本次改动无关。

未验证范围：真实前端联调（`status` 新增 `paused`/`cancelled` 枚举值的展示）；
多进程部署下注册表为进程内的语义边界。重验条件：涉及 abort/resume 生命周期
改动或注册表契约变化时重跑上述四组测试。

## 防错模式与资料同步

- 防错模式：**旁路标记类机制必须保证"标记者即清理者"的对称性**——每个打标记的
  入口都要么只在真实生效时打（守卫），要么在对应的反向操作中清理（resume 清
  pause）。已写入 `cancellation.py` 模块 docstring 作为该模块契约。
- 资料同步：本文档为唯一记录；`modules` 覆盖 core/context_engine、harness、
  agent_teams 三处，索引由 sync_documents.py 生成。

## 遗留与后续

- `context_state.py` 的 `status` 新增 `paused`/`cancelled` 是前端可见契约扩展，
  需确认前端已同步支持（超出本仓范围）；
- `_CORE_CLASSES` 未导出 `reset_cancellation_cause` / `DEFAULT_TTL_SECONDS`
  （模块 `__all__` 有），当前无消费方，暂不处理。
