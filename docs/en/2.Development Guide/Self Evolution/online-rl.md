# Online RL/SFT Backend Operations

This document supplements
[Online RL Service Operations](./online-rl-service-operations.md). It describes
the RL and SFT paths after JiuwenSwarm injects an Online Rail, and the SWE
rollout skill path for collecting high-quality SFT data.

For the Chinese version, see
[online-rl-service-operations-backends.zh.md](./online-rl-service-operations-backends.zh.md).

The injection entry point is:

```text
openjiuwen/harness/deep_agent.py
  -> openjiuwen/agent_evolving/agent_rl/online/core/rail_factory.py
```

## Start Here

Choose one path before setting environment variables. The three paths have
different sample contracts and upload timing.

| Goal | Rail/output | Upload timing | Training trigger |
| --- | --- | --- | --- |
| Improve an online policy with PPO | `RLOnlineRail` / `rail-v1` | Each completed PPO turn | `POST /v1/rl/training/runs` |
| Fine-tune from an ordinary JiuwenSwarm session | `SFTOnlineRail` / `sft-raw-v1` or `sft-sample-v1` | When the SFT session flushes | SFT-aware Scheduler/launcher |
| Fine-tune from verified SWE rollouts | `SFTOnlineRail` plus skill cleanup / `sft-sample-v1` | Only after cleanup and `resolved=true` | SFT-aware Scheduler/launcher |

## 1. Shared Rail Injection and Boundaries

`DeepAgent` calls `build_online_training_rail_from_env()` during creation. An
Online Rail is injected only when this switch is enabled:

```bash
export USE_RL_ONLINE_RAIL=1
```

The Rail factory selects an implementation using the following rules:

| Configuration | Injected Rail |
| --- | --- |
| `TRAIN_BACKEND=PPO`, `RL`, or `online_rl` | `RLOnlineRail` |
| `TRAIN_BACKEND=SFT` | `SFTOnlineRail` |
| `TRAIN_BACKEND` is unset and `RL_ONLINE_CAPTURE_MODE` is `raw`, `raw_session`, or `sft_raw` | `SFTOnlineRail` |
| All other cases | `RLOnlineRail` |

Both Rails use `TrajectoryUploader` to call:

```text
POST <TRAJECTORY_GATEWAY_URL>/v1/gateway/upload/batch
```

`TRAJECTORY_GATEWAY_URL` must be reachable from the process running
JiuwenSwarm. A Docker container, sidecar, or remote sandbox cannot use the
host-only `127.0.0.1` address directly. Use a reachable host address, a
container-network service name, or the sandbox upstream reverse proxy.

The principal shared environment variables are:

| Variable | Purpose |
| --- | --- |
| `USE_RL_ONLINE_RAIL` | Enables automatic Online Rail injection for `DeepAgent`. |
| `TRAIN_BACKEND` | Selects the `PPO`/`RL` or `SFT` Rail. |
| `RL_ONLINE_CAPTURE_MODE` | Compatibility mode selector; `raw`, `raw_session`, and `sft_raw` select the SFT Rail. |
| `TRAJECTORY_GATEWAY_URL` | Gateway address. |
| `TRAJECTORY_GATEWAY_API_KEY` | Gateway Bearer token. |
| `RL_ONLINE_TENANT_ID` | Stable tenant/user namespace for trajectories, samples, and LoRAs. |
| `LORA_DEFAULT_POLICY` | `latest_by_user` requests the current active LoRA for the user. |
| `TRAJECTORY_UPLOAD_TIMEOUT_SECONDS` | HTTP upload timeout. |
| `TRAJECTORY_WAL_DIR` | Local WAL directory for failed uploads. |
| `TRAJECTORY_FORCE_WAL` | When `1`, writes only to WAL and does not upload. It must remain disabled for end-to-end validation. |

