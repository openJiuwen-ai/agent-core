# Real RL/SFT E2E Revalidation Runbook

This runbook records the non-mock, non-dry-run validation flow for the current
`agentos-sft-core-dev` code.

Validated on 2026-08-14:

- Conda env: `openjiuwen-rl`
- Base model: `/data1/lll/models/Qwen3-0.6B`
- vLLM GPUs: `0,1`
- Training GPUs: `4,5,6,7`
- Gateway: `http://127.0.0.1:18080`
- vLLM: `http://127.0.0.1:18002`
- Redis: auto-detected by `online_rl_local_env.sh`
- Training trigger mode: manual API only

Important controls:

- `ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1`
- `TRAIN_THRESHOLD=999`
- `SFT_DRY_RUN=0`

With this setup, samples are uploaded first and training starts only after
`POST /v1/training/tasks`.

## 1. RL PPO E2E

This validates:

`jiuwenswarm -> RLOnlineRail -> Gateway -> Redis -> /v1/training/tasks -> PPO -> LoRA repo -> vLLM hot-load`

### 1.1 Clean State

```bash
cd /data1/lll/workspace/openjiuwen/code-opt/agent-core

USE_CONDA=1 \
ONLINE_RL_CONDA_ENV=openjiuwen-rl \
MODEL_PATH=/data1/lll/models/Qwen3-0.6B \
MODEL_NAME=Qwen3-0.6B \
VLLM_GPU=0,1 \
VLLM_TP=2 \
TRAIN_GPU=4,5,6,7 \
TRAIN_BACKEND=PPO \
TRAIN_THRESHOLD=999 \
ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1 \
ONLINE_RL_ENABLE_JUDGE=0 \
ONLINE_RL_PPO_SAMPLES_PER_STEP=4 \
ONLINE_RL_MAX_SAMPLES_PER_RUN=4 \
examples/jiuwenrl_online/deploy_scripts/clean_online_rl_env.sh
```

### 1.2 Start Backend

```bash
USE_CONDA=1 \
ONLINE_RL_CONDA_ENV=openjiuwen-rl \
MODEL_PATH=/data1/lll/models/Qwen3-0.6B \
MODEL_NAME=Qwen3-0.6B \
VLLM_GPU=0,1 \
VLLM_TP=2 \
TRAIN_GPU=4,5,6,7 \
TRAIN_BACKEND=PPO \
TRAIN_THRESHOLD=999 \
ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1 \
ONLINE_RL_ENABLE_JUDGE=0 \
ONLINE_RL_PPO_SAMPLES_PER_STEP=4 \
ONLINE_RL_MAX_SAMPLES_PER_RUN=4 \
examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh start
```

Check service status:

```bash
examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh status
```

Expected:

- `vllm health: ok`
- `gateway health: ok`
- scheduler is running with `backend=PPO`

### 1.3 Collect Real RL Samples

Send real messages through jiuwenswarm WebSocket:

```bash
USE_CONDA=1 \
ONLINE_RL_CONDA_ENV=openjiuwen-rl \
MODEL_PATH=/data1/lll/models/Qwen3-0.6B \
MODEL_NAME=Qwen3-0.6B \
VLLM_GPU=0,1 \
VLLM_TP=2 \
TRAIN_GPU=4,5,6,7 \
TRAIN_BACKEND=PPO \
TRAIN_THRESHOLD=999 \
ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1 \
ONLINE_RL_ENABLE_JUDGE=0 \
ONLINE_RL_PPO_SAMPLES_PER_STEP=4 \
ONLINE_RL_MAX_SAMPLES_PER_RUN=4 \
ONLINE_RL_SESSION_ID=rl_e2e_manual_$(date +%Y%m%d%H%M%S) \
examples/jiuwenrl_online/deploy_scripts/send_online_rl_requests.sh
```

Verify that samples were uploaded but not auto-trained:

```bash
curl -sf http://127.0.0.1:18080/v1/gateway/stats | python3 -m json.tool
```

Expected:

- `trajectory_store_pending` is greater than or equal to `4`
- no LoRA has been generated yet unless a previous run was not cleaned

### 1.4 Trigger PPO Training By API

