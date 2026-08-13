# Online-RL 脚本速查

本文只记录本地端到端验证常用脚本的关键点。默认在当前容器内执行，使用
`openjiuwen-rl` conda 环境，不需要 `docker exec`。

## 0. 换环境只改这里

本地机器相关配置统一放在：

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_local_env.sh
```

换机器/换模型/换端口时，优先只改这个文件。其他脚本都会 source 它。

关键变量：

```bash
ONLINE_RL_CONDA_ENV=openjiuwen-rl
ONLINE_RL_CONDA_SH=/data1/lll/miniconda3/etc/profile.d/conda.sh

MODEL_PATH=/data1/lll/models/Qwen3-4B-Thinking-2507
MODEL_NAME=Qwen3-4B-Thinking-2507

ONLINE_RL_DEVICE_BACKEND=cuda
ONLINE_RL_VISIBLE_DEVICES_ENV=CUDA_VISIBLE_DEVICES
PPO_CONFIG_PATH=

REDIS_URL=auto-detect
REDIS_CONTAINER_NAME=pinchbench-redis
REDIS_PORT=6379

VLLM_GPU=4,5
VLLM_TP=2
VLLM_HOST=127.0.0.1
VLLM_PORT=18002

GATEWAY_HOST=127.0.0.1
GATEWAY_PORT=18080

TRAIN_GPU=6,7
TRAIN_THRESHOLD=4
SCAN_INTERVAL=10
TRAJECTORY_BATCH_SIZE=1

AGENT_SERVER_PORT=18092
WEB_PORT=19000
CLAW_GATEWAY_PORT=19001
FRONTEND_PORT=5173
```

常规三步：

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/clean_online_rl_env.sh
agent-core/examples/jiuwenrl_online/deploy_scripts/start_online_rl_services.sh
agent-core/examples/jiuwenrl_online/deploy_scripts/send_online_rl_requests.sh
```

Redis 基本功能验证：

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/verify_online_rl_redis.sh
```

Ascend 平台最小改法：

```bash
# agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_local_env.sh
ONLINE_RL_DEVICE_BACKEND=ascend
ONLINE_RL_VISIBLE_DEVICES_ENV=ASCEND_RT_VISIBLE_DEVICES
VLLM_GPU=0,1
TRAIN_GPU=2,3
PPO_CONFIG_PATH=/path/to/online_ppo_npu.yaml
```

如果 Ascend vLLM 需要额外参数，也放在同一个文件：

```bash
VLLM_EXTRA_ARGS="--max-model-len 32768 --max-num-seqs 4 --enforce-eager"
```

改完仍然执行同一套命令：

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/clean_online_rl_env.sh
agent-core/examples/jiuwenrl_online/deploy_scripts/start_online_rl_services.sh
agent-core/examples/jiuwenrl_online/deploy_scripts/send_online_rl_requests.sh
```

## 1. 清空环境

```bash
cd /data1/lll/workspace/openjiuwen/refactor
agent-core/examples/jiuwenrl_online/deploy_scripts/clean_online_rl_env.sh
```

作用：

- 停掉 `deploy_scripts/online_rl_backend.sh` 拉起的服务。
- 删除 Redis 中 `rl:traj*`、`pending_judge*` key。
- `ray stop --force`。
- 删除 `logs/`、`records/`、`.jiuwenswarm-online/`。
- 将当前 `lora_repo/` 移到 `lora_repo.stale.<timestamp>/`，再创建新的空目录。

默认 Redis 会自动探测，顺序是：

```bash
1. 显式 REDIS_URL
2. 当前容器本地 redis://127.0.0.1:${REDIS_PORT}/0
3. docker inspect ${REDIS_CONTAINER_NAME}
4. fallback redis://127.0.0.1:${REDIS_PORT}/0
```

如果不想依赖 Docker 里的 Redis，可以用官方 Redis tar 包在当前容器本地启动：

```bash
cd /data1/lll/workspace/openjiuwen/refactor
agent-core/examples/jiuwenrl_online/deploy_scripts/deploy_local_redis_from_tarball.sh \
  start --tarball /path/to/redis-stable.tar.gz
export REDIS_URL=redis://127.0.0.1:6379/0
```