The standalone `online.service` currently wires a PPO executor into
`/v1/rl/training/runs`. Setting `TRAIN_BACKEND=SFT` does not automatically turn
that endpoint into a generic SFT training API. SFT training must be launched by
an SFT-aware Gateway, Scheduler, or launcher that calls `SFTTrainingExecutor`.

### 1.1 Deployment Preflight

Before starting any of the three paths, verify the following:

1. The process running JiuwenSwarm can reach `TRAJECTORY_GATEWAY_URL`.
2. `RL_ONLINE_TENANT_ID` is set to the stable namespace used by the target
   model, sample store, and LoRA repository.
3. The Gateway understands the protocol that will be uploaded:
   `rail-v1` for PPO, and `sft-raw-v1` or `sft-sample-v1` for SFT.
4. `TRAJECTORY_FORCE_WAL` is disabled unless the rollout explicitly uses WAL
   as a local staging area.
5. The selected training trigger matches the sample type. A PPO Training Run
   cannot consume SFT samples, and an SFT launcher cannot consume `rail-v1`
   PPO samples.

## 2. JiuwenSwarm with the RL/PPO Training Backend

### 2.1 Flow

The RL backend captures turn-level samples suitable for PPO. Plain
`prompt`/`response` text alone is insufficient.

```text
JiuwenSwarm / DeepAgent
  -> RLOnlineRail
  -> collect messages, token ids, logprobs, and usage from model calls and observability spans
  -> rail-v1
  -> TrajectoryUploader
  -> Gateway
  -> Redis pending PPO samples
  -> API creates a Training Run
  -> PPOTrainingExecutor / veRL-RL
  -> export and publish LoRA
  -> AIGW / inference service activates LoRA
```

`RLOnlineRail` adds user identity and sampling requirements before a model
call. After the call, it reconstructs the model exchange from callbacks or an
observability span. A valid PPO sample must include at least:

- prompt messages and prompt token ids;
- completion token ids;
- log probabilities aligned with the completion;
- usage, finish reason, model identity, and session identity;
- task/session data needed to associate a reward.

The Gateway PPO ingestion path rejects samples without token or log-probability
ground truth. An ordinary SFT conversation cannot be used directly for PPO
training.

Training is explicitly started through an API, not implicitly after every
trajectory upload:

```text
POST /v1/rl/training/runs
  -> atomically claim the current pending sample batch
  -> PPOTrainingExecutor
  -> PPOBatchEngine / Ray / veRL
  -> checkpoint and LoRA
  -> LoRARepository.publish()
  -> AIGWLoRAClient.activate()
```

Trajectories uploaded after the Training Run is created remain in the next
pending batch and cannot be mixed into the claimed run.

### 2.2 Minimum Configuration

```bash
export USE_RL_ONLINE_RAIL=1
export TRAIN_BACKEND=PPO
export TRAJECTORY_GATEWAY_URL=http://gateway-host:18080
export RL_ONLINE_TENANT_ID=rl-user
export LORA_DEFAULT_POLICY=latest_by_user
export RL_ONLINE_SESSION_DONE_ON_INVOKE_END=1
```

`RL_ONLINE_SESSION_DONE_ON_INVOKE_END=1` is appropriate when PPO samples are
collected per invoke or turn. For sessions that span multiple invokes, the
business callback should explicitly set `session_done`, `close_session`, or
`done` at completion.

### 2.3 RL/PPO Training Resource Variables

These variables override PPO training resources configured in YAML:

