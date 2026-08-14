# Jiuwen Online RL E2E Runbook

This note records the working local flow for the online-RL stack in this
workspace. It is meant for restarting a fresh session and getting to an E2E
signal without rediscovering the same failures.

## Known Working Setup

- Workspace: `/data1/lll/workspace/openjiuwen/refactor`
- Conda env: `openjiuwen-rl`
- Model: `/data1/lll/models/Qwen3-4B-Thinking-2507`
- Inference vLLM: `http://127.0.0.1:18002`
- Online RL gateway: `http://127.0.0.1:18080`
- JiuwenSwarm WebChannel: `ws://127.0.0.1:19000/ws`
- Web UI: `http://127.0.0.1:5173`
- Redis in this container: `redis://172.17.0.2:6379/0`

Do not use `docker exec` for this flow. Run commands in the current container
with the `openjiuwen-rl` env.

## Start The Backend

Use the one-shot backend script:

```bash
cd /data1/lll/workspace/openjiuwen/refactor

REDIS_URL=redis://172.17.0.2:6379/0 \
VLLM_GPU=4,5 \
TRAIN_GPU=6,7 \
TRAIN_THRESHOLD=4 \
SCAN_INTERVAL=10 \
TRAJECTORY_BATCH_SIZE=1 \
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh start
```

Useful commands:

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh status
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh logs gateway
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh stop
```

If only scheduler settings need changing, stop scheduler and Ray workers, then
run `start` again:

```bash
PID_FILE=agent-core/examples/jiuwenrl_online/logs/pids/scheduler.pid
test -f "$PID_FILE" && kill -TERM "$(cat "$PID_FILE")" 2>/dev/null || true
sleep 8
source /data1/lll/miniconda3/etc/profile.d/conda.sh
conda activate openjiuwen-rl
ray stop --force
rm -f "$PID_FILE"

REDIS_URL=redis://172.17.0.2:6379/0 \
VLLM_GPU=4,5 TRAIN_GPU=6,7 TRAIN_THRESHOLD=4 SCAN_INTERVAL=10 TRAJECTORY_BATCH_SIZE=1 \
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh start
```

## Smoke Test Through JiuwenClaw

Send real WebSocket turns through JiuwenClaw. Use at least 5 turns: the first 4
create trainable assistant turns, and the 5th triggers delayed reward for turn
4.

For manual single-message sends, use:

```bash
agent-core/examples/jiuwenrl_online/deploy_scripts/send_online_rl_msg.sh "你的消息"
```

The script reuses `ONLINE_RL_SESSION_ID=manual_online_rl_cli` by default, so
repeated calls accumulate into one session and can trigger training after enough
delayed-reward samples are judged. Override the session when needed:

```bash
ONLINE_RL_SESSION_ID=my_session \
agent-core/examples/jiuwenrl_online/deploy_scripts/send_online_rl_msg.sh "第1条消息"
```

```bash
source /data1/lll/miniconda3/etc/profile.d/conda.sh
conda activate openjiuwen-rl
PYTHONPATH=/data1/lll/workspace/openjiuwen/refactor/agent-core:/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw \
python - <<'PY'
import asyncio, json, time, uuid
import websockets

URL = "ws://127.0.0.1:19000/ws"
SESSION = "online_rl_e2e_" + uuid.uuid4().hex[:8]
CWD = "/data1/lll/workspace/openjiuwen/refactor"
PROMPTS = [
    "端到端验证第1轮。请只回复：收到1",
    "端到端验证第2轮。请只回复：收到2",
    "端到端验证第3轮。请只回复：收到3",
    "端到端验证第4轮。请只回复：收到4",
    "端到端验证第5轮，用于结算上一轮奖励。请只回复：收到5",
]

async def wait_done(ws, req_id, turn):
    start = time.time()
    while time.time() - start < 900:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=900))
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        event = msg.get("event") or payload.get("event_type")
        if msg.get("type") == "res" and msg.get("id") == req_id:
            print(f"turn {turn} ack ok={msg.get('ok')}")
        if event == "chat.processing_status":
            print(f"turn {turn} processing={payload.get('is_processing')}")
            if payload.get("is_processing") is False:
                return
    raise TimeoutError(f"turn {turn} did not finish")

