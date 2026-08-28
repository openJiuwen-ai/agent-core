# Online RL Service Operations

AgentBox Adapter (AIGW) is the only production inference gateway. It owns the loopback RL Service process and exposes
the public Service, Task, Training Run, trajectory, and LoRA control APIs. The RL Service does not start an Agent, vLLM,
Judge, Redis, or a second production gateway.

## Prerequisites

- AIGW and agent-core must run on the same host.
- Redis must use a dedicated database or dedicated instance. Do not use `FLUSHDB`; delete only owned keys when doing
  manual maintenance.
- AIGW and the RL Service must see the same absolute `lora.root`. The PPO worker writes version directories below
  that root and vLLM reads those exact paths.
- The RL Service endpoint must listen on loopback. HMAC protects the public AIGW control routes; internal loopback
  callbacks are not public routes.

## RL Service Config

Save a production config such as `/etc/openjiuwen/rl-service.yaml`:

```yaml
listen_host: 127.0.0.1
listen_port: 18081
redis_url: redis://127.0.0.1:6379/8
trajectory_retention_seconds: 604800
model_id: Qwen3-8B
base_model_path: /models/Qwen3-8B
aigw_endpoint: http://127.0.0.1:18080
judge_endpoint: http://127.0.0.1:18082/v1/chat/completions
judge_model: Qwen3-8B-Judge
judge_api_key: EMPTY
judge_votes: 3
judge_retries: 2
judge_timeout: 30
lora_activation_timeout: 150
min_samples_for_training: 32
max_samples_per_run: 128
ppo_samples_per_step: 32
ppo_config_path: /etc/openjiuwen/ppo.yaml
nproc_per_node: 4
training_gpu_ids: "4,5,6,7"
lora_repository_path: /srv/online-rl/loras
record_dir: /var/lib/openjiuwen/online-rl/records
log_path: /var/log/openjiuwen/rl-service.log
log_max_bytes: 10485760
log_backup_count: 5
log_level: INFO
```

Set `lora_activation_timeout` higher than AIGW's `lora.operationTimeout` so AIGW can finish a multi-instance load or
rollback before the RL Service decides the Training Run result.

The AIGW config for the same model must include:

```json
{
  "lora": {
    "root": "/srv/online-rl/loras",
    "statePath": "/var/lib/aigw/lora-state.json",
    "operationTimeout": "60s"
  },
  "onlineRL": {
    "command": ["/opt/openjiuwen/bin/python", "-m", "openjiuwen.agent_evolving.agent_rl.online.service"],
    "configPath": "/etc/openjiuwen/rl-service.yaml",
    "endpoint": "http://127.0.0.1:18081",
    "hookTimeout": "30s",
    "hookRetries": 2,
    "drainTimeout": "120s",
    "controlTimeout": "300s"
  }
}
```

Configure `global.cryptoSock` and the existing AIGW key provider in production. Control signatures use
`hex(HMAC-SHA256(apiHmacKey, X-Timestamp + raw_request_body))`, where `X-Timestamp` is Unix milliseconds.

## Start And Stop

Start AIGW normally; do not start the RL Service separately:

```bash
/usr/local/bin/aigw --config=/etc/aigw/conf/aigw.json
```

Call signed `POST /v1/rl/service/start`, then check `GET /v1/rl/service`. A ready response has `status=running` and
means both `/health` and Redis were ready. Call signed `POST /v1/rl/service/stop` before stopping AIGW. Stop drains
Task captures, cancels an active Training Run, and terminates only the owned child process. AIGW also performs this
cleanup on normal shutdown.

RL Service logs are written to the configured `log_path` (`/var/log/openjiuwen/rl-service.log` above). AIGW logs
process start, readiness, abnormal exit, hook retries, drain failures, and LoRA operations in its normal log directory.
After an abnormal RL Service exit, `GET /v1/rl/service` reports `failed`; ordinary inference and LoRA control remain
available. Correct the cause and call Service start again. A Run interrupted in `queued` or `training` is marked failed
with `service_restarted` and its fixed samples return to pending; an `activating` Run retries idempotent activation.

## JiuwenSwarm Online Update Example

For a runnable Task, terminal-reward, Training Run, and policy-verification flow, see
[`examples/jiuwenrl_online`](../../examples/jiuwenrl_online/README.md). The example configures JiuwenSwarm inference to
use AIGW and keeps service deployment separate from per-interaction RL control.

## No-GPU System Verification

From agent-core, run:

```bash
bash tests/system_tests/agent_evolving/agent_rl/online/run_aigw_system.sh
```

The command uses a real AIGW binary from AgentBox Adapter, a real RL Service process, and a dedicated temporary Redis
Docker container. Only vLLM, Judge, and PPO are fake. Set `AIGW_REPO`, `AIGW_BIN`, or an explicitly isolated
`ONLINE_RL_REDIS_URL` to override defaults. The harness never runs `FLUSHDB`; it removes only the container it created
and explicitly terminates every process/server it owns. The Redis pause/recovery case is skipped when an external
Redis URL is supplied because the harness does not own that server. No GPU or real PPO installation is required.

## Manual PPO To LoRA Acceptance

1. Start Redis, real inference vLLM instances, the Judge, and AIGW. Register every vLLM instance for the configured
   `model_id`, then start the RL Service through AIGW.
2. Start terminal and delayed-feedback Tasks through AIGW. Send Agent inference with stable
   `X-Agent-Session-Id` and, for delayed feedback, `X-Agent-Turn-Id`. Stop the Tasks and submit terminal rewards.
3. Confirm trajectory stats and details contain prompt/completion token IDs, aligned log probabilities, usage,
   finish reason, tool calls when present, reward, and the captured policy version.
4. Explicitly `POST /v1/rl/training/runs`. Confirm `sample_count` and `policy_versions` are the fixed batch present at
   creation; samples arriving later must remain pending for the next Run.
5. Observe `queued -> training -> activating -> succeeded`. Verify the artifact is a new
   `/srv/online-rl/loras/<model>/vN` directory and all registered vLLM instances accepted `load_lora_adapter`.
6. Start a new Task and confirm inference uses `<model>:vN`. A Task started before activation must remain pinned to its
   old policy. Verify LoRA requests bypass prefix-cache routing.
7. Delete the active LoRA. Confirm new requests return to base immediately, pinned Tasks finish on the old version,
   and `unload_lora_adapter` occurs only after their leases drain.
8. Restart AIGW and confirm LoRA state recovers as `active` or explicitly `degraded`; never silently route a missing
   artifact. Stop the RL Service and confirm ordinary OpenAI/Anthropic inference and LoRA control still work.
