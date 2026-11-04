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
   resume 方必须显式 clear 的义务。

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
  确认 c4e52b7c 原有 paused/cancelled 端到端行为与交互回归无破坏。

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
