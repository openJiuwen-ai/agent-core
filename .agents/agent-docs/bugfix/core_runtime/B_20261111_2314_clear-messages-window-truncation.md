---
id: B_20261111_2314_clear-messages-window-truncation
title: clear_messages 因窗口截断无法清空底层缓冲导致旧消息重回模型上下文
type: B
author: CharlesWang2030
author_email: 93426525@qq.com
modules:
  - openjiuwen/core/context_engine
files:
  - openjiuwen/core/context_engine/context/message_buffer.py
  - openjiuwen/core/context_engine/context/context.py
  - tests/unit_tests/core/context_engine/test_context_model.py
designs: []
design_note: 契约违背的常规缺陷修复，未归档独立设计；清空语义以 context_engine/base.py 抽象方法 docstring 为唯一契约依据，修复方向为使实现回归既有契约，无新增契约或方案取舍。
---

# clear_messages 因窗口截断无法清空底层缓冲导致旧消息重回模型上下文修复记录

## 现象与影响

任何调用 `Context.clear_messages()` 的路径（用户显式「清空对话」、NativeHarness 无快照回滚等）执行后，
当底层缓冲消息数 `L > max_context_message_num`（M）时，最早的 `L − M` 条消息残留，
下一轮重新进入模型上下文。"清除敏感对话"与"异常回滚恢复"场景下构成数据残留；
方向自反——旧实现清掉的是最新 M 条、留下最旧的 L−M 条，与"清空"语义及窗口"保留最新"的设计方向均矛盾。

触发条件（四项同时成立）：配置了 `max_context_message_num`；清空时 `L > M`；发生一次 `clear_messages` 调用；
清空后再发一轮消息。所有 `L > M` 均触发，不限 `M < L ≤ 2M`：增量对话稳态 `L ∈ [M+1, 2M]` 必然暴露；
批量 `add_back`（`_if_need_resize` 单次裁剪非循环，实测 M=5 批量加 20 条 → L=15）与 `set_messages`
（不触发 resize，实测 set 30 条 → L=30）可达 `L > 2M` 且残留更多。

实际暴露面：NativeHarness 无快照回滚（`agent_teams/harness/native_harness.py:1749-1758`
`_rollback_to_snapshot` 兜底自动调用 `clear_context_messages`，workswarm team/swarm 成员运行其上，
且任务越复杂越易回滚、消息越易超限，两场景高度重合）；legacy agent（`max_rounds*2` 必设）；
mobile GUI 子代理（默认 120）。workswarm 主链路当前未配置窗口限制且无清空入口，属潜伏缺陷。

## 根因

- `Context.clear_messages()`（基线 `context.py:617-618`）调用 `pop_messages(len(self), with_history)`，
  意图"清空全部"；但 `len(self)` → `__len__` → `ContextMessageBuffer.size()` 返回
  `min(len(底层), max_buffer_size)`，是被可见窗口截断的长度。
- `pop_back` 用 `get_back` 返回的可见窗口长度（`popped_size ≤ M`）去裁底层列表，
  `L > M` 时窗口外最旧的 `L − M` 条永远裁不掉。
- 契约依据：`context_engine/base.py:114-126` 抽象方法明确 `with_history=True` 全清、
  `with_history=False` 仅保留持久 history 前缀；`with_history=False` 的设计目的是
  "保历史、丢本轮"（回滚依赖），旧实现连唯一职责都未达成。
- 现有单测长期未暴露：clear 用例均未设 `max_context_message_num`，M=None 时
  `size()` 返回底层真实长度，行为正确。

## 修复决策

新增 `ContextMessageBuffer.clear(with_history)` 真正清空底层缓冲（不受可见窗口约束），
`SessionModelContext.clear_messages` 改用它，并把原 `pop_messages` 内部的
`invalidate_usage_metadata` / `_mark_message_changed("remove")` 平移到 `clear_messages`，
保证缓存失效与 revision 语义不变。`pop_back` / 普通 `pop_messages(size)` 未改动。
选 `clear()` 而非窗口内打补丁（如循环裁剪）的原因：清空语义与窗口无关，独立方法语义清晰、
易验证，且对任意 `L`（含 `L > 2M`）行为一致。

已知遗留（随本次提交修正）：`clear_messages` 内沿自基线的注释
"pop_messages owns the revision increment" 在修复后不再准确（revision 由自身 `_mark_message_changed` 递增）。

## 验证证据（候选：工作区 fix/CORE-01-clear-messages 未提交，基线 develop 15e8c1413）

- **复现**（`repro_core01.py`，buffer 级）：M=5、add 8，基线路径 `pop_back(size())` 清空后残留
  `[m0, m1, m2]` —— CONFIRMED；
- **修复验证**（`verify_core01.py`，buffer 级 5 项）：无 history `clear(True/False)` 全清、
  带 history=2 时 `clear(False)` 保留 `[h0,h1]` / `clear(True)` 全清、旧路径残留对照 —— ALL PASS；
- **触发对照演示**（`trigger_core01.py`）：M=50、单轮 60 条，旧路径残留 10 条、修复后全清，
  截图 `CORE-01_触发结果.png`（存放于分析目录 E:\workspace\2026\问题和需求\20260919 jiuwenswarm develop代码分析\CORE-01\）；
- **真实入口 E2E**：`ContextEngine` 构造 `max_context_message_num=5`，`clear_messages(False/True)`
  后 `len(context)==0`、`get_messages()==[]` —— PASS；
- **回归测试**：`test_context_model.py` 新增 2 个窗口场景用例
  （`test_clear_messages_clears_full_buffer_beyond_window`、`test_clear_messages_keeps_history_beyond_window`），
  连同既有 54 个共 **56 passed**（2026-11-11，uv 环境实跑）。新增用例在基线上预期失败
  （残留 m-0..m-2），在候选上通过，构成基线→候选行为证据闭环；
- 未执行项：develop 基线端到端实跑未做（避免改动用户工作区，以静态 diff + 复刻旧路径的
  repro 脚本佐证）；workswarm 下游集成未在本仓验证，`clear_messages` 为唯一清空入口，
  下游语义只会改善。

## 提交组织

用户要求测试与修复同一提交：`fix(context): 修复清空上下文在消息数超过窗口限制时无法清干净的问题`，
内容为 `message_buffer.py` + `context.py` + `test_context_model.py`；本 bugfix 文档与索引同步
可并入该提交或作为 docs 跟随提交。issue/PR 草稿见
`E:\workspace\2026\问题和需求\20260919 jiuwenswarm develop代码分析\CORE-01\`（2026-11-11 修订版，
触发区间与影响面已按本记录修正）。
