# Goal 阻塞审计（Blocked Audit）开发者指南

> 本文面向维护者/使用者：说明 goal 的阻塞审计机制是什么、为什么、怎么用、怎么验证。
> 功能代码入口：`openjiuwen/harness/goal/`（`manager.py`、`schema.py`、`evaluation.py`、
> `prompts/sections/goal.py`、`rails/task_completion_rail.py`、`tools/goal.py`）。

## 一、它解决什么问题

goal 模式下，agent 遇到第一个障碍就 `submit_goal_report(status=blocked)`，评估器确认后
goal 立即 **BLOCKED 收工** —— 即使换个思路本可以继续。结果：**agent 过早放弃**。

对齐 Codex 原版 `continuation.md` 的 Blocked audit（"the same blocking condition repeated
N consecutive turns"），本项目实现：**相同阻塞必须连续出现 N 次（默认 3）才确认 BLOCKED**，
前 N-1 次强制降级为 continue，让 agent 继续尝试替代方案。

## 二、机制与分工

**机制层（确定性、可单测）** —— `GoalManager.apply_assessment`：

- 收到 `status=blocked` 的评估后，结合 assessor 的 `blocking_same_as_previous` 信号刷新
  `GoalRecord.blocking_history`（evidence 字符串列表）：
  - **相同**（`blocking_same_as_previous is not False`，含 assessor 未给信号的保守情况）→ 追加，计数 +1；
  - **不同**（`blocking_same_as_previous is False`）→ 重置为当前一条，计数 = 1；
  - 非阻塞评估（COMPLETE / CONTINUE / PAUSED）→ 清空 `blocking_history`（重新审计）。
- `len(blocking_history) >= blocked_threshold`（默认 3）→ **最终 BLOCKED**；
- 未达阈值 → goal 保持 **ACTIVE**，本次评估**降级为 CONTINUE**，`next_instruction` 提示
  "try an alternative approach or obtain the missing input"。
- **resume**（从 BLOCKED / PAUSED 恢复）时清空 `blocking_history` —— 对齐 Codex
  "resumed run as fresh blocked audit"：一次新 run 重新起计数。

**assessor 层（语义判断）**：评估 prompt 注入 `<blocking_history>` 块，assessor 判断当前
阻塞与历史是否**同一根本原因**，输出布尔 `blocking_same_as_previous`。机制层只做确定性计数，
不做文本相似度比对。

## 三、数据模型

```python
@dataclass
class GoalAssessment:
    status: GoalAssessmentStatus
    evidence: str
    ...
    blocking_same_as_previous: Optional[bool] = None   # 仅 status=blocked 时由 assessor 填

@dataclass
class GoalRecord:
    ...
    blocking_history: list[str] = field(default_factory=list)  # 连续"相同阻塞"的累计
```

- `GoalAssessment.from_dict` 只接受真正的 `bool`（非布尔 → None，按"相同"保守处理）。
- `GoalRecord` 序列化保留 `blocking_history`；旧持久化数据无该字段 → 空列表（兼容）。

## 四、配置

- `blocked_threshold`：`GoalManager.__init__` 参数，默认 `3`，host/测试可覆盖。

## 五、如何观察运行行为

goal logger（log_type=`goal`）输出关键链路日志：

```
[GoalLifecycle] apply assessment: status=active  goal=<id> blocking_history_len=1 blocked_threshold=3
[GoalLifecycle] apply assessment: status=blocked goal=<id> blocking_history_len=3 blocked_threshold=3
```

判断标准：前 N-1 次 blocked 后仍是 `status=active` 且 `blocking_history_len` 递增；
第 N 次同根因才 `status=blocked`；不同根因时 `blocking_history_len` 回落到 1。

## 六、如何验证

**单元测试（核心验证，CI 内）**：

```bash
uv run pytest tests/unit_tests/harness/goal/test_goal_manager.py \
  tests/unit_tests/harness/goal/test_goal_schema.py \
  tests/unit_tests/harness/goal/test_goal_evaluation.py \
  tests/unit_tests/harness/goal/test_goal_prompts.py \
  tests/unit_tests/harness/test_task_completion_extensions.py -q
```

覆盖：连续 3 次相同阻塞才 BLOCKED / 不同阻塞重置 / 未达阈值降级 continue /
COMPLETE·CONTINUE·PAUSED·resume 清历史 / 字段序列化 / JSON 解析 / 中断 finalize。

**端到端脚本（真实模型，可选）**：

```bash
# 需要 ~/.openjiuwen/settings.json 配好模型
cd <agent-core>
uv run python examples/harness/goal_blocked_audit_verify.py
```

脚本构造"读取不存在的 CSV 并产出报告"的注定阻塞目标，驱动模型连续 blocked，
观察 `blocking_history_len` 1→2→3 后 goal 才落定 BLOCKED。

## 七、边界与已知取舍

- **"相同阻塞"语义**交给 assessor；机制层只读布尔信号做 +1/重置。assessor 未给信号时
  按"相同"追加（避免误重置导致计数永远到不了阈值）。
- **连续计数 vs 总次数**：统计的是"当前这串连续相同阻塞"的长度；"无token→磁盘满→无token"
  （3 次但非连续相同）不会误判 BLOCKED。
- **真实持续阻塞**仍应报 `status=blocked`（供机制计数），但不应因任务难、慢、不确定而报。
- 权限阻塞（如 `bash: ask` 弹窗）与阻塞审计正交，属权限配置问题。