# RSI Epoch 结果复用

启用完整 H0 基线评测的运行现在按以下顺序执行：

1. 完整 H0 评测，作为不变的基线分数。
2. Epoch 1 按 Batch 读取 H0 中可复用的失败轨迹，分析并生成候选。
3. 候选仍须实际复测，Epoch 结束仍须完整评测。
4. 下一 Epoch 优先读取最近一次匹配当前 Harness 的完整评测。

复用同时核对 Harness 包内容、任务模型配置及引用的 Judge 配置、评测配置、逐题输入和相关运行环境。前一 Batch 更换 Harness 后，没有该版本结果的题必须补评。基础设施失败、缺失或损坏的结果/轨迹不作为可复用证据。旧结果若没有匹配依据，不会被猜测为可复用。

Analyzer 收到独立的 Batch 证据目录及重新汇总的 Batch 分数，不会混入完整评测中的其他题。完整原始证据保留，未进行额外压缩。

## 事件上报

继续使用原有 `node.stage`，不新增对外接口：

- `stage.id=source.reuse`：当前 Batch 已复用证据，`status=done`。
- `batch_index`、`total_cases`、`reused_case_count`、`evaluated_case_count`：本 Batch 范围及复用/补评数量。
- `score`：当前 Batch 分数，不是全局基线或 Epoch 分数。
- `eval_ref_path`、`evaluations`：Batch 证据及其原始评测路径。
- `reused_case_ids`、`evaluated_case_ids`：逐题来源。

真实补评继续上报原来的 `evaluate.case.*`。复用不伪造这些事件，不增加模型调用或 Token。`EventProgress.iteration` 仍只计完成的 Epoch，不计 Batch 或复用次数。最终 Epoch 节点的 `extra.source_evidence` 和运行状态保存各 Batch 的来源，便于刷新页面后追溯。

已完成阶段的恢复不会再次计费。若恢复时发现已执行阶段的模型或 Harness 被外部修改，会拒绝混用旧分析并要求新建运行；不会删除或覆盖旧结果。此次改动不更改原生 `load_plugin` 或候选接受规则。

## 本地验证

2026-09-08：引擎控制器、复用边界、事件与用量测试 116 项通过；前端展示/树进度测试 11 项通过，TypeScript 检查通过。服务 RSI 测试在 asyncio auto 模式下 178 项通过，排除 1 个已有的临时目录清理测试：它模拟 `shutil.rmtree` 抛出 `PermissionError`，同时导致测试自己的目录清理失败，与本次改动无关。

另外使用已完成的真实 SWE 运行产物做离线验证：从 5 题完整评测中构造 2 题 Batch，用实际 `CaseReader` 读取，题目、响应、分数、评分证据和测试契约均保持一致，未混入另外 3 题。没有新增模型请求，未重启或中断正在运行的服务。