```bash
python - <<'PY'
from __future__ import annotations

import json
import time
import urllib.request

gateway = "http://127.0.0.1:18080"
payload = {
    "user_id": "local-web-user",
    "sample_count": 4,
    "metadata": {
        "source": "rl-real-e2e",
        "backend": "PPO",
        "manual_trigger": True,
    },
}

request = urllib.request.Request(
    gateway + "/v1/training/tasks",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=60) as response:
    task = json.loads(response.read().decode("utf-8"))
print(json.dumps(task, ensure_ascii=False, indent=2))

task_id = task["task_id"]
for idx in range(240):
    time.sleep(5)
    with urllib.request.urlopen(f"{gateway}/v1/training/tasks/{task_id}", timeout=20) as response:
        current = json.loads(response.read().decode("utf-8"))
    print(
        json.dumps(
            {
                "poll": idx + 1,
                "status": current.get("status"),
                "sample_count": current.get("sample_count"),
                "error": current.get("error"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if current.get("status") in {"succeeded", "failed", "canceled"}:
        print(json.dumps(current, ensure_ascii=False, indent=2))
        raise SystemExit(0 if current.get("status") == "succeeded" else 1)

raise TimeoutError(task_id)
PY
```

### 1.5 Verify RL LoRA

```bash
curl -sf "http://127.0.0.1:18080/v1/rl/lora/latest?model_id=local-web-user" | python3 -m json.tool

find examples/jiuwenrl_online/lora_repo/local-web-user \
  -maxdepth 3 \
  -type f \
  | sort

tail -n 160 examples/jiuwenrl_online/logs/vllm.log | \
  rg "Loaded new LoRA|POST /v1/load_lora_adapter" || true
```

Expected:

- latest LoRA has `load_status: "loaded"`
- `adapter_model.safetensors` exists
- vLLM log contains `Loaded new LoRA adapter`

Passing evidence from the 2026-08-14 validation:

- task: `task-e1e30281218a`
- LoRA: `local-web-user:v1`
- trajectory count: `4`
- adapter: `examples/jiuwenrl_online/lora_repo/local-web-user/v1/adapter_model.safetensors`
- vLLM loaded adapter name: `local-web-user`

### 1.6 Stop RL Backend

```bash
USE_CONDA=1 \
ONLINE_RL_CONDA_ENV=openjiuwen-rl \
MODEL_PATH=/data1/lll/models/Qwen3-0.6B \
MODEL_NAME=Qwen3-0.6B \
examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh stop
```

## 2. SFT veRL Local Program E2E

This validates:

`sft-optimize skill wrapper -> local_program rollouter -> supervisor vLLM -> SFTOnlineRail -> Gateway -> Redis -> /v1/training/tasks -> veRL SFT -> LoRA repo -> vLLM hot-load`

The local program flow does not start SWE Docker containers. It creates one
jiuwenswarm sampling session per local Python exercise and uploads
`sft-sample-v1`.

### 2.1 One-Command Revalidation

```bash
cd /data1/lll/workspace/openjiuwen/code-opt/agent-core

USE_CONDA=1 \
ONLINE_RL_CONDA_ENV=openjiuwen-rl \
SFT_E2E_CONDA_ENV=openjiuwen-rl \
SFT_E2E_MODEL_PATH=/data1/lll/models/Qwen3-0.6B \
SFT_E2E_MODEL_NAME=Qwen3-0.6B \
SFT_E2E_CASE_LIMIT=5 \
SFT_E2E_CONCURRENCY=1 \
SFT_E2E_TRAIN_GPU=4,5,6,7 \
SFT_E2E_MAX_LENGTH=8192 \
SFT_E2E_MAX_TOKEN_LEN_PER_GPU=8192 \
SFT_E2E_TRAIN_WAIT_TIMEOUT=1800 \
examples/jiuwenrl_online/sft_e2e/run_sft_verl_e2e_local.sh
```

The script performs all required steps:

1. Cleans Redis, local records, Ray state, and LoRA artifacts.
2. Starts vLLM, Gateway, scheduler, jiuwenswarm, and web services.
3. Runs `sft-optimize` with `--backend local_program`.
4. Uploads 5 `sft-sample-v1` records.
5. Exports pending SFT samples to `/tmp/sft_samples_sft-verl-local-e2e.json`.
6. Creates a manual `/v1/training/tasks` task.
7. Waits for veRL SFT to finish.
8. Verifies latest LoRA is hot-loaded.
9. Stops backend services on exit.

### 2.2 Manual Sample Collection Command

The one-command script above uses this skill wrapper command internally:

```bash
python examples/jiuwenrl_online/skills/sft-optimize/scripts/run_sft_optimize_skill.py \
  --request "我想要用 sft-optimize 技能对模型进行微调：数据集是 examples/jiuwenrl_online/sft_e2e/data/local_python_cases.json，5个本地 Python 用例，并发 1，不训练，只采集 supervisor replay 轨迹。" \
  --dataset-mapping examples/jiuwenrl_online/sft_e2e/data/local_python_cases.json \
  --limit 5 \
  --concurrency 1 \
  --backend local_program \
  --gateway-url http://127.0.0.1:18080 \
  --scheduler-url http://127.0.0.1:18080 \
  --supervisor-url http://127.0.0.1:18002 \
  --supervisor-token EMPTY \
  --supervisor-model Qwen3-0.6B \
  --tenant-id sft-verl-local-e2e \
  --no-trigger-training
```

Required details:

- `--backend local_program` is required for this local Python flow.
- `--no-trigger-training` is intentional. Training is triggered separately by
  `/v1/training/tasks`.
- The sampling rail uses `SFT_ONLINE_UPLOAD_MODE=sample`.
- The scheduler runs with `TRAIN_BACKEND=SFT` and
  `ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1`.

### 2.3 Manual Training Trigger

```bash
python examples/jiuwenrl_online/sft_transfer/trigger_sft_training.py \
  --gateway-url http://127.0.0.1:18080 \
  --user-id sft-verl-local-e2e \
  --sample-count 5 \
  --metadata '{"source":"sft-verl-e2e-local","cases":5,"max_length":8192}'
```

Monitor:

```bash
curl -s "http://127.0.0.1:18080/v1/training/tasks/<TASK_ID>" | python3 -m json.tool
```

### 2.4 Verify SFT LoRA

```bash
curl -sf "http://127.0.0.1:18080/v1/rl/lora/latest?model_id=sft-verl-local-e2e" | python3 -m json.tool

find examples/jiuwenrl_online/lora_repo/sft-verl-local-e2e \
  -maxdepth 3 \
  -type f \
  | sort

tail -n 180 examples/jiuwenrl_online/logs/scheduler.log | \
  rg "Triggering SFT training|train/loss|Published SFT LoRA|hot-loaded|load_lora_adapter" || true
```

Expected:

- latest LoRA has `load_status: "loaded"`
- `adapter_model.safetensors` exists
- scheduler log contains a real `train/loss`
- vLLM receives `/v1/load_lora_adapter`

Passing evidence from the 2026-08-14 validation:

- uploaded samples: `5`
- task: `task-59bd0cd72be7`
- train loss: `1.3468551635742188`
- LoRA: `sft-verl-local-e2e:v1`
- adapter: `examples/jiuwenrl_online/lora_repo/sft-verl-local-e2e/v1/adapter_model.safetensors`
- hot-load: `load_status: "loaded"`

## 3. Cleanup

Stop services and remove local runtime state:

```bash
USE_CONDA=1 \
ONLINE_RL_CONDA_ENV=openjiuwen-rl \
MODEL_PATH=/data1/lll/models/Qwen3-0.6B \
MODEL_NAME=Qwen3-0.6B \
examples/jiuwenrl_online/deploy_scripts/clean_online_rl_env.sh
```

If only stopping services is needed:

```bash
examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh stop
```

Confirm service state:

```bash
examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh status
nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader,nounits
```

## 4. Troubleshooting Checks

Check backend logs:

```bash
tail -n 200 examples/jiuwenrl_online/logs/gateway.log
tail -n 240 examples/jiuwenrl_online/logs/scheduler.log
tail -n 160 examples/jiuwenrl_online/logs/vllm.log
tail -n 160 examples/jiuwenrl_online/logs/jiuwenswarm.log
```

Check sample counters:

```bash
curl -sf http://127.0.0.1:18080/v1/gateway/stats | python3 -m json.tool
```

Check tasks:

```bash
curl -sf "http://127.0.0.1:18080/v1/training/tasks?limit=20" | python3 -m json.tool
```

Common issues:

- If training starts before the API call, check `ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1`.
- If SFT sample collection uses Docker unexpectedly, pass `--backend local_program`.
- If SFT training does not run, check `SFT_DRY_RUN=0` and `TRAIN_BACKEND=SFT`.
- If SFT only has raw records, check `SFT_ONLINE_UPLOAD_MODE=sample`.
- If LoRA files exist but inference is not updated, check vLLM
  `/v1/load_lora_adapter` logs.
