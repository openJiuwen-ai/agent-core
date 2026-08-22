# 临时 Reviewer 缺票时 Fail Closed

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-22 |
| 范围 | `openjiuwen/agent_teams/agent/scheduling/`、reviewer prompts、`i18n.py` |
| 测试基线 | `tests/unit_tests/agent_teams/agent/test_team_scheduler.py`，24 passed |
| Refs | Scheduled dispatch reviewer protocol |

## 背景

Scheduled 团队的临时 reviewer 可能正常结束一次模型调用，却没有执行 `verify_task`。旧调度器只记
“本轮已经派发”，因此没有新的 DB 事件可以唤醒它，任务会永久停在 `in_review`。把 reviewer 的自然语言
总结当成通过票会破坏 DB 票据权威；无限重启 reviewer 又会形成无界模型调用。

## 决策

1. 调度器以 `(task_id, review_round, reviewer)` 跟踪实际 reviewer job 与尝试次数，而不是只记整轮已派发。
2. 每次临时 reviewer 结束后重新读取当前任务和票表；只有持久化票据才算完成协议。
3. 缺票或执行异常最多重试一次。两次仍无票则通知 leader，并保持任务 `in_review`，不伪造 pass/fail。
4. reviewer job 退出本身触发一次幂等扫描，因此“无票所以没有 DB 事件”不再造成永久静默。
5. scheduler pause/stop 会取消 reviewer jobs；活跃 scheduler 内部的意外取消仍按一次执行失败重试。
6. 临时 reviewer 不继承 author 的 nested subagents，避免 bounded review 被再次委派并在清理阶段提前取消。
7. 中英文 reviewer prompt 都明确要求把 `verify_task` 作为最后一个工具调用。

## 拒绝的方案

- **解析 reviewer 最终文本并合成票据**：文本不是数据库事实，且会绕过 reviewer 身份与轮次守卫。
- **只依赖 review stall timeout**：它会在较长时间后提醒 leader，却不能恢复一次已结束且无任何新事件的 reviewer。
- **无限自动重试**：错误 prompt、工具不可用或模型不服从时会形成无界 token 消耗。
- **按 review round 单独记 dispatched**：无法区分已投票 reviewer、缺票 reviewer 与同轮被替换的 reviewer。

## 验证

- 缺票两次：任务保持 `in_review`，只产生一次协议故障升级。
- 首次缺票、第二次投票：立即按 DB 票据结算。
- 活跃状态下 reviewer job 被取消：允许有界重试。
- scheduler deactivate：取消 job，且不会在暂停后后台重启 reviewer。
- 多 reviewer：只重试尚未持久化票据的成员。
- reviewer 被替换或任务轮次变化：旧 job 结果视为 superseded，不污染新状态。

## 已知遗留

尝试次数仍是进程内状态；leader 进程重启后最多额外派发一次 reviewer。任务状态、轮次和票据仍以 DB 为
唯一真相，因此不会由此伪造结算结果。若未来需要跨进程严格限制调用次数，应把 reviewer attempt 作为独立
持久化调度事实，而不是塞进投票表。
