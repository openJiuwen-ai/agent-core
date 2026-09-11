# Agent Evolving

Agent 自演进的训练、评估、在线经验与强化学习子系统。

本文件是模块入口。深入修改时继续读取：

- `trajectory/AGENTS.md` — canonical OTLP 轨迹、capture、消息重建与归档
- `experience/AGENTS.md` — Skill 经验生成、审批、提交、消费反馈与治理
- `checkpointing/AGENTS.md` — Trainer checkpoint 与 Skill EvolutionStore 持久化
- `agent_rl/AGENTS.md` — 离线 / 在线 RL 与可选依赖边界
- `agent_rl/online/AGENTS.md` — 在线采集、Gateway、judge、训练调度与服务生命周期

不同链路都使用 `evolution` 一词，但不共享同一种数据模型、生命周期或持久化语义。

## 公开入口（public API）

`openjiuwen.agent_evolving.__init__` 是顶层公开表面；子包 `__init__.py` 是各子域公开表面。
新增、移动或删除导出时同步检查 API 文档、示例和兼容性提示。

| 入口 | 用途 |
|---|---|
| `Trainer` | 编排预测、评估、更新、候选验证和 checkpoint |
| `BaseEvaluator` / `DefaultEvaluator` / `MetricEvaluator` | 将执行结果转换为评估结果和分数 |
| `BaseOptimizer` / `InstructionOptimizer` / `SkillExperienceOptimizer` | 生成参数或经验更新 |
| `Updater` / `SingleDimUpdater` / `MultiDimUpdater` | 将轨迹和信号路由到 optimizer |
| `Trajectory` | 唯一规范的执行轨迹值对象 |
| `EvolveCheckpoint` | 可恢复的训练状态模型 |
| `FileCheckpointStore` | checkpoint JSON 文件存储 |
| `CheckpointManager` / `DefaultCheckpointManager` | checkpoint 保存策略、构造与恢复编排 |
| `RLConfig` | 离线强化学习配置 |
| `OfflineRLOptimizer` / `OnlineRLOptimizer` | 离线与在线强化学习高层入口 |

`agent_rl` 的高成本可选依赖通过 `__getattr__` 延迟导入。不要在包级提前导入 Ray、verl、
FastAPI、Redis 或训练 backend，避免普通 `import openjiuwen.agent_evolving` 要求完整 RL 环境。

## 模块地图

| 路径 | 职责 |
|---|---|
| `dataset/` | `Case`、`EvaluatedCase` 与数据加载 |
| `evaluator/` | evaluator、metric 与评估流水线 |
| `optimizer/` | llm、tool、memory、skill 候选更新生成 |
| `updater/` | 单维、多维更新路由与编排 |
| `trainer/` | evaluate → update → validate 训练循环 |
| `trajectory/` | canonical OTLP 轨迹、capture 与归档 |
| `signal/` | 对话、评估、review、team 事实到 `EvolutionSignal` 的转换 |
| `experience/` | Skill 经验生成结果的审批、提交、评分与治理生命周期 |
| `checkpointing/` | Trainer checkpoint 与 Skill `EvolutionStore` 持久化 |
| `tools/` | 主 Agent 与受限 review Agent 的演进工具适配 |
| `prompts/` | 演进协议 section、Skill 创建指导与工具 metadata |
| `sharing/` | Skill 经验及 Skill 包的跨用户检索、暂存与传输 |
| `agent_rl/` | 离线 / 在线强化学习扩展 |

## 三条主链路

### 经典参数优化

`CaseLoader → Trainer predict/evaluate → Trajectory → Updater → Operator candidate → validation → checkpoint`

- `Trainer` 通过 `agent.get_operators()` 获取可优化对象；不要猜测 Agent 内部结构。
- 多个候选从同一份 operator snapshot 出发独立验证，最后恢复最佳候选，不能相互污染。
- `requires_forward_data() == False` 的 optimizer 可以跳过无意义的训练集前向。
- 新增可恢复状态时，checkpoint save/restore 必须成对修改并覆盖 resume。

### 在线 Skill 经验演进

`Trajectory/review/evaluation → EvolutionSignal → updater/optimizer → local preview → approval → EvolutionStore`

- Signal 只表达演进原因和目标；optimizer/updater 只生成候选；经验生命周期与持久化分别由
  `experience/` 和 `checkpointing/` 管理。
- 主 Agent 工具与受限 review Agent 工具使用两套显式 allowlist。review Agent 只能读取限定
  证据并提交结构化 review，不能获得主 Agent 的写入工具。
- 新增 signal source、target 或 draft kind 时，同步修改协议常量、schema、normalization、
  parser、prompt、tool schema 和测试，不能只扩展模型输出文本。

### 强化学习

`agent_rl/offline/` 负责任务、rollout、持久化和 verl 训练协调；`agent_rl/online/` 负责
Rail 采集、Gateway、judge、store、scheduler 和服务启动。两者可共享 schema、reward 与
部分存储抽象，但进程模型和生命周期不同。

## 修改规则

- 以目标子包 `__init__.py`、当前调用方和镜像测试目录为事实来源。
- 使用解决当前问题所需的最少抽象；新配置项和扩展点需要真实调用方或已确认需求。
- 工具 schema、描述、prompt section 和解析器必须同步；行为校验放在执行边界，prompt 只写
  模型需要遵循的协作契约。
- 公开 API 或行为变化同步检查
  `docs/en/2.Development Guide/API Docs/openjiuwen.agent_evolving/` 和
  `docs/en/2.Development Guide/Self Evolution/`；RL 变化还要检查 `examples/rl_*` 与
  `examples/jiuwenrl_online/`。

## 测试

- 全模块：`uv run pytest tests/unit_tests/agent_evolving -q`
- 单子域：`uv run pytest tests/unit_tests/agent_evolving/<subdir> -q`
- system tests 位于 `tests/system_tests/agent_evolving/`，通常需要真实模型、凭据、容器、
  Redis、Ray/verl 或硬件环境；不要用 mock 结果替代真实 E2E 结论。

优先运行被修改子域的镜像测试。涉及公开导出、持久化、审批恢复、并发、轨迹兼容或服务
生命周期时同时覆盖失败与恢复路径，并确认 pytest 进程正常退出。