| Variable | Purpose |
| --- | --- |
| `ONLINE_RL_VISIBLE_DEVICES_ENV` | Name of the visible-device variable. Defaults to `CUDA_VISIBLE_DEVICES`; set it to the appropriate NPU variable for Ascend. |
| `ONLINE_RL_DEVICE_BACKEND` | Selects the NPU path when set to `ascend` or `npu`. |
| `ONLINE_RL_INIT_LORA_ADAPTER_PATH` | Initial LoRA adapter override. |
| `ONLINE_RL_MAX_PROMPT_LENGTH` | PPO maximum prompt tokens. |
| `ONLINE_RL_MAX_RESPONSE_LENGTH` | PPO maximum completion tokens. |
| `ONLINE_RL_TRAIN_BATCH_SIZE` | PPO train batch size. |
| `ONLINE_RL_PPO_MINI_BATCH_SIZE` | PPO mini-batch size. |
| `ONLINE_RL_PPO_MICRO_BATCH_SIZE_PER_GPU` | PPO micro-batch size per device. |
| `ONLINE_RL_SEQUENCE_PARALLEL_SIZE` | Sequence/tensor parallel size. |
| `ONLINE_RL_ACTOR_PPO_MAX_TOKEN_LEN_PER_GPU` | Actor token limit per device. |
| `ONLINE_RL_ROLLOUT_MAX_MODEL_LEN` | Maximum rollout context length. |
| `ONLINE_RL_ROLLOUT_GPU_MEMORY_UTILIZATION` | Rollout-side memory utilization. |
| `ONLINE_RL_ACTOR_LEARNING_RATE` | Actor learning rate. |

Service-level configuration, such as model paths, Redis, LoRA repository,
training GPUs, minimum sample count, and the AIGW endpoint, remains in the YAML
file referenced by `RL_SERVICE_CONFIG`.

## 3. JiuwenSwarm with the SFT Training Backend

### 3.1 Flow

The SFT backend uses OpenAI-style structured `messages`, tool calls, and
assistant output. It does not reduce an entire session to one concatenated
string.

```text
JiuwenSwarm / DeepAgent
  -> SFTOnlineRail
  -> collect structured messages, tool calls, assistant response, and task metadata
  -> session flush
  -> TrajectoryUploader directly uploads to Gateway
  -> GatewayTrajectoryRuntime
  -> sft-raw-v1    -> SFT raw store    -> SFT rollouter -> sft-sample-v1
  -> sft-sample-v1 -> SFT sample store
  -> SFTTrainingExecutor / veRL SFT
  -> LoRARepository.publish()
```

“Direct upload” means that `SFTOnlineRail` calls the Gateway when the session
flushes. It does not use the PPO reward/trajectory training path. The Gateway
must route SFT protocols to:

- `GatewayTrajectoryRuntime.record_sft_raw()` for `sft-raw-v1`;
- `GatewayTrajectoryRuntime.record_sft_sample()` for `sft-sample-v1`.

The standalone `/v1/gateway/upload/batch` route in `online.service` is mainly
for `rail-v1` PPO ingestion. An SFT deployment must verify that its external
Gateway is wired to the SFT runtime above; a working PPO route alone does not
prove that SFT raw/sample data is being persisted.

In particular, the current standalone route calls
`trajectory_api.rail_ingestor.ingest_rail_batch(payload)`. Although
`GatewayTrajectoryRuntime.batch_create_trajectories()` supports SFT protocols,
that method is not used by this standalone route. The external Gateway must
perform the protocol dispatch explicitly.

### 3.2 Upload Modes and Flush Rules

| `SFT_ONLINE_UPLOAD_MODE` | Gateway data | Intended use |
| --- | --- | --- |
| `raw` | `sft-raw-v1` | Preserve the original session, then use a supervisor/replay rollouter to create a teacher answer. |
| `sample` | `sft-sample-v1` | The current call already produced a trainable assistant teacher answer, so the sample can enter the SFT sample store directly. |

SFT flushes a session in these conditions:

| Condition | Behavior |
| --- | --- |
| `RL_ONLINE_SESSION_DONE_ON_INVOKE_END=1` | Upload the current session at the end of every invoke. |
| The callback context contains `session_done`, `close_session`, or `done` | Explicitly end and upload the session. |
| `TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K>0` and the threshold is reached | Flush on the token threshold. |

