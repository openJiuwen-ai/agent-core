# SFT Transfer Flow

This directory contains the offline handoff flow for SFT optimization when
supervisor trajectory collection and GPU training run on different machines.

## Flow

1. On the collection machine, use `sft-optimize` / `run_sft_optimize.py` to run
   supervisor replay and upload direct `sft-sample-v1` samples to the local
   gateway. Do not trigger training on this machine.
2. Export pending SFT samples from the collection machine Redis into one JSON
   package.
3. Copy the JSON package to the training machine.
4. Import the package into the training machine gateway.
5. Trigger one manual training task on the training machine.

The training machine should run scheduler in manual mode:

```bash
export TRAIN_BACKEND=SFT
export ONLINE_RL_DRAIN_PENDING_ON_TRAIN=True
```

With this mode, imported samples stay pending until a training task is created
through `/v1/training/tasks` (compat alias: `/v1/training/tasks:trigger`).

## Script 1: Export Trajectories

Run on the collection machine:

```bash
python examples/jiuwenrl_online/sft_transfer/export_sft_samples.py \
  --redis-url redis://127.0.0.1:6379/0 \
  --user-id local-web-user \
  --status pending \
  --output /tmp/sft_samples_export.json
```

Output format:

```json
{
  "protocol_version": "sft-transfer-v1",
  "exported_at": "...",
  "source": {
    "redis_url": "redis://127.0.0.1:6379/0",
    "user_id": "local-web-user",
    "status": "pending",
    "limit": 100000
  },
  "samples": []
}
```

Only `samples` are used for training. Runtime Redis fields such as
`_store_status` are stripped during export.

## Script 2: Import Trajectories

Run on the training machine after copying the package:

```bash
python examples/jiuwenrl_online/sft_transfer/import_sft_samples.py \
  /tmp/sft_samples_export.json \
  --gateway-url http://127.0.0.1:18180 \
  --user-id local-web-user
```

The script uploads each item back through the existing gateway upload API:

```text
POST /v1/gateway/upload/batch
```

Each imported item is written as a pending `sft-sample-v1` sample. The import
step does not require supervisor LLM access or SWE Docker replay.

## Script 3: Trigger Training

Run on the training machine after import:

```bash
python examples/jiuwenrl_online/sft_transfer/trigger_sft_training.py \
  --gateway-url http://127.0.0.1:18180 \
  --user-id local-web-user \
  --sample-count 0 \
  --metadata '{"source":"sft-transfer"}'
```

`sample-count` is metadata for the task record. Scheduler still consumes the
actual pending samples from Redis according to its configured thresholds and
limits.

The script calls:

```text
POST /v1/training/tasks
```

`POST /v1/training/tasks` remains compatible and uses the same task creation
logic.

## Notes

- This flow transfers `sft-sample-v1`, not `sft-raw-v1`. The supervisor replay
  has already happened on the collection machine.
- Keep `user_id` consistent between export, import, and trigger, otherwise the
  scheduler may not find the imported pending samples.
- If gateway auth is enabled, set `TRAJECTORY_GATEWAY_API_KEY` or pass
  `--gateway-api-key`.
