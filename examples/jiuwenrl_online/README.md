# JiuwenSwarm Online RL 更新示例

本示例展示 JiuwenSwarm 如何通过 AgentBox Adapter AIGW 采集真实交互、提交 terminal reward、显式触发
Training Run，并让后续 JiuwenSwarm Task 使用新 LoRA。它对应当前生产架构，不再启动旧 Python Gateway、
`OnlineTrainingScheduler` 或 collection session。

```text
JiuwenSwarm
  -> AIGW /v1/chat/completions
  -> vLLM (base 或 Task 固定的 LoRA)
  -> AIGW 管理的 RL Service
  -> Task + terminal reward
  -> Training Run -> PPO -> LoRA 激活
  -> 新 Task 使用新策略
```

## 边界

示例脚本只负责一次 JiuwenSwarm 交互的 Task/reward 生命周期，并可选触发 Training Run。以下长期运行服务
应由部署系统管理，脚本不会启动或停止它们：

- Redis、推理 vLLM 和 Judge
- AgentBox Adapter AIGW
- PPO/Ray 训练环境
- JiuwenSwarm Agent Server 和 Gateway

AIGW 与 RL Service 的完整配置和启停要求见
[Online RL Service Operations](../../docs/dev/online-rl-service-operations.md)。

## 1. 配置 JiuwenSwarm

先选择一个稳定的 RL session ID。AIGW 用它把 JiuwenSwarm 的模型请求关联到当前 Task：

```bash
export ONLINE_RL_SESSION_ID=jiuwenswarm-online-demo
```

修改 JiuwenSwarm 的 `config.yaml`，保证默认模型指向 AIGW，模型名与 AIGW 注册的 `model_id` 一致，并注入
相同的 session ID：

```yaml
models:
  defaults:
    - model_client_config:
        api_base: http://127.0.0.1:18080/v1
        api_key: EMPTY
        model_name: Qwen3-8B
        client_provider: OpenAI
        verify_ssl: false
        custom_headers:
          X-Agent-Session-Id: jiuwenswarm-online-demo
      model_config_obj:
        temperature: 0.7
      is_default: true
```

配置变更后启动或重启 JiuwenSwarm：

```bash
jiuwenswarm-start app
```

如果 JiuwenSwarm 自动选择了非默认端口，将日志中输出的 `/tui` WebSocket 地址传给示例的
`--gateway-url`；默认端口通常可以由 JiuwenSwarm CLI 自动发现。

## 2. 采集交互和奖励

生产 AIGW 的控制 API 使用 HMAC。将与 AIGW key provider 一致的 key 放入环境变量，不要写进参数或配置文件：

```bash
export AIGW_API_HMAC_KEY='replace-with-the-configured-key'
```

执行一次带 terminal reward 的 JiuwenSwarm 交互：

```bash
python examples/jiuwenrl_online/run_jiuwenswarm_online_rl.py \
  "完成当前仓库中的目标任务" \
  --reward 1
```

脚本依次执行：

1. 检查 AIGW 管理的 RL Service。
2. 使用 `ONLINE_RL_SESSION_ID` 创建 terminal-reward Task，并打印其 `policy_lora_name`。
3. 通过 `jiuwenswarm chat --mode agent` 发起真实交互。
4. 停止 Task、提交 reward，并打印形成的训练样本数。

RL Service 尚未启动时可增加 `--start-service`。仅当开发环境明确关闭了 AIGW HMAC 时才使用 `--unsigned`：

```bash
python examples/jiuwenrl_online/run_jiuwenswarm_online_rl.py \
  "完成当前仓库中的目标任务" \
  --reward 1 \
  --start-service \
  --unsigned
```

负例或未达到目标的交互应提交真实的低奖励，例如 `--reward 0`。不要为了凑训练数量统一标为成功。

## 3. 触发在线更新

先按 RL Service 的 `min_samples_for_training` 收集足够样本。最后一次交互增加 `--train`，脚本会创建并等待
Training Run：

```bash
python examples/jiuwenrl_online/run_jiuwenswarm_online_rl.py \
  "完成当前仓库中的目标任务" \
  --reward 1 \
  --train
```

成功输出应包含如下状态变化和新策略名：

```text
Training Run run-...: status=pending stage=queued
Training Run run-...: status=running stage=training
Training Run run-...: status=running stage=activating
Training Run run-...: status=succeeded stage=activating
Activated policy: Qwen3-8B:v1 (.../Qwen3-8B/v1)
```

再次运行不带 `--train` 的交互。新建 Task 打印的 `policy_lora_name` 应为刚激活的版本；训练前已经存在的
Task 仍固定使用原策略，这是预期行为。

## 4. 真实端到端验证

仓库还提供真实 JiuwenSwarm、Claude Code、AIGW、vLLM、Redis、PPO 和 LoRA 激活系统测试。
JiuwenSwarm 使用 OpenAI 协议入口：

```bash
RUN_ONLINE_RL_TRAINING_ST=1 \
AIGW_REPO=/path/to/AgentInfra/Adapter \
JIUWENSWARM_REPO=/path/to/jiuwenswarm \
python -m pytest -vv -s \
  tests/system_tests/agent_evolving/agent_rl/online/test_jiuwenswarm_training_e2e.py
```

Claude Code 使用 Anthropic 协议入口；`CLAUDE_CODE_CLI` 可省略，此时默认从 `PATH` 查找 `claude`：

```bash
RUN_ONLINE_RL_TRAINING_ST=1 \
AIGW_REPO=/path/to/AgentInfra/Adapter \
CLAUDE_CODE_CLI=/path/to/claude \
python -m pytest -vv -s \
  tests/system_tests/agent_evolving/agent_rl/online/test_claude_code_training_e2e.py
```

两项测试不仅检查 API 状态，还验证生成了非零 LoRA、rewarded 样本的相对偏好得到改善，并确认训练后的
新 Task 实际绑定新策略。默认测试收集模式会跳过真实 GPU 运行；只有显式设置
`RUN_ONLINE_RL_TRAINING_ST=1` 才会启动完整训练。