Normal SFT sessions do not finish after each invoke by default and may collect
multiple turns. With `RL_ONLINE_SESSION_DONE_ON_INVOKE_END=1`, each business
invoke is uploaded as a separate record rather than one complete multi-turn
session.

### 3.3 Minimum Configuration

Direct raw-session upload:

```bash
export USE_RL_ONLINE_RAIL=1
export TRAIN_BACKEND=SFT
export SFT_ONLINE_UPLOAD_MODE=raw
export TRAJECTORY_GATEWAY_URL=http://gateway-host:18080
export RL_ONLINE_TENANT_ID=sft-user
export SFT_SCENARIO=multi_turn_supervisor
export SFT_TASK_PROMPT="implement the requested change"
```

Direct upload of a trainable sample:

```bash
export USE_RL_ONLINE_RAIL=1
export TRAIN_BACKEND=SFT
export SFT_ONLINE_UPLOAD_MODE=sample
export TRAJECTORY_GATEWAY_URL=http://gateway-host:18080
export RL_ONLINE_TENANT_ID=sft-user
export RL_ONLINE_SESSION_DONE_ON_INVOKE_END=1
```

### 3.4 Main SFT Environment Variables

| Variable | Purpose |
| --- | --- |
| `SFT_ONLINE_UPLOAD_MODE` | Selects the `raw` or `sample` upload protocol. |
| `SFT_SCENARIO` | Labels the SFT data source or scenario, such as `multi_turn_supervisor`. |
| `SFT_TASK_PROMPT` | Task prompt metadata. |
| `SFT_INSTANCE_ID` | SWE or dataset case ID. |
| `SFT_DOCKER_IMAGE` | Task-image metadata. |
| `SFT_DATASET_CASE_JSON` | Dataset-case JSON metadata. |
| `SFT_WORKSPACE_REF_JSON` | Workspace/repository metadata. |
| `SFT_SUPERVISOR_TIMEOUT` | Supervisor request timeout. |
| `SFT_TASK_UPLOAD_SETTLE_SECONDS` | Seconds to wait for asynchronous upload after task completion. |
| `SFT_ROLLOUT_CONCURRENCY` | Supervisor/task rollout concurrency. |
| `SFT_VERL_MAX_LENGTH` | Maximum SFT training sequence length. |
| `SFT_VERL_TRUNCATION` | Overlength-sample truncation direction. |
| `SFT_VERL_WINDOW_LENGTH` | Generates turn-based sliding windows when greater than zero. |
| `SFT_VERL_WINDOW_OVERLAP_TURNS` | Turn overlap between adjacent windows. |
| `SFT_VERL_SUPERVISE` | Supervision scope, for example `last` or all assistant turns. |
| `SFT_VERL_TRAIN_BATCH_SIZE` | SFT train batch size. |
| `SFT_VERL_MICRO_BATCH_SIZE_PER_GPU` | SFT micro-batch size per device. |
| `SFT_VERL_LORA_RANK` | LoRA rank. |
| `SFT_VERL_LORA_ALPHA` | LoRA alpha. |
| `SFT_VERL_TARGET_MODULES` | LoRA target modules. |
| `SFT_VERL_DEVICE` | veRL SFT device, such as `cuda` or `npu`. |
| `SFT_VERL_NNODES`, `SFT_VERL_NODE_RANK`, `SFT_VERL_MASTER_ADDR`, `SFT_VERL_MASTER_PORT` | Multi-node `torchrun` configuration. |

The SFT and PPO trainers share `ONLINE_RL_VISIBLE_DEVICES_ENV`. An Ascend
deployment can use the actual NPU visible-device variable:

```bash
export ONLINE_RL_VISIBLE_DEVICES_ENV=ASCEND_RT_VISIBLE_DEVICES
export SFT_VERL_DEVICE=npu
```

## 4. SFT Fine-Tuning with the SWE Rollout Skill

### 4.1 Target Flow

The SWE rollout skill replays SWE cases into verified supervisor trajectories
and then sends accepted data to SFT. Its quality gate is strict: **only cases
with `resolved=true` may be uploaded to the Gateway and added to the training
set.**

