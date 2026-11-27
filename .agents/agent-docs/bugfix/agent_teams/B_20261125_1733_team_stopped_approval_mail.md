---
id: B_20261125_1733_team_stopped_approval_mail
title: 停止成员的审批回执阻塞持久化 Team 完成
type: B
author: "MaxMadMax"
author_email: "chengkunck@gmail.com"
modules: [agent_teams, core.extension]
files:
  - openjiuwen/agent_teams/agent/recovery_manager.py
  - openjiuwen/agent_teams/agent/spawn_manager.py
  - openjiuwen/agent_teams/harness/team_harness.py
  - tests/unit_tests/agent_teams/test_stopped_approval_recovery.py
  - openjiuwen/agent_teams/group_chat/message_handler.py
  - openjiuwen/agent_teams/agent/coordination/kernel.py
  - tests/unit_tests/agent_teams/test_group_chat_integration.py
  - tests/unit_tests/agent_teams/test_coordination_lifecycle.py
  - openjiuwen/agent_teams/tools/database/__init__.py
  - openjiuwen/agent_teams/tools/database/member_dao.py
  - openjiuwen/agent_teams/tools/database/message_dao.py
  - tests/unit_tests/core/extension/test_install_report.py
designs: [D-20261125-1414-team-stopped-approval-recovery, D-20261126-1704-team-lifecycle-boundaries]
---

# 停止成员的审批回执阻塞持久化 Team 完成

PR !3893 基线 `a97f6dfd6` 中，停止成员没有可读取中断的 runtime，审批回执持续未读，阻塞持久化 Team 完成。修复 `2a42d3202` 让收件人在原 checkpoint 上判断回执是否仍有效。独立代码复审通过，启动接管与取消语义见 S_03。

回归提交 `66471dba9` 使用 SQLite、Native/ReAct 和 mock 模型。新增 10 项通过，覆盖失效消费、有效审批只执行一次、缺少凭据或无权回执、装载失败和停机竞争。关联回归 474 项通过、19 项跳过。群聊清表用例的顺序依赖在基线和候选都能复现，单跑均通过。

## 2026-11-26：本 PR 的 UT 后续修复

审批 CAS 所需的 memory SQLite 读锁触发了既有群聊通知取消竞争：取消 get_message 会使 SQLAlchemy 关闭唯一内存连接；未等待通知退出又与 dispose 并发，导致关库挂起和后续路径测试污染。仅给 close 加锁仍会丢失成员表，因此不采用。保留读写互斥，仅在通知 owner 内保护完整查询并延迟传播取消，pause/stop 等待通知排空后释放会话。契约见 S_28。

候选为 b8948bf13 上的未提交修复。审批、CAS、数据库、群聊及生命周期定向回归 684 项通过、20 项跳过；原报告同序链扩大到 53 项通过。另两项 PostgreSQL FakeEngine 和已注册 session 表断言失败在修改前 HEAD 与 develop 46014823e 均复现，保持原样。新增事件屏障覆盖锁等待、cursor 边界、重复取消、并发 drain、通知超时交叠、查询失败和会话释放顺序。全量 CI 尚未验证。

## 2026-11-26：通知重入与恢复资格竞态收尾

按 D-20261126-1704-team-lifecycle-boundaries 实施：通知关闭只排空框架消息读取，当前回调请求停止时跳过自身取消，其余回调维持取消策略。审批接管在同一 write/session 内先占用 STOPPED 成员，再复核预先验证的审批 ID 集合及既有广播、群提及、群通知未读；资格失效则 rollback，不创建 runtime。事务结果确认承受重复取消；仅明确未接管且仍由当前 task 持有的预约可释放，实际启动和异常保留本轮尝试记录。接管后的实例继续正常收件。

验证候选为 399177342 上的本地修复，Python 3.11、真实 SQLite、Native Harness 与 mock 模型。通知定向 10 项、审批恢复定向 28 项通过；关联回归 700 项通过、20 项跳过，仅排除此前 HEAD/develop 已复现的两项基线失败；原失败顺序链 53 项通过。独立代码复审 Approve，无阻塞项。未运行静态工具、完整 CI 或 PostgreSQL/MySQL 集成；验证时尚未提交或推送。整个 stop 的不可取消保证不在本次范围；原有条件清理 await 的取消边界保持。

## 2026-11-26：门禁协作方法命名修复

最新门禁评论 204213348 的静态报告完整返回 3 条未解决问题，均为本 PR 新增协作方法的跨类 protected-access。将 TeamDatabase.claim_approval_restart、MemberDao.claim_restart_in_session 和 MessageDao.approval_mail_matches 改为无前导下划线的协作接口，并同步调用及测试引用；事务、取消和审批判断逻辑不变，S_03/S_28 的行为契约无需调整。

候选为 f2ef6588d 上的本地修复，Python 3.11、真实 SQLite 与 mock 模型。审批恢复、数据库并发和会话 watchdog 三个测试文件共 73 项通过。未运行本地静态工具；最新远端报告的 UT、开源合规和 Ruff 为 SUCCESS，新候选的远端静态检查仍待推送后确认。

## 2026-11-26：基线 Extension UT 门禁补充

门禁评论 204222668 的完整报告对应 58e432f71，29,621 passed、493 skipped、2 xfailed、1 failed。唯一失败是 test_cross_domain_wait_and_reverse_dependency_protection 中 b 成功后 a 返回 FAILED。相关测试及生产代码与本地 develop 46014823e 一致。报告未包含错误码；在 b 启动前注入 1.2 秒延迟可复现 EXTENSION_DEPENDENCY_TIMEOUT，支持成功场景的 1 秒预算过短这一解释，不能据此确认 CI 的具体错误码。

