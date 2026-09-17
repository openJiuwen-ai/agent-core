# Agent Evolving RL

强化学习扩展，包含离线 rollout/training 和在线采集/training 两套运行时。该子包依赖较重，
不能让普通 `agent_evolving` 导入路径加载完整训练环境。

深入在线链路时继续读取 `online/AGENTS.md`。

## 模块地图

| 路径 | 职责 |
|---|---|
| `schemas.py` | 跨 rollout、reward 与训练链路的 Pydantic 数据模型 |
| `dataset.py` | verl `RLHFDataset` 适配和 dataset 构建 |
| `reward.py` | reward 注册与解析 |
| `config/` | 离线/在线配置模型 |
| `optimizer/` | `OfflineRLOptimizer` / `OnlineRLOptimizer` 高层入口 |
| `offline/` | 离线任务编排和训练循环 |
| `rl_trainer/` | verl 数据转换、PPO step 与执行器 |
| `storage/` | local/Redis trajectory、SFT data、task 和 LoRA 存储 |
| `rl_rail.py` | 经典 RL Rail |
| `online/` | 在线 Rail、Gateway、judge、scheduler 与 launcher |

## 公开与依赖边界

- `agent_rl.__init__` 通过 `__getattr__` 延迟导入 optimizer 和 Rail。不要在包级恢复对 Ray、
  verl、FastAPI、Redis、vLLM 或具体 backend 的 eager import。
- `RLTask`、`Rollout`、`RolloutMessage`、`RolloutWithReward` 和配置 schema 是跨离线/在线边界；
  修改字段时同步检查序列化、dataset、store、gateway 和 trainer。
- 具体 backend 的私有对象不得进入公共 schema。可选依赖只在使用对应能力时导入，并给出带
  上下文的缺依赖错误。

## 离线与在线边界

- 离线链路负责读取任务、并发生成 rollout、计算 reward、持久化和触发训练；每轮状态必须可
  从明确 checkpoint/store 恢复，不能依赖进程内计数推断。
- 在线链路通过 Rail/Gateway 收集样本，再由 judge、store 和 scheduler 驱动训练；它不是离线
  optimizer 的后台模式，不能复用其进程生命周期假设。
- 两条链路的消息和轨迹都从 canonical schema 转换。不要在 gateway、rollouter 和 verl
  converter 中各自发明角色、tool call 或 reward 字段。
- 训练候选、LoRA 版本和当前推理版本使用显式 ID/状态；发布与切换不能仅以文件存在为成功。

## 修改与测试

- 纯 schema/reward/storage 逻辑使用不依赖外部服务的 unit tests。
- Ray、verl、Redis、vLLM、GPU、容器和真实 Gateway 联调属于 system/integration evidence；
  mock 通过只能证明本地编排，不能声明训练或部署可用。
- 运行受影响的 `tests/unit_tests/agent_evolving/agent_rl/` 子目录，并确认进程正常退出；启动
  长期服务或训练前检查对应 example、配置和外部资源授权。
