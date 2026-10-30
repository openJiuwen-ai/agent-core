---
title: Runner 根任务组跨事件循环复用陈旧 owner，stop 抛 attached to a different loop
author: spacewing
author_note: PR !2323 作者署名；不代表代码 owner
id: B_20261029_2019_runner-root-task-group-cross-loop-reuse
type: B
modules:
- openjiuwen/core/runner
files:
- openjiuwen/core/runner/runner.py
designs: []
design_note: 无前置设计文档，修复源自 Runner 单例跨事件循环缺陷的渐进修复 PR !2323
author_email: ljcjeans@163.com
---

# Runner 根任务组跨事件循环复用陈旧 owner，stop 抛 attached to a different loop

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-29 |
| 严重程度 | 高（多 app/多 TestClient 场景下 Runner 无法干净停止，teardown 异常冒泡为未捕获 RuntimeError） |
| 范围 | `openjiuwen/core/runner/runner.py` |
| 测试基线 | `tests/test_runner_ensure_root_task_group.py` 8 passed；`tests/unit_tests/core/runner` 484 passed（4 errors 为 develop 预存环境问题，干净树对照一致） |
| 关联 | Runner 单例跨事件循环缺陷的渐进修复；PR !2323；上游 !2950 修复了同族的 ReadWriteLockManager 跨 loop cancel |

## 现象

多个 FastAPI app（或 pytest TestClient）各自 lifespan 串行驱动同一进程级单例 `GLOBAL_RUNNER`、
各跑在独立 asyncio loop 上时，第二个 app 的 teardown 调 `Runner.stop()` 抛
`RuntimeError: Task ... got Future ... attached to a different loop`，root task group 泄漏。
单跑任一 app 正常；跨 app 串行混跑必现。

## 根因

`_ensure_root_task_group` 的复用判断只检查字段非 None，不检查 owner task 绑定的 loop
是否仍存活、是否当前 running loop。owner 由 `asyncio.create_task` 创建、绑死创建它的 loop；
单例跨 loop 复用时，第二个 loop 直接复用绑在旧 loop 上的 owner，`stop()` 里 `await owner`
跨 loop 等待即抛 RuntimeError。

旧实现另有结构性缺陷：生命周期四元组（owner/ready/stop/group）是四个平铺实例字段，
rebuild 时逐个清空再逐个写入，陈旧 owner 的 teardown（owner loop 的 finally）与新 loop 的
rebuild 会交叉写同一批字段，存在半清理状态窗口。

## 修复

1. 复用判断收敛为单一谓词 `_is_live_task_on_running_loop`：owner 未结束且
   `owner.get_loop() is asyncio.get_running_loop()`。一次 identity 检查同时覆盖三类陈旧：
   loop 已关闭、loop 存活但 foreign、owner 已结束。
2. 四元组收敛为单一状态对象 `_RootTaskGroupState`，`_root_state` 单引用持有。rebuild 整体
   换引用；陈旧 owner 的 teardown 只改自己那份 state（`if self._root_state is state` 守卫），
   永不覆盖新 loop 发布的 state。
3. `_close_root_task_group` 的 cancel_all / stop.set / cancel_scope / await 全部收敛到
   "owner 属于当前 loop" 分支内；跨 loop 的 stop 走 drop-only 分支并记 warning，与上游
   !2950 对 ReadWriteLockManager 陈旧 cleanup task 的处理惯例一致（closed/foreign loop 上的
   task 不能 cancel，cancel 的调度本身依赖 loop 活着）。

## 验证

- `test_reuse_after_loop_close_rebuilds_on_current_loop`、
  `test_reuse_on_live_foreign_loop_rebuilds_on_current_loop`：修复前必挂、修复后通过。
- `test_cross_loop_stop_never_touches_the_stale_state`：跨 loop stop 后陈旧 state 的 stop
  event 未被 set（证明未跨 loop 驱动陈旧对象）。
- `test_stale_owner_exit_does_not_clobber_rebuilt_state`：陈旧 owner 退出时不覆盖新 state
  的 group 槽（identity 守卫的回归）。
- 单 loop 基线与同 loop 幂等启停不受影响。

## 遗留

Runner 单例缺陷的完整诉求（可实例化隔离 Runner、多线程并发驱动）仍需大改，本修复是其渐进里程碑；
对应的上游 issue 由维护者按团队决议管理，本 PR 不请求关闭或重开。