按本次 UT 修复要求，53767359e 仅将该成功场景的 a 等待预算调为 5 秒，与相邻用例一致，并在失败断言显示 error；生产默认 300 秒和专门的 0.05 秒超时测试不变。Python 3.11、真实文件报告和 Registry：无注入时修复前后 test_install_report.py 均为 34 项通过；同一 1.2 秒延迟下原测试失败，修复后通过。最小差异复查完成，未运行本地静态工具或完整 CI。

PR 重开后，53767359e 的流水线 cb0d6487f2884dd5a90499e37a34d400 在 check_pr_size 失败：51/50 个文件、2434/6000 行。后续 UT 等阶段未执行，评论 204234768 引用的仍为 58e432f71 旧附件，SHA256 与上一报告相同。将独立 B_20261126_1900_extension-dependency-wait-ut 的核心证据并入本记录以恢复 50 文件边界；该独立记录由本节替代。新候选的实际 CI 结果仍待重跑。

## 2026-11-27：READY 结果等待的重复取消复现

只读候选 fd80a84651467039741d6526f7d3c66dcab919cb。针对评论 204312911，在仓库外临时复用 stopped_worker、原生恢复入口、真实文件 SQLite 与 mock 模型：事件屏障停在 RESTARTING → READY 提交之后、DAO 返回之前；调用 leader.stop_coordination，并用单独 task 的 wait_for 在 0.1 秒后取消停止操作，观察到成员 task 已收到两次取消后才释放屏障。复现测试按正确行为断言 STOPPED，在当前代码上失败（1 failed，3.48 秒）：member_status=ready、ready_written=false、ready_claim_cancelled=true、runtime_alive=false、registered_handles=[]、unread=1、tool_calls=0。

临时脚本和 observed.json、repro.log 位于 /private/tmp/core-3893-ready-cancel-plan-20261127/，未改产品或仓内测试。根因是 kernel 首次 shield 后的裸 await claim 被第二次取消穿透；最小补充方案续写 D-20261125-1414-team-stopped-approval-recovery。本次仅复现及方案评审，尚无修复后通过证据，未提交或推送。

## 2026-11-27：READY 重复取消的最小修复

按已评审补充方案，在 kernel._complete_approval_startup 内循环 shield 同一个 READY claim，终结后同步读取结果、记录 ready_written，再传播已收到的取消。CAS false、普通异常和正常启动继续沿原分支；不修改外层 stop、owner 的条件回滚或数据库逻辑。S_03 补充结果确认窗口及等待预算的边界。生产差异为 14 行新增、6 行删除；测试差异为 75 行新增、5 行删除，优先参数化原真实停止用例，没有新增文件或设计偏差。

候选为 fd80a8465 上的本地未提交修复。Python 3.11、真实文件/内存 SQLite、Native Harness 与 mock 模型：新增回归在生产修改前为 5 failed、5 passed（另 27 deselected）；修复后审批恢复文件 37 passed。原始仓外复现及 coordination_lifecycle、database_concurrency、db_sessions_watchdog 共 74 passed；原始复现观察到 STOPPED、ready_written=true、ready_claim_cancelled=false、runtime_alive=false、handles=[]、审批未读 1 条、工具执行 0 次。测试覆盖真实 stop 超时、三次取消、无取消的 false/异常，以及取消与 DAO 失败并发；false 用例还确认并发 SHUTDOWN 不被条件回滚覆盖。

code-quality-check 已按候选完成实现与范围核对；架构升级 Not triggered：只调整现有等待机制，写入与清理归属、依赖和接口不变。遵照仓库要求未运行本地静态工具；尚未运行新候选远端 CI 或其它数据库集成。结果确认后的其它清理点再次取消仍不在本补充范围，确认结果可能使停止等待超过外层预算。2026-11-27 独立代码复审由 independent-reviewer（任务 ready_result_plan_review）完成，结论 Approve，无阻塞发现、无必须修订项；覆盖本地六文件差异、真实取消/清理链和验证证据。未提交或推送。证据目录 /private/tmp/core-3893-ready-cancel-impl-20261127/。

## 2026-11-27：按用户要求精简测试

将 READY 成功/false/异常与取消次数合并为一个参数化集成用例，共用屏障、真实停止链、清理及最终状态断言。相对 fd80a8465，测试变更从 75 增 5 删（80 行、净增 70）收敛为 47 增 12 删（59 行、净增 35）。保留已提交 READY 下停止超时和三次取消、无取消的 false/异常、DAO 失败与二次取消并发及 SHUTDOWN 不被覆盖；所有分支在文件/内存 SQLite 验证。生产代码、S_03 及清理边界未改变。

精简候选审批恢复文件 41 passed（11.40 秒）；临时在进程内替换为 HEAD 的旧 READY 等待函数后，同一参数化测试为 8 failed、6 passed、27 deselected（3.49 秒），确认仍检出取消穿透。该对照只替换目标函数，不代表完整旧分支回放。原生产改动未变，上轮 74 项关联回归证据继续适用，本轮未重复执行。2026-11-27 同一 independent-reviewer 对精简候选独立复查，结论 Approve，确认全部参数组合有效、原缺陷检测力和关键清理证据保留，无必须修改项。未提交或推送。证据目录 /private/tmp/core-3893-ready-tests-compact-20261127/。