```text
SWE dataset mapping
  -> rollout skill reads case, image, and commit data
  -> starts an isolated rollout environment per case
  -> JiuwenSwarm completes the task with the Supervisor model
  -> collects session, tool calls, patch, and execution result
  -> data cleanup and case evaluator
  -> resolved=true
  -> normalize as sft-sample-v1
  -> Gateway / RedisSFTStore
  -> API triggers SFT training
  -> veRL SFT exports LoRA
```

Cases with `resolved=false`, timeouts, startup failures, incomplete
trajectories, unparsable assistant messages, or cleanup failures may be kept
only as diagnostic logs or troubleshooting artifacts. They must not enter the
SFT sample store or be claimed by this training run.

### 4.2 Data Cleanup and Quality Gate

Before upload, the skill must:

1. Read the case instance ID, repository, base commit, task description, and
   test information from the dataset mapping.
2. Collect structured supervisor messages, tool calls, final assistant output,
   code changes, and verification results.
3. Remove runtime-control logs, container startup logs, secrets, irrelevant
   stdout/stderr, and content that cannot be represented as messages.
4. Normalize the data into OpenAI-style roles, content, and tool calls while
   retaining stable `user_id`, `session_id`, `instance_id`, and model metadata.
5. Verify that supervised assistant content exists, then apply target-tokenizer
   length accounting and the configured `SFT_VERL_MAX_LENGTH` truncation or
   skip policy.
6. Run the case evaluator. Generate and upload `sft-sample-v1` only when the
   evaluator explicitly returns `resolved=true`.

This path must not automatically upload before the evaluator reports a result.
The rollout collection phase must keep all flush triggers disabled:

```bash
export RL_ONLINE_SESSION_DONE_ON_INVOKE_END=0
export TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K=0
```

The skill must also avoid passing `session_done`, `close_session`, or `done` to
JiuwenSwarm before evaluation. Those callback values explicitly flush
`SFTOnlineRail` even when `RL_ONLINE_SESSION_DONE_ON_INVOKE_END=0`.

The current Docker task-rollout default also sets
`RL_ONLINE_SESSION_DONE_ON_INVOKE_END=1` and finishes a chat request with
`session_done=True` and `close_session=True`. Used as-is, it uploads every
completed rollout before evaluation and therefore does **not** meet the
resolved-only requirement. A SWE skill must override that environment and
completion behavior, or use a local staging mechanism such as WAL and upload
only the accepted payload after evaluation.

After `resolved=true` and successful cleanup, the skill explicitly finishes
the session or uses its Gateway client to upload the final `sft-sample-v1`.
Unresolved cases must neither flush the session to Gateway nor issue a direct
upload request.

### 4.3 Supervisor and Rollout Environment Variables

JiuwenSwarm in the rollout should use the supervisor model, not the default
business-agent model. The skill normally sets these variables in its child
process:

| Variable | Purpose |
| --- | --- |
| `SUPERVISOR_URL` | OpenAI-compatible Supervisor endpoint. |
| `SUPERVISOR_TOKEN` | Supervisor authentication token. |
| `SUPERVISOR_MODEL` | Supervisor model name. |
| `API_BASE` | Model endpoint actually used by JiuwenSwarm; normally derived from `SUPERVISOR_URL`. |
| `API_KEY` | Model token actually used by JiuwenSwarm; normally derived from `SUPERVISOR_TOKEN`. |
| `MODEL_NAME` | Model name actually used by JiuwenSwarm; normally derived from `SUPERVISOR_MODEL`. |
| `SFT_ROLLOUT_CONCURRENCY` | Number of concurrently replayed cases. |
| `SFT_DOCKER_ROLLOUT_TIMEOUT` | Docker/SWE rollout timeout per case. |
| `SFT_DOCKER_ROLLOUT_COMMAND` | Command run by JiuwenSwarm inside a task container. |
| `SFT_DOCKER_ROLLOUT_DEBUG_LOG` | Enables rollout stdout/stderr logs. |
| `SFT_TASK_CWD` | JiuwenSwarm task working directory. |
| `SFT_TASK_MODE` | JiuwenSwarm task mode. |
| `SFT_TASK_MAX_ITERATIONS` | Maximum iterations per task. |
| `SFT_TASK_CHAT_TIMEOUT` | Chat completion timeout per task. |
| `SFT_LOCAL_REPO_WORK_ROOT` | local_program rollout workspace root. |
| `SFT_LOCAL_PROGRAM_SOURCE_DIR` | local_program source/problem directory. |