async def main():
    async with websockets.connect(URL, max_size=8 * 2**20, close_timeout=2) as ws:
        print(await asyncio.wait_for(ws.recv(), timeout=5))
        for turn, prompt in enumerate(PROMPTS, 1):
            req_id = "chat-" + uuid.uuid4().hex[:12]
            frame = {
                "type": "req",
                "id": req_id,
                "method": "chat.send",
                "is_stream": True,
                "params": {
                    "session_id": SESSION,
                    "content": prompt,
                    "query": prompt,
                    "mode": "agent.plan",
                    "cwd": CWD,
                    "project_dir": CWD,
                    "trusted_dirs": [CWD],
                },
            }
            await ws.send(json.dumps(frame, ensure_ascii=False))
            await wait_done(ws, req_id, turn)
            await asyncio.sleep(2)
    print("done", SESSION)

asyncio.run(main())
PY
```

## Success Checks

Gateway stats should increase:

```bash
curl -sf http://127.0.0.1:18080/v1/gateway/stats
```

Expected fields:

- `total_requests` grows when chat goes through gateway to vLLM.
- `total_samples` grows when `RLOnlineRail` uploads trajectories.
- `trajectory_store_total` grows when judged samples are written to Redis.
- `trajectory_store_pending` may stay non-zero because delayed reward always
  keeps the latest turn pending until a following user message arrives.

JiuwenClaw must load online rail:

```bash
rg -n "RLOnlineRail added" agent-core/examples/jiuwenrl_online/logs/jiuwenswarm.log
```

Gateway must call the local vLLM:

```bash
rg -n "POST http://127.0.0.1:18002/v1/chat/completions" \
  agent-core/examples/jiuwenrl_online/logs/gateway.log
```

Scheduler must show non-empty PPO training. The important values are
`batch_size=4` and `training/n_triplets_dropped_remainder: 0`:

```bash
tail -n 220 agent-core/examples/jiuwenrl_online/logs/scheduler.log | \
  rg "Converted 4 samples|train_step metrics|Published PPO LoRA|hot-loaded"
```

vLLM must load the new LoRA:

```bash
tail -n 160 agent-core/examples/jiuwenrl_online/logs/vllm.log | \
  rg "Loaded new LoRA|POST /v1/load_lora_adapter"
```

Example passing evidence from 2026-06-30:

- `Converted 4 samples to DataProto (batch_size=4, ...)`
- `training/n_triplets_dropped_remainder': 0`
- `Published PPO LoRA user=local-web-user version=v205`
- `LoRA hot-loaded for user local-web-user: .../lora_repo/local-web-user/v205`
- vLLM: `Loaded new LoRA adapter: name 'local-web-user', path '.../v205'`

Clean restart passing evidence from 2026-06-30:

- Runtime dirs were cleared and Redis `rl:traj*` keys were deleted.
- The backend was started only through `deploy_scripts/online_rl_backend.sh start`.
- `Triggering PPO training #1 for user=local-web-user samples=4`
- `Converted 4 samples to DataProto (batch_size=4, ...)`
- `training/n_triplets_dropped_remainder': 0`
- `Published PPO LoRA user=local-web-user version=v1`
- `LoRA hot-loaded for user local-web-user: .../lora_repo/local-web-user/v1`
- vLLM: `Loaded new LoRA adapter: name 'local-web-user', path '.../v1'`

## Clean Restart

Shortcut scripts:

```bash
cd /data1/lll/workspace/openjiuwen/refactor

agent-core/examples/jiuwenrl_online/deploy_scripts/clean_online_rl_env.sh
agent-core/examples/jiuwenrl_online/deploy_scripts/start_online_rl_services.sh
agent-core/examples/jiuwenrl_online/deploy_scripts/send_online_rl_requests.sh
```

Stop services, clear only online-RL runtime artifacts, then start via the
one-shot script:

```bash
cd /data1/lll/workspace/openjiuwen/refactor

agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh stop

source /data1/lll/miniconda3/etc/profile.d/conda.sh
conda activate openjiuwen-rl
redis-cli -u redis://172.17.0.2:6379/0 --scan --pattern 'rl:traj*' | \
  xargs -r redis-cli -u redis://172.17.0.2:6379/0 DEL
ray stop --force

rm -rf agent-core/examples/jiuwenrl_online/logs \
       agent-core/examples/jiuwenrl_online/records \
       agent-core/examples/jiuwenrl_online/.jiuwenswarm-online
mkdir -p agent-core/examples/jiuwenrl_online/logs \
         agent-core/examples/jiuwenrl_online/records \
         agent-core/examples/jiuwenrl_online/lora_repo \
         agent-core/examples/jiuwenrl_online/.jiuwenswarm-online

REDIS_URL=redis://172.17.0.2:6379/0 \
VLLM_GPU=4,5 \
TRAIN_GPU=6,7 \
TRAIN_THRESHOLD=4 \
SCAN_INTERVAL=10 \
TRAJECTORY_BATCH_SIZE=1 \
agent-core/examples/jiuwenrl_online/deploy_scripts/online_rl_backend.sh start
```

If `lora_repo` contains root-owned old adapter files and `rm -rf lora_repo`
fails, move the whole repo aside and recreate an empty repo. This is safe for a
local clean validation because vLLM is restarted and the old adapters are not
needed:

```bash
ts=$(date +%Y%m%d_%H%M%S)
mv agent-core/examples/jiuwenrl_online/lora_repo \
   agent-core/examples/jiuwenrl_online/lora_repo.stale.$ts
mkdir -p agent-core/examples/jiuwenrl_online/lora_repo
```

## Common Failures

1. JiuwenClaw not paired with vLLM

This was not the root cause in the verified run. Gateway logs showed:

```text
POST http://127.0.0.1:18002/v1/chat/completions "HTTP/1.1 200 OK"
```

2. Wrong Redis URL

The README default is `redis://127.0.0.1:6379/0`, but this container used:

```text
redis://172.17.0.2:6379/0
```

Use the same `REDIS_URL` for gateway and scheduler.

3. Empty PPO batch

Do not use `TRAIN_THRESHOLD=1` or `2` for this config. PPO mini-batch is 4.
When threshold is too small, logs show:

```text
Data alignment: 2 -> 0 samples
Batch is empty after data alignment
training/skipped_empty_batch: 1
```

Use `TRAIN_THRESHOLD=4`.

4. `records/samples.jsonl` permission denied

If gateway is running as user `lll` and `records/samples.jsonl` is root-owned,
uploads fail with `PermissionError`. Fix ownership or replace the file with a
user-writable copy:

```bash
ls -l agent-core/examples/jiuwenrl_online/records/samples.jsonl
```

5. Judge makes local validation slow

Judge currently reuses the inference vLLM. It often times out and falls back to
score `0.0`, but still produces judged samples. This can leave vLLM with several
queued requests and make WebSocket turns slow. For faster routine validation,
use an independent judge service or reduce judge load.

6. Prompt too long

Use the script's default light profile. It disables several JiuwenClaw rail/tool
surfaces so prompts fit within the 16k vLLM limit.

7. vLLM prometheus route issue

Use `deploy_scripts/vllm_patched_launcher.py` from this directory. It monkeypatches the
prometheus instrumentator issue seen with the installed vLLM stack.

8. WAL files after upload failures

If `records/rail_v1_wal/*.json` exists, replay it against gateway after fixing
the root cause. Use a long timeout for local E2E because the upload endpoint
does synchronous delayed-judge scoring and the judge currently reuses the same
vLLM as inference:

```bash
for f in agent-core/examples/jiuwenrl_online/records/rail_v1_wal/*.json; do
  [ -e "$f" ] || continue
  curl --max-time 900 -sf -X POST http://127.0.0.1:18080/v1/gateway/upload/batch \
    -H 'Content-Type: application/json' \
    --data-binary @"$f" && rm -f "$f"
done
```

The sample counter in `gateway/stats` counts recorded JSONL rows and may grow
from duplicate WAL retries. For the scheduler threshold, trust
`trajectory_store_pending`/Redis unique `rl:traj:*` sample IDs, not only
`total_samples`.
