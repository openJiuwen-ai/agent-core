# Supervisor Replay to LLaMA-Factory SFT 8K E2E

本文档记录在当前机器复现 `supervisor replay -> SFT sample -> gateway -> scheduler -> LLaMA-Factory LoRA SFT -> vLLM hot-load` 的命令。验证时使用本地 Qwen3-0.6B，VLLM 使用 0、1 卡，训练使用 4、5、6、7 卡。

## 1. 环境

```bash
cd /data1/lll/workspace/openjiuwen/code-opt/agent-core
source /data1/lll/miniconda3/etc/profile.d/conda.sh
conda activate openjiuwen-sft
export PYTHONPATH=/data1/lll/workspace/openjiuwen/code-opt/agent-core:/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw
```

可选但推荐先安装 `sft-optimize` skill，后续可直接通过 skill 触发
supervisor replay 和训练：

```bash
bash examples/jiuwenrl_online/skills/install_sft_optimize_skill.sh
```

## 2. 启动后端

```bash
ONLINE_RL_CONDA_ENV=openjiuwen-sft \
MODEL_PATH=/data1/lll/models/Qwen3-0.6B \
MODEL_NAME=Qwen3-0.6B \
VLLM_GPU=0,1 \
VLLM_TP=2 \
VLLM_HOST=0.0.0.0 \
VLLM_PORT=18002 \
VLLM_EXTRA_ARGS='--max-model-len 32768 --gpu-memory-utilization 0.80 --max-num-seqs 4 --enforce-eager' \
GATEWAY_HOST=0.0.0.0 \
GATEWAY_PORT=18080 \
TRAIN_GPU=4,5,6,7 \
TRAIN_BACKEND=SFT \
TRAIN_THRESHOLD=10 \
ONLINE_RL_MAX_SAMPLES_PER_RUN=10 \
ONLINE_RL_PPO_SAMPLES_PER_STEP=10 \
ONLINE_RL_ALLOW_PARTIAL_LAST_STEP=1 \
ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1 \
ONLINE_RL_ENABLE_JUDGE=0 \
SFT_ROLLOUTER=multi_turn_supervisor \
SFT_DRY_RUN=0 \
SFT_TASK_MAX_ITERATIONS=1 \
SFT_LLAMAFACTORY_CUTOFF_LEN=8192 \
SFT_LLAMAFACTORY_MAX_STEPS=1 \
SFT_LLAMAFACTORY_TEMPLATE=qwen3 \
SFT_LLAMAFACTORY_TRUNCATE_TO_CUTOFF=1 \
SFT_LLAMAFACTORY_EXTRA_ARGS='{"enable_liger_kernel": true, "flash_attn": "fa2"}' \
SFT_LORA_RANK=8 \
SFT_LORA_ALPHA=16 \
SFT_DOCKER_CONDA_ENV=openjiuwen-sft \
SFT_DOCKER_AGENT_CORE_HOST_PATH=/data1/lll/workspace/openjiuwen/code-opt/agent-core \
SFT_DOCKER_JIUWENCLAW_HOST_PATH=/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw \
examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh start
```

健康检查：

```bash
curl -sf http://127.0.0.1:18080/health
curl -sf http://172.17.0.5:18080/health
curl -sf http://127.0.0.1:18002/health
curl -sf http://172.17.0.5:18002/health
```

## 3. Supervisor Replay 采集

`tenant-id` 建议每次换一个，避免 Redis 中旧样本影响判断。下面示例使用 `sft-e2e-8k-manual`。

```bash
python examples/jiuwenrl_online/sft_rollout/run_sft_optimize.py \
  --dataset-mapping examples/jiuwenrl_online/sft_e2e/data/sft_short_10_cases.json \
  --limit 10 \
  --offset 0 \
  --gateway-url http://172.17.0.5:18080 \
  --scheduler-url http://127.0.0.1:18080 \
  --supervisor-url http://172.17.0.5:18002 \
  --supervisor-token EMPTY \
  --supervisor-model Qwen3-0.6B \
  --tenant-id sft-e2e-8k-manual \
  --concurrency 2 \
  --timeout 900
```

检查样本：

```bash
docker exec pinchbench-redis redis-cli ZCARD rl:sft_sample_idx:sft-e2e-8k-manual:pending
tail -50 examples/jiuwenrl_online/logs/gateway.log
```

## 4. 触发训练

```bash
curl -sf -X POST http://127.0.0.1:18080/v1/training/tasks \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"sft-e2e-8k-manual","metadata":{"e2e":"supervisor_replay_to_sft_8k","cutoff_len":8192,"sample_count":10,"model":"Qwen3-0.6B"}}'
```

轮询任务：

```bash
TASK_ID=替换为上一步返回的 task_id
watch -n 5 "curl -sf http://127.0.0.1:18080/v1/training/tasks/${TASK_ID}"
```

## 5. 验证结果

确认 LLaMA-Factory 被使用、样本数为 10、LoRA 被发布并热加载：

```bash
rg -n "Triggering SFT training|SFT run|llama-factory|SFT record truncated|tools dropped|Published SFT|load_lora_adapter|ERROR" \
  examples/jiuwenrl_online/logs/scheduler.log | tail -120

find examples/jiuwenrl_online/lora_repo/sft-e2e-8k-manual -maxdepth 3 -type f -printf '%p %s bytes\n' | sort
```

确认训练输入没有超过 8k：

```bash
python - <<'PY'
import json
from pathlib import Path

run_dirs = sorted(Path("/tmp/agent_rl_online").glob("sft_run_*/llama_factory"), key=lambda p: p.stat().st_mtime)
latest = run_dirs[-1]
records = json.loads((latest / "train.json").read_text())
metas = [record.get("metadata", {}) for record in records]
print("llama_factory_dir", latest)
print("records", len(records))
print("max_token_after_meta", max((m.get("token_count_after_truncate") or m.get("token_count") or 0) for m in metas))
print("truncated", sum(1 for m in metas if m.get("truncated_to_cutoff")))
print("tools_dropped", sum(1 for m in metas if m.get("truncate_tools_dropped")))
print((latest / "stats.json").read_text())
PY
```

## 6. 清理

```bash
examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh stop
```

## 本次验证记录

- tenant: `sft-e2e-8k-10b`
- task: `task-bf2c42c652ad`
- rollout: 10 个 SWE case 全部 exit=0
- training: LLaMA-Factory LoRA SFT 成功，`Num examples = 10`
- LoRA: `examples/jiuwenrl_online/lora_repo/sft-e2e-8k-10b/v1/adapter_model.safetensors`
- hot-load: scheduler 日志中 `/v1/load_lora_adapter` 返回 200
- 截断: `stats.json` 中 `record_count=10`、`truncated_records=10`、`max_token_count=833`