Whether rollout executes in Docker, local_program, a sidecar, or a remote
sandbox, these settings must be present in the process that executes
JiuwenSwarm:

```bash
export USE_RL_ONLINE_RAIL=1
export TRAIN_BACKEND=SFT
export SFT_ONLINE_UPLOAD_MODE=sample
export TRAJECTORY_GATEWAY_URL=http://gateway-reachable-from-rollout:18080
export RL_ONLINE_TENANT_ID=<stable-training-user>
export RL_ONLINE_SESSION_DONE_ON_INVOKE_END=0
export TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K=0
```

### 4.4 Training Trigger and Acceptance

The SWE skill is responsible for collection, cleanup, evaluation, and upload.
Training is still explicitly started through an API. Before calling that API,
confirm that:

1. The Gateway has received the expected number of `sft-sample-v1` samples.
2. Every sample participating in the run comes from a `resolved=true` case.
3. `user_id`, target model, and LoRA namespace match the training request.
4. The SFT scheduler/launcher selects `SFTTrainingExecutor`, not the PPO
   executor.
5. The tokenizer uses the actual training `base_model_path`.
6. `SFT_VERL_MAX_LENGTH`, sequence parallelism, and micro-batch size fit the
   available GPU/NPU memory.

After training, validate the checkpoint, merged LoRA adapter, LoRA repository
metadata, and inference-service load state. A Gateway trajectory upload alone
does not prove that SFT training or LoRA publication completed.

## 5. Troubleshooting

### 5.1 Rail Was Not Injected

Check:

```bash
echo "$USE_RL_ONLINE_RAIL"
echo "$TRAIN_BACKEND"
python -c \
  "from openjiuwen.agent_evolving.agent_rl.online.core.rail_factory import build_online_training_rail_from_env"
```

Set `USE_RL_ONLINE_RAIL=1` and explicitly set either `TRAIN_BACKEND=PPO` or
`TRAIN_BACKEND=SFT`.

### 5.2 Trajectories Were Not Uploaded

Check in this order:

1. Use `curl` inside the rollout/container/sandbox to reach the actual
   `TRAJECTORY_GATEWAY_URL`.
2. Verify that `TRAJECTORY_FORCE_WAL` is not `1`.
3. Inspect `TRAJECTORY_WAL_DIR` for failed upload records.
4. For RL, verify that token ids and logprobs are complete.
5. For SFT, verify that the Gateway is wired to `record_sft_raw()` or
   `record_sft_sample()`.
6. For normal SFT, verify the flush condition. For the SWE skill, verify that
   all automatic and explicit flush triggers stay disabled until the evaluator
   returns `resolved=true`.
7. Verify that `RL_ONLINE_TENANT_ID` is stable and non-empty.

### 5.3 SFT Samples Exist but Training Did Not Start

Verify that the request is not being sent to a standalone RL Service that only
wires a PPO executor. Also verify:

1. The SFT sample store has pending `sft-sample-v1` data.
2. The SFT scheduler/launcher selects the SFT backend.
3. `base_model_path` and the tokenizer point to the same target model.
4. The veRL Python environment, device variables, and distributed variables
   are consistent.
5. Overlength sample truncation/skip logs match the configured policy.
