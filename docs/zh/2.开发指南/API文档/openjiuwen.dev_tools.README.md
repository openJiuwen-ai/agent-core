# openjiuwen.dev_tools

`openjiuwen.dev_tools` 提供了 openJiuwen 的**开发工具（DevTools）**模块，面向“研发/调试/迭代”场景，主要覆盖两类能力：
- **提示词构建与优化（prompt_builder）**：
  - 用于把“任务说明/需求/反馈/坏例”转化为更稳健的提示词模板。
  - 适合在提示词工程阶段快速收敛质量，减少手工反复试错。
- **Agent 调优（tune）**：
  - 用于把 `Optimizer + Evaluator + Trainer` 组合成一套可重复的训练流程。
  - 适合在有用例集（Case 数据集）时，自动化评估与迭代提示词参数（system/user prompt 等）。
- **Agent 强化学习训练（agentrl）**：
  - 基于 verl 的 RL 训练扩展，支持多轮 rollout 编排、并行执行和结构化轨迹收集。
  - 提供高级用户入口 `RLOptimizer`，简化 RL 训练流水线的配置和运行。
- **Agent 构建（agent_builder）**：
  - 用于从自然语言描述构建 LLM Agent 与工作流 Agent（澄清、设计、DL/DSL 转换等）。
  - 模块说明见 [agent_builder](./openjiuwen.dev_tools/agent_builder.README.md)；包级 API 见 [agent_builder.md](./openjiuwen.dev_tools/agent_builder/agent_builder.md)。GitBook 目录见 [SUMMARY.md](../../SUMMARY.md) 中 `openjiuwen.dev_tools` → `agent_builder`。

> 说明：`dev_tools` 面向"开发迭代"，不建议在生产主链路中无控制地调用（可能带来额外 LLM 费用与耗时）。
>
> 生产侧通常使用已固化后的 prompt/agent 配置，调优流程建议离线或在受控环境中运行。

**Modules**：

| MODULE | DESCRIPTION |
|--------|-------------|
| [prompt_builder](./openjiuwen.dev_tools/prompt_builder.README.md) | 提示词构建和优化工具。 |
| [tune](./openjiuwen.dev_tools/tune.README.md) | Agent调优工具。 |
| [agentrl](./openjiuwen.dev_tools/agentrl.README.md) | 基于 verl 的强化学习训练扩展。 |
| [agent_builder](./openjiuwen.dev_tools/agent_builder.README.md) | Agent 构建（NL→LLM Agent / 工作流）开发工具。 |
