---
id: B_20261116_2246_subworkflow-stream-batch-mode
title: 子工作流默认批式配置被流式边调用时 _sub_stream 必崩的缩进缺陷
type: B
author: CharlesWang2030
author_email: 93426525@qq.com
modules:
  - openjiuwen/core/workflow
files:
  - openjiuwen/core/workflow/workflow.py
  - tests/unit_tests/core/component/test_sub_workflow_comp.py
designs: []
design_note: 常规缺陷修复，未归档独立设计；修复方案在 CORE-02 问题分析归档中确认（缩进修正 + 批式回退），沿用 _sub_invoke 既有批式返回契约，无新增公开 API。
---

# 子工作流默认批式配置被流式边调用时必崩缺陷修复记录

## 现象与影响

父工作流通过流式连线（`add_stream_connection`）调用子工作流组件，而子工作流
`set_end_comp` 保持默认批式响应（未设 `response_mode="streaming"`，`_is_streaming=False`）时，
该组合 100% 失败，抛出
`ExecutionError [100054] ... cannot access free variable 'sub_end_ability'`
（根因 `NameError: cannot access free variable 'sub_end_ability' where it is not associated
with a value in enclosing scope`）。错误信息指向内部变量名，用户无法理解。

最小复现：`E:/workspace/2026/问题和需求/20260919 jiuwenswarm develop代码分析/CORE-02/core02_repro.py`
（基于官方单测 `test_workflow` 构造，唯一差异是子工作流 end 组件去掉 `response_mode="streaming"`）。
修复前实测（2026-11-16，develop）：对照组（流式子流）成功 16 帧；复现组崩溃并抛出上述错误。

## 关联设计与当前契约

无独立设计。当前契约：`Workflow._sub_stream` 是子工作流被父图流式边调用时的流式执行路径，
`_is_streaming=True` 时逐帧转发子流帧；批式子流的批式输出返回契约与 `_sub_invoke` 回退路径一致
（`NodeSession(...).state().get_outputs(self._end_comp_id)`）。本修复不新增公开 API，不改变
流式路径行为。

## 根因与修复决策

根因：`openjiuwen/core/workflow/workflow.py` 的 `_sub_stream` 缩进错误——`sub_end_ability`
与 `stream_timeout` 仅在 `if self._is_streaming:` 内赋值，`required_abilities` /
`stream_ability_count` / `while` 循环却写在 `if` 外并引用上述变量；子工作流为默认批式时
触发 `UnboundLocalError`。同文件 `_sub_invoke`（缩进正确的参照实现）证实了正确的结构。

修复决策（最小改动）：
1. `required_abilities` / `stream_ability_count` / `while` 整体缩进进 `if self._is_streaming:`；
2. 新增 `else` 分支：批式子流 yield 一次结束组件批式输出，语义与 `_sub_invoke` 回退一致；
3. 删除只累加不使用的残留变量 `frame_count`。

考虑过的备选：在 `else` 分支把批式输出拆成逐帧流式形状（使父图流式边行为与流式子流一致）。
拒绝原因：需要依赖 End 组件 collect 输出的具体形状做形状分支，超出本次崩溃修复范围，
且未与批式输出过流式模板的既有语义对齐，登记为遗留待确认。

## 回归证据与适用范围

- 候选版本：`fix/subworkflow-stream-batch-mode` 分支（基于 `develop@1071f4564`），2026-11-16。
- 复现脚本修复后实测：对照组成功 16 帧（不变）；复现组成功完成（14 帧），不再抛错。
- 目标单测：`tests/unit_tests/core/component/test_sub_workflow_comp.py` 6 passed
  （含新增回归用例 `test_workflow_with_batch_sub_workflow_on_stream_edge`，
  以 `RecordingSubWorkflowComp` 记录帧，断言批式子流输出按单帧交付且不抛错）。
- 回归范围：`tests/unit_tests/core/workflow` + `tests/unit_tests/core/component`
  228 passed, 14 skipped，无回归。
- 未验证范围：真实 LLM 组件子流、模板型 End 组件（`response_template`）组合未覆盖；
  重验条件为上述构造的最小复现与目标单测。

## 防错模式与资料同步

- 值得复用的模式：流式循环内引用的变量必须与流式守卫（`if self._is_streaming:`）同层赋值；
  从批式方法复制流式逻辑时需整体核对缩进层次。已通过本 bugfix 记录沉淀，未新增全仓禁令。
- `frame_count` 类"只累加不使用"残留会掩盖结构错误，修复时一并清理。
- 已同步：CORE-02 分析归档（`E:/workspace/.../CORE-02/` 的 issue.md / PR.md）已更新修复分支、
  改动方案与验证证据；本仓库 bugfix 索引由 `sync_documents.py` 刷新。

## 遗留与后续

- 批式子流经父图流式边交付的单帧形状为 `{'output': [chunks...]}`（list），父图 end 节点按帧
  模板路径（如 `${sub_comp.output.out}`）取不到值，该组合"能跑完但不产出 OUTPUT 帧"。
  属批式输出过流式模板的既有形状语义，非本次引入。若需要"批式子流在流式边上表现得与
  流式子流一致（逐帧拆分）"，需另行评估并确认后实施（PR.md 待确认决策点已登记）。
- `_sub_stream` 的 `while` 循环中 `receive` 持续返回 `None` 时存在理论上的无限循环风险，
  属既有行为，未在本次处理。