管理命令：

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/deploy_local_redis_from_tarball.sh status
agent-core/examples/jiuwenrl_online/deploy_scripts/deploy_local_redis_from_tarball.sh verify
agent-core/examples/jiuwenrl_online/deploy_scripts/deploy_local_redis_from_tarball.sh cli PING
agent-core/examples/jiuwenrl_online/deploy_scripts/deploy_local_redis_from_tarball.sh stop
agent-core/examples/jiuwenrl_online/deploy_scripts/deploy_local_redis_from_tarball.sh clean
```

## 2. 拉起所有后端服务

```bash
cd /data1/lll/workspace/openjiuwen/refactor
agent-core/examples/jiuwenrl_online/deploy_scripts/start_online_rl_services.sh
```

默认值：

```bash
source agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_local_env.sh
```

启动内容：

- vLLM: `http://127.0.0.1:18002`
- Gateway: `http://127.0.0.1:18080`
- OnlineTrainingScheduler
- JiuwenClaw agent/websocket: `ws://127.0.0.1:19000/ws`
- Web UI: `http://127.0.0.1:5173`

脚本会自动调用：

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh start
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh status
curl -sf "${GATEWAY_URL}/v1/gateway/stats"
```

## 3. 管理后端服务

底层控制脚本：

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh start
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh stop
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh restart
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh status
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh logs scheduler
```

关键环境变量：

```bash
source agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_local_env.sh
```

注意：

- 当前本地脚本按 CUDA/vLLM 写，昇腾平台需要把设备 env 和 PPO config 单独适配。
- `TRAIN_THRESHOLD` 本地验证保持 `4`，和 PPO mini batch 对齐。
- 如果 gateway 不在默认地址，设置 `GATEWAY_URL`，或设置 `GATEWAY_HOST`/`GATEWAY_PORT`。

## 4. 发送单条消息

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/send_online_rl_msg.sh "你的消息"
```

默认值：

```bash
ONLINE_RL_SESSION_ID=manual_online_rl_cli
ONLINE_RL_WS_URL=ws://127.0.0.1:19000/ws
ONLINE_RL_CWD=/data1/lll/workspace/openjiuwen/refactor
ONLINE_RL_CONDA_ENV=openjiuwen-rl
```

输出会打印：

- websocket 连接信息
- server hello 前 240 字符
- assistant 流式回复
- `processing=False`
- `done`

如果想连续触发延迟奖励和训练，复用同一个 `ONLINE_RL_SESSION_ID`。

## 5. 发送一批请求并检查训练

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/send_online_rl_requests.sh
```

默认会发送 8 轮消息，用来容忍个别 judge vote 慢或失败，同时触发一次 delayed reward + PPO 训练：

```text
第1轮，请只回复：1
第2轮，请只回复：2
第3轮，请只回复：3
第4轮，请只回复：4
第5轮，请只回复：5
第6轮，请只回复：6
第7轮，请只回复：7
第8轮，用于结算上一轮奖励，请只回复：8
```

也可以传自定义消息：

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/send_online_rl_requests.sh \
  "第一条消息" \
  "第二条消息" \
  "第三条消息" \
  "第四条消息" \
  "第五条消息"
```

脚本末尾会打印：

- Gateway stats: `http://127.0.0.1:18080/v1/gateway/stats`
- Scheduler 训练证据：
  - `Triggering PPO training`
  - `Converted 4 samples`
  - `train_step metrics`
  - `Published PPO LoRA`
  - `hot-loaded`
- vLLM LoRA 加载证据：
  - `Loaded new LoRA`
  - `POST /v1/load_lora_adapter`

## 6. 手动验训练和 LoRA

```bash
curl -sf http://127.0.0.1:18080/v1/gateway/stats
```

```bash
tail -n 220 agent-core/examples/jiuwenrl_online/logs/scheduler.log | \
  rg "Triggering PPO training|Converted 4 samples|train_step metrics|Published PPO LoRA|hot-loaded"
```

```bash
tail -n 120 agent-core/examples/jiuwenrl_online/logs/vllm.log | \
  rg "Loaded new LoRA|POST /v1/load_lora_adapter"
```

LoRA 文件应出现在：

```bash
agent-core/examples/jiuwenrl_online/lora_repo/local-web-user/v*/adapter_config.json
agent-core/examples/jiuwenrl_online/lora_repo/local-web-user/v*/adapter_model.safetensors
agent-core/examples/jiuwenrl_online/lora_repo/local-web-user/v*/metadata.json
```

## 7. 推荐一键验证顺序

```bash
cd /data1/lll/workspace/openjiuwen/refactor
agent-core/examples/jiuwenrl_online/deploy_scripts/clean_online_rl_env.sh
agent-core/examples/jiuwenrl_online/deploy_scripts/start_online_rl_services.sh
agent-core/examples/jiuwenrl_online/deploy_scripts/send_online_rl_requests.sh
```
