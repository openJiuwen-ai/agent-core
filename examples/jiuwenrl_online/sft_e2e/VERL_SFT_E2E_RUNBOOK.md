# veRL SFT E2E 手动复现

这份文档用于复现当前 `agentos-sft-core` 分支上的 veRL SFT 全链路。

链路固定为四步：

1. 启动后端服务
2. 通过 `sft-optimize` skill 拉起 SWE 用例容器并上传 `sft-sample-v1`
3. 导出待训练轨迹
4. 通过 API 手动触发训练，等待 veRL 产出 LoRA

注意：

- 训练触发必须走 `/v1/training/tasks`，不要依赖阈值自动触发
- 采集模式必须是 `SFT_ONLINE_UPLOAD_MODE=sample`
- `RL_ONLINE_CAPTURE_MODE` 仍保持 `raw_session`
- 校验时优先看当前 `user_id` 的轨迹状态，不要把历史残留的全局 raw 当成失败

## 1. 最短复现

只要环境已经装好，直接执行：

```bash
bash examples/jiuwenrl_online/sft_e2e/run_sft_verl_e2e.sh
```

这个脚本会自动完成：

- 清理旧状态
- 启动 backend
- 通过 skill wrapper 采集 5 个本地 Python 用例轨迹
- 导出 pending sample
- 用 `/v1/training/tasks` 触发训练
- 等待 veRL 训练完成并检查 LoRA hot-load

## 2. 环境

```bash
source /data1/lll/miniconda3/etc/profile.d/conda.sh
conda activate openjiuwen-rl
cd /data1/lll/workspace/openjiuwen/code-opt/agent-core
export PYTHONPATH=/data1/lll/workspace/openjiuwen/code-opt/agent-core:/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw
export HOST_IP="$(hostname -I | tr ' ' '\n' | awk '/^172\./ {print; exit}')"
export VLLM_HOST=0.0.0.0
export VLLM_URL="http://${HOST_IP}:18002"
export GATEWAY_HOST=0.0.0.0
export GATEWAY_URL="http://${HOST_IP}:18080"
export SUPERVISOR_URL="${VLLM_URL}"
export RL_GATEWAY_URL="${GATEWAY_URL}"
export RL_SCHEDULER_URL="http://127.0.0.1:18080"
export TRAIN_BACKEND=SFT
export TRAIN_THRESHOLD=999
export ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1
export ONLINE_RL_ENABLE_JUDGE=0
export SFT_DRY_RUN=0
export SFT_UPLOAD_CHECK_TIMEOUT=0
```

## 3. 按步骤手动执行

### 3.1 启动后端

```bash
bash examples/jiuwenrl_online/deploy_scripts/clean_online_rl_env.sh
bash examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh start
```

### 3.2 采集轨迹

```bash
python examples/jiuwenrl_online/skills/sft-optimize/scripts/run_sft_optimize_skill.py \
  --request "我想要用 sft-optimize 技能对模型进行微调：数据集是 examples/jiuwenrl_online/sft_e2e/data/local_python_cases.json，5个本地 Python 用例，并发 1，不训练，只采集 supervisor replay 轨迹。" \
  --dataset-mapping examples/jiuwenrl_online/sft_e2e/data/local_python_cases.json \
  --limit 5 \
  --concurrency 1 \
  --backend local_program \
  --gateway-url http://${HOST_IP}:18080 \
  --scheduler-url http://127.0.0.1:18080 \
  --supervisor-url http://${HOST_IP}:18002 \
  --supervisor-token EMPTY \
  --supervisor-model Qwen3-0.6B \
  --tenant-id sft-verl-local-e2e \
  --no-trigger-training
```

### 3.3 导出待训练轨迹

```bash
python examples/jiuwenrl_online/sft_transfer/export_sft_samples.py \
  --redis-url "${REDIS_URL}" \
  --user-id sft-verl-local-e2e \
  --status pending \
  --limit 5 \
  --output /tmp/sft_samples_sft-verl-local-e2e.json
```

### 3.4 手动触发训练

```bash
python examples/jiuwenrl_online/sft_transfer/trigger_sft_training.py \
  --gateway-url http://127.0.0.1:18080 \
  --user-id sft-verl-local-e2e \
  --sample-count 5 \
  --metadata '{"source":"sft-verl-local-e2e","cases":5,"max_length":8192}'
```

## 4. 验证结果

### 4.1 查看训练任务

```bash
curl -s http://127.0.0.1:18080/v1/training/tasks/<TASK_ID> | python3 -m json.tool
```

### 4.2 查看最新 LoRA

```bash
curl -s "http://127.0.0.1:18080/v1/rl/lora/latest?model_id=sft-verl-local-e2e" | python3 -m json.tool
find examples/jiuwenrl_online/lora_repo/sft-verl-local-e2e -name adapter_model.safetensors
```

### 4.3 查看轨迹

```bash
python examples/jiuwenrl_online/sft_e2e/validate_sft_e2e.py \
  --phase direct-final \
  --redis-url "${REDIS_URL}" \
  --gateway-url http://127.0.0.1:18080 \
  --user-id sft-verl-local-e2e \
  --task-id <TASK_ID> \
  --tmp-root /tmp/agent_rl_online \
  --min-samples 5 \
  --wait-timeout 1800
```

## 5. 常见问题

- `run_sft_optimize_skill.py` 默认 `backend=docker`。如果不传 `--backend`，或者环境里没有设置 `SFT_ROLLOUT_BACKEND`，就会走 SWE Docker 用例链路。
- 如果你要跑本地 Python 练习目录，必须显式传 `--backend local_program`，并使用 `examples/jiuwenrl_online/sft_e2e/data/local_python_cases.json`。
- 如果训练没有触发，先确认是不是走了 `/v1/training/tasks`
- 如果只看到 raw 没看到 sample，确认 `SFT_ONLINE_UPLOAD_MODE=sample`
- 如果 stats 里还有别的用户的 pending raw，不要把它当成当前 tenant 的失败
- 如果 LoRA 没有 hot-load，先看 gateway 和 vLLM 的 `v1/load_lora_adapter` 日志
- 如果想手动定位问题，先查：
  - `curl -s http://127.0.0.1:18080/v1/training/tasks`
  - `curl -s http://127.0.0.1:18080/v1/rl/lora/latest?model_id=sft-verl-local-e2e`
  - `tail -n 200 examples/jiuwenrl_online/logs/scheduler.log`

## 6. 本地 Python 练习版

如果你想验证“本机采样 session + supervisor + 训练”链路，但不想启动 SWE Docker，可以直接跑：

```bash
bash examples/jiuwenrl_online/sft_e2e/run_sft_verl_e2e_local.sh
```

这个版本会：

1. 启动后端服务
2. 直接在本机跑 5 个小型 Python 练习目录
3. 每个目录单独拉起一个本地 jiuwenswarm 采样 session，并上传 `sft-sample-v1`
4. 导出 pending 样本
5. 通过 `/v1/training/tasks` 手动触发训练
6. 等待 veRL 生成 LoRA 并检查热加载

实际使用时，业务入口 jiuwenswarm 使用业务 LLM；task_rollouter 拉起的采样 session 使用 `SUPERVISOR_URL` / `SUPERVISOR_MODEL` 指定的 teacher LLM。两者是分离配置，本地 E2E 为了节省资源才把它们都指向同一个 Qwen3-0.6B vLLM。
