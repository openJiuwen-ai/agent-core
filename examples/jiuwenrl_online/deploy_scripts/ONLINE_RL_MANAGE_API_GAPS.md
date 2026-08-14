# Online RL Gateway Management API Handoff

This note records the management API work aligned with:

- `agentic_rl_design_opt.md`
- `agentic_rl_api.md`

The implementation intentionally follows the current code structure:

- Trajectories are stored as scored training samples in `RedisTrajectoryStore`.
- LoRA versions are stored as `LORA_REPO_ROOT/<model_id-or-user_id>/vN`.
- The gateway process exposes management routes directly; there is no separate Trajectory Manager or LoRA Repo service process yet.

## Implemented Gateway APIs

Health:

- `GET /v1/rl/health`

Trajectory management:

- `POST /v1/rl/trajectories:batchCreate`
- `GET /v1/rl/trajectories`
- `GET /v1/rl/trajectories/stats`
- `GET /v1/rl/trajectories/{trajectory_id}`
- `PATCH /v1/rl/trajectories/{trajectory_id}`
- `DELETE /v1/rl/trajectories/{trajectory_id}`

LoRA version management:

- `GET /v1/rl/lora`
- `GET /v1/rl/lora/latest?model_id=...`
- `GET /v1/rl/lora/{lora_id}`
- `POST /v1/rl/lora`
- `POST /v1/rl/lora/{lora_id}:setLatest`
- `GET /v1/rl/lora/{lora_id}:download`
- `DELETE /v1/rl/lora/{lora_id}`

`lora_id` currently uses:

```text
<model_id-or-user_id>:<version>
```

Example:

```text
manage-user:v1
```

## Validation Script

Start the services:

```bash
bash examples/jiuwenrl_online/deploy_scripts/start_online_rl_services.sh
```

Run the API smoke test:

```bash
bash examples/jiuwenrl_online/deploy_scripts/test_manage_api.sh
```

The script creates synthetic trajectory and LoRA records, validates CRUD behavior, and does not trigger training or consume GPU.

## Data Mapping

`POST /v1/rl/trajectories:batchCreate` accepts the design-doc style `agent-rollout-v1` shape and converts each LLM step into the current training sample shape.

Required fields for a trainable LLM step:

- `trajectory_id`
- `session_id`
- `user_id` or top-level `user_id` / `tenant_id`
- `steps[].request.messages`
- `steps[].response.content`
- `steps[].token_trace.response_ids`

Optional fields are preserved where current sample storage has a natural place:

- `task_id`
- `source`
- `policy_version`
- `metadata`
- `reward.score`

Reserved fields not fully implemented yet:

- `rollout_id` is stored but not indexed.
- `base_model` is not separately indexed for trajectory queries.
- `reward.details` is stored under `judge.details` when present.
- Detailed audit fields such as operator, request id, tenant id, and review status are accepted through `metadata` but not indexed.

## Design Gaps For Future Agents

The following APIs or semantics are described in the design docs but are not fully implemented in this stack yet.

1. Separate Trajectory Manager service

Current gateway writes directly to Redis through `RedisTrajectoryStore`. Future work can split the management methods from `storage/redis_trajectory_store.py` behind an internal REST service:

- `POST /v1/internal/trajectories:batchCreate`
- `POST /v1/internal/trajectories:leaseForTraining`
- `POST /v1/internal/trajectories:markTrained`
- `POST /v1/internal/trajectories:releaseLease`

2. Training job management API

The scheduler currently runs as a polling process and does not expose the full design-doc job lifecycle through the gateway. Missing external APIs:

- `POST /v1/rl/train-jobs`
- `GET /v1/rl/train-jobs`
- `GET /v1/rl/train-jobs/{job_id}`
- `POST /v1/rl/train-jobs/{job_id}:stop`

Pause/resume are design-reserved. If implemented, they need trainer checkpoint coordination and trajectory lease release semantics.

3. LoRA hot-load management from gateway

The current gateway management API manages repo metadata and files. Runtime loading still happens from the scheduler/notifier path or vLLM API. Missing or reserved APIs:

- `POST /v1/rl/lora/{lora_id}:load`
- `POST /v1/rl/lora/{lora_id}:unload`

If added, the gateway needs an inference notifier configured with the upstream vLLM URL and should update `load_status`.

4. Download packaging

`GET /v1/rl/lora/{lora_id}:download` currently returns local path metadata. Future object-store or file-stream implementation should return one of:

- A tar/zip stream containing adapter files and metadata.
- A short-lived signed URL.

5. Pagination and indexing

Current trajectory list supports a simple `limit` and filters in process. Future production use should add:

- Cursor pagination.
- Redis indexes for `task_id`, `source`, `policy_version`, `model_id`, and time ranges.
- Reward range indexes if reward filtering is needed.

6. Stronger state validation

Current management patch allows status changes needed for operational repair. Future stricter state machine should enforce:

- `pending -> training`
- `training -> trained`
- `training -> pending`
- `pending/training -> failed`
- `pending/trained/failed -> deleted`

7. Idempotency and duplicate reporting

`batchCreate` is idempotent at the Redis key level because `sample_id` is stable, but the response does not yet distinguish `duplicate` from accepted overwrite. Future implementation should check existing hash status before save and report duplicates without overwriting `training` or `trained`.

8. Auth and audit

`GATEWAY_API_KEY` bearer auth is supported. Full audit logging fields from the API doc are not yet persisted:

- `request_id`
- `operator`
- `audit_action`
- `before` / `after`

These can be added as structured logs first, then persisted if the management plane needs history queries.
