# SFT Online Training Debug Task

- 2026-07-30: After GPU 3 fell off, current container PyTorch reports `device_count=0` even with `CUDA_VISIBLE_DEVICES=0,1` and `CUDA_VISIBLE_DEVICES=4,5,6,7`. vLLM cannot start until CUDA visibility is isolated from the bad device, likely by using a fresh container/runtime that only exposes healthy GPUs or by host-level GPU reset.
## Current Objective

Debug the `agentos-v2-dev` branch SFT online training flow end to end.

Target flow:

1. Start SFT backend services: gateway and scheduler.
2. Use `/data1/lll/workspace/sft_train_demo/` as the SWE Bench demo input source.
3. Run 2 SWE cases only. Each case should run in its own Docker container.
4. The SWE container should use the same `jiuwenswarm` and `agent-core` code as the `openjiuwen-rl` environment.
5. Use the implemented `SFTOnlineRail` to collect string trajectory data, including at least:
   - case Docker image
   - original task prompt
   - session-level string trajectory
6. After collecting trajectories, call an API to trigger an async training task.
7. The scheduler should use a rollouter configured at startup.
8. The rollouter should replay the task prompt in the recorded Docker image and collect a new string trajectory.
9. For this debugging round, the supervisor model can reuse the same vLLM/model as step 2 instead of a stronger external model.
10. Train LoRA weights from the rollouter trajectories using the SFT + SP + LoRA training path.

## Hard Constraints

- Do not modify `jiuwenswarm` code for this validation.
- Use the `openjiuwen-rl` conda environment unless explicitly changed.
- Because `jiuwenswarm` runs in other Docker containers, service URLs should use the current container host IP, for example the `172.*` IP of `lll-rl-dev-152`, instead of localhost.
- If trajectories are too long or training OOMs, truncation is allowed for this debug flow, preferably capped at 32k first.
- Keep gateway/scheduler config pointed at services started in this task.
- Keep this file updated whenever progress, blockers, or user feedback affects debugging decisions.

## User Feedback Log

- 2026-07-30: Added SFTOnlineRail env fallback for Docker-case metadata: `SFT_DATASET_CASE_JSON`, `SFT_DOCKER_IMAGE`, `SFT_INSTANCE_ID`, `SFT_TASK_PROMPT`, and `SFT_WORKSPACE_REF_JSON`, so jiuwenswarm source does not need changes for scenario 2-1 collection.
- 2026-07-30: If the user gives any mid-task feedback that helps debugging, record it in this file to avoid repeating mistakes after interruption or restart.
- 2026-07-30: GPU 3 has been unstable / fallen off before. Use GPU `0,1` for vLLM and reserve GPU `4,5,6,7` for training. Avoid GPU 3 in this task.
- 2026-07-30: `openjiuwen-rl` does not directly import `jiuwenswarm` without a source path. Use `PYTHONPATH=/data1/lll/workspace/openjiuwen/code-opt/agent-core:/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw` for host-side validation, and mount/set equivalent paths for Docker rollout containers.
- 2026-07-30: VLLM should keep using the normal `online_rl_backend.sh` / `openjiuwen-rl` startup path in the current environment. Do not switch VLLM itself to a Docker startup path; Docker GPU checks are only diagnostic.
- 2026-07-30: Scenario 2-1 rollout Docker containers should package/run the SWE case and jiuwenswarm process only. They do not need GPU; vLLM and SFT training remain separate local backend processes.
- 2026-07-30: Scenario 2-1 rollout should mount host `agent-core`, `jiuwenclaw`, and `/data1/lll/miniconda3` into the SWE Docker container at the same absolute paths, then activate `openjiuwen-rl` inside the container before launching jiuwenswarm.
- 2026-07-30: For scenario 2-1, use an outer task rollouter to read the case table, resolve each case's Docker image, and pass `SFT_DOCKER_IMAGE`, `SFT_INSTANCE_ID`, `SFT_TASK_PROMPT`, and `SFT_DATASET_CASE_JSON` into the SWE container. The in-container jiuwenswarm can then add image/prompt metadata to raw SFT trajectories through `SFTOnlineRail`.
- 2026-07-30: Earlier debugging used an environment-controlled `sitecustomize` patch to register `SFTOnlineRail`; this is now superseded. Current direction is to keep jiuwenswarm/jiuwenclaw source unchanged and inject through agent-core `DeepAgent` env autoload.
- 2026-07-30: If a jiuwenswarm path bypasses core rail callbacks, fix the agent-core integration path first. Avoid reintroducing instance-level invoke monkey patches unless explicitly approved.
- 2026-07-30: `sft-raw-v1.created_at` must be an ISO datetime string. A float timestamp is rejected by the Redis-backed gateway path through `datetime.fromisoformat`.
- 2026-07-30: Original `sft-raw-v1` trajectories are rollout inputs, not final SFT training data. Redis SFT raw status should be `pending -> processing -> processed/failed`; only `sft-sample-v1` records use `pending -> training -> trained/failed`.
- 2026-07-30: Upload should stay on one API, `/v1/gateway/upload/batch`. Gateway should only validate/store raw trajectory payloads; scheduler/backend consumption decides whether the raw trajectory enters RL reward/training or SFT rollout/training, and status updates should reflect that consumption path.
- 2026-07-30: Do not add a separate SFT upload API. The same raw upload endpoint should accept rail/session trajectory payloads; backend-specific behavior is selected later by scheduler config/status, not by upload API shape.
- 2026-07-30: Mock E2E debugging found two concrete script issues: Docker service URL auto-detection was over-escaped and produced `127.0.0.1`; scheduler rollout also pointed `SFT_DOCKER_JIUWENCLAW_HOST_PATH` at missing `/data1/lll/workspace/openjiuwen/code-opt/jiuwenclaw`. Fixed both by detecting the `172.*` address and resolving the real jiuwenclaw path in the common script.
- 2026-07-30: Correct jiuwenclaw source path for this debug environment is `/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw`, not `/data1/lll/workspace/openjiuwen/jiuwenclaw`. Mock E2E should prefer the refactor path and export it through both `PYTHONPATH` and `SFT_DOCKER_JIUWENCLAW_HOST_PATH`.
- 2026-07-30: The original env-controlled online rail path loaded `RLOnlineRail`. Adapt that same agent-core injection point so `USE_RL_ONLINE_RAIL=1` plus `TRAIN_BACKEND=SFT` loads `SFTOnlineRail`; keep jiuwenswarm/jiuwenclaw source unchanged.
- 2026-07-30: Split debugging into three explicit stages:
  1. `task_rollouter` stage: read task prompt and Docker image, launch the SWE container with mounted jiuwenswarm and agent-core, execute the task, and verify the SWE behavior/patch plus raw trajectory metadata and normal LLM output.
  2. Training task + scheduler rollouter stage: after stage 1 raw uploads are correct across agent instances, call the training API, verify the scheduler consumes the raw trajectory, recovers the same task prompt/image, launches the rollouter correctly, collects a different supervisor trajectory for the same task, and updates original raw vs rollout raw statuses differently.
  3. Trainer + LoRA stage: after rollout samples are correct, invoke the real SFT backend to generate LoRA, publish/hot-load it, and verify subsequent trajectories record the expected LoRA version. While GPU is unavailable, this stage should use mock/dry-run training but still validate the data contract.
- 2026-07-30: Each of the three debug stages should have its own runnable script, not only a single full-chain wrapper. Current scripts are `run_sft_mock_stage1_raw.sh`, `run_sft_mock_stage2_rollout.sh`, and `run_sft_mock_stage3_train.sh`.
- 2026-07-31: Current validation scope is narrowed to raw trajectory collection only. Do not trigger the supervisor rollout or SFT training path for this pass.
- 2026-07-31: Do not use MiniMax for this debug pass. The intended model backend is Qwen3 through vLLM.
- 2026-07-31: The primary goal is correct SWE task dispatch and execution environment: `task_rollouter` must pass the real SWE prompt/image into the case Docker container, mount the current `agent-core` and `jiuwenclaw` sources, activate the expected conda environment, and let jiuwenswarm run inside the SWE case normally.
- 2026-07-31: Server was restarted and GPU 3 is visible again, but keep avoiding GPU 3. For the next real E2E validation use GPU `0,1` for Qwen3 vLLM and GPU `4,5,6,7` for real SFT LoRA training. Do not use mock training. Scope is one SWE task, no concurrency. Target chain: task_rollouter raw upload -> API training task -> scheduler Docker rollouter supervisor replay -> SFT samples -> LoRA training -> publish and hot-load into vLLM -> verify LoRA is effective.
- 2026-07-31: When debugging the real E2E chain, refer back to the previous staged debugging notes in this file. The control-plane flow should already be functional; likely issues are service reachability, resource/device binding, trajectory status movement, training length/OOM, and LoRA publish/load verification.
- 2026-07-31: For step 3/4 of the real E2E flow, persist supervisor replay raw trajectories to disk first, then build the final SFT training samples from that artifact. Keep the raw artifact path in sample metadata so the training input can be traced back to the replay output.
- 2026-07-31: verl SFT dataset has a hard schema expectation: if a sample starts with `system`, the first supervised turn must be `system -> user -> assistant`. Merge any extra pre-assistant user/reminder messages into the first user turn before writing parquet.
- 2026-07-31: pyarrow parquet writing can fail on empty nested structs such as message `metadata={}`. Strip empty dict fields before parquet export, but keep the JSON source artifact unchanged for debugging.
- 2026-07-31: Real SFT + SP=4 + LoRA training succeeded from persisted supervisor replay data. The trainer produced FSDP shards, then LoRA tensors were exported into a standard PEFT adapter and successfully hot-loaded into vLLM.
- 2026-07-31: Updated direction: do not persist supervisor replay trajectories in the normal scheduler path. Scheduler should consume the supervisor replay raw trajectory directly and pass the converted SFT samples to training. Artifact persistence is now only an explicit debug option through `SFT_PERSIST_SUPERVISOR_ROLLOUT_RAW=1`.
- 2026-07-31: Mock E2E revalidation found the Markdown image parser was restoring `django_1776_django-11790` to `django-11790` instead of `django__django-11790`, so SWE-bench metadata lookup missed `problem_statement`. Fixed `_instance_id_from_image()` to strip the `sweb.eval.x86_64.` prefix and restore `_1776_` to `__`.
- 2026-07-31: Mock E2E should default `HF_HUB_OFFLINE=1` and `HF_DATASETS_OFFLINE=1`, because the demo node may not reach Hugging Face and the required SWE-bench data is already cached locally.
- 2026-07-31: Supervisor replay raw trajectories wrap the original SWE prompt inside a jiuwenswarm message JSON string, so validator checks must handle escaped `\n`, `\t`, and `\"` before checking that `dataset_case.task_prompt` contains the issue statement.

## Interaction Suggestions To Preserve

These are design/debug suggestions from the conversation that should survive context loss:

- SFT 场景 2-1 不要依赖 agent 容器自己推断 Docker 镜像名。外层 `task_rollouter` 应从用例表读取 `docker_image` / `instance_id` / task prompt，启动 SWE Docker 时通过环境变量传入容器，容器内 `SFTOnlineRail` 再把这些信息写入 raw 轨迹元数据。
- 场景 2-1 的执行单位应是“一个数据集用例对应一个会话”。任务结束时应显式关闭会话，并在关闭会话时生成一条 raw session 轨迹。
- 场景 2-2 的会话切分不能只依赖用户主动换会话。除显式关闭会话外，还需要长度阈值；超过阈值时单独生成一条轨迹，并可结合上下文压缩。
- 原始 raw 轨迹不是最终 SFT 训练数据。它应作为 scheduler rollouter 的输入，状态从 `pending -> processing -> processed/failed`；只有 rollouter 产出的 `sft-sample-v1` 才进入 `pending -> training -> trained/failed`。
- 上传入口应统一。agent / jiuwenswarm / rollout 容器都只调用 `/v1/gateway/upload/batch` 上传轨迹；不新增 SFT 专属上传接口。
- 上传 API 不应决定训练后端。gateway 只做基础校验、记录和入库；scheduler 消费轨迹时根据 `TRAIN_BACKEND`、任务配置、是否需要 rollout 来推进状态并选择 RL 奖励计算或 SFT rollout/training。
- 如果后端是 RL，raw 轨迹可以进入奖励计算和 RL 训练路径；如果后端是 SFT 且需要 rollout，则 scheduler 在消费 raw 时先标记 processing，rollout 完成后再标记 processed。
- 当前验证优先打通控制面和数据面，不跑真实 GPU 训练。GPU 训练先用 mock/dry-run 替代，重点检查传入训练的数据格式和元数据是否满足 SFT 需求。
- 端到端验证脚本应覆盖完整流程：启动后台服务但不启动 host jiuwenswarm；调用 `task_rollouter` 拉起 SWE Docker + jiuwenswarm；采集 raw 轨迹；校验 raw 格式和元数据；调用训练任务 API；scheduler rollouter 重放任务并生成 supervisor 轨迹；mock SFT 训练校验 `train.json`；最后清理环境。
- 验证必须可重复运行，至少连续 3 次确认链路稳定，且每次启动前应清理 Redis 中 SFT raw/sample/task 状态，避免误用历史残留数据。
- `llm-data-proxy` 是其他同事的轨迹采集实现，当前 SFTOnlineRail/supervisor rollout 链路需要覆盖它的 ChatML 轨迹能力，尤其是 messages、assistant response、tools、tool calls、tool results、timestamps/incomplete 语义，以及可训练的 assistant 输出。
- jiuwenswarm 源码不要直接修改。注入 Rail 应通过 agent-core 的环境变量入口完成：`USE_RL_ONLINE_RAIL=1` 保持旧开关语义，`TRAIN_BACKEND=SFT` 选择 `SFTOnlineRail`，默认/RL/PPO 选择 `RLOnlineRail`。
- SWE Docker 容器不需要 GPU。容器只负责复现任务和运行 jiuwenswarm；模型服务、scheduler 和训练服务在外部后端环境中运行。
- 由于 jiuwenswarm 在其他 Docker 容器里运行，gateway / supervisor / mock OpenAI URL 应使用当前容器可被 Docker 访问的 `172.*` IP，而不是只写 `127.0.0.1`。
- 为保证 SWE 容器内代码和 conda 环境一致，启动容器时挂载 host 的 `agent-core`、`jiuwenclaw` 和 `/data1/lll/miniconda3`，并在容器内激活 `openjiuwen-rl`。

## Current Branch And Save Points

- Previous branch saved before switching:
  - branch: `agentos-v1-sp-validate`
  - commit: `2e0aa7fb chore(online-rl): save sp sft validation work`
- Active target branch:
  - branch: `agentos-v2-dev`

## Current Status

- [x] Saved current `agentos-v1-sp-validate` work before switching branches.
- [x] Switched to `agentos-v2-dev`.
- [x] Inspect existing SFT implementation in `agentos-v2-dev`.
- [x] Identify gateway, scheduler, SFT rail, collector, rollouter, and training task API entry points.
- [x] Ported prior SFT implementation from reflog commit `92605ecd` into `agentos-v2-dev`.
- [x] Added `/v1/training/tasks` gateway API and Redis `TrainingTaskStore`.
- [x] Added scheduler support for `TRAIN_BACKEND=SFT` with API-triggered raw rollout and training.
- [x] Added configurable `DockerJiuwenSwarmRollouter` for scenario 2-1.
- [x] Added initial scenario 2-1 task rollouter for launching CPU-only SWE + jiuwenswarm containers from the case table and injecting image/prompt metadata through environment variables.
- [x] Updated local debug defaults to use vLLM GPU `0,1` and training GPU `4,5,6,7`.
- [x] Added default SFT trainer command: LoRA + SP=4 + max_length=32k + max_token_len_per_gpu=8k.
- [x] Targeted unit tests passed: `test_sft_primitives.py`, `test_online_training_scheduler.py`, `gateway/test_processor_components.py` with `python -m pytest -o addopts=''`.
- [ ] Start SFT gateway and scheduler.
- [x] Start SFT gateway and scheduler.
- [x] Build or run SWE + jiuwenswarm containers from `/data1/lll/workspace/sft_train_demo/`.
- [x] Confirm SFT string trajectories are uploaded.
- [x] Trigger async SFT training task through API.
- [x] Confirm scheduler rollouter replays trajectories.
- [x] Confirm SFT dry-run trainer writes dataset from supervisor rollout samples.
- [x] Confirm supervisor replay raw trajectories can be persisted to disk and used to directly launch real SFT + SP + LoRA training.
- [x] Fixed direct SFT training input issues found from the persisted replay artifact: merge pre-assistant user prefix for verl and strip empty nested dicts before parquet.
- [x] Confirm SFT + SP + LoRA training starts, writes checkpoint output, exports PEFT LoRA adapter, and vLLM can hot-load/use the adapter.
- [x] Confirmed current GPU driver state blocks GPU E2E: current container and new GPU Docker containers both fail CUDA/NVML initialization while GPU3 is fallen off.
- [x] Run a CPU/dry-run control-plane validation for SFT gateway, scheduler, task API, and raw/sample movement while GPU is unavailable.
- [x] Dry-run validation result: gateway accepted 2 `sft-raw-v1` session trajectories, `/v1/training/tasks` created `task-309c1d7e1431`, scheduler launched `DockerJiuwenSwarmRollouter` for the two SWE images, the task finished `succeeded`, and pending raw/sample counts returned to 0.
- [x] Add reproducible mock E2E scripts that start backend services only, run task rollouter containers, validate raw/sample metadata, trigger dry-run SFT training, and clean the environment.
- [x] Run the new mock E2E scripts 3 times successfully.
- [x] Compare `llm-data-proxy` ChatML capture with SFTOnlineRail/supervisor rollout capture and document field coverage.
- [x] Adapted the original env-controlled online rail autoload so `TRAIN_BACKEND=SFT` selects `SFTOnlineRail` instead of `RLOnlineRail`, while default/RL/PPO keeps `RLOnlineRail`.

## Reproducible Mock E2E Scripts

New scripts live under:

- `examples/jiuwenrl_online/sft_e2e/mock_openai_server.py`
  - CPU-only OpenAI-compatible mock.
  - Provides `/health`, `/v1/models`, and `/v1/chat/completions`.
  - Returns deterministic assistant text plus fake `prompt_token_ids`, `token_ids`, and `logprobs`.
- `examples/jiuwenrl_online/sft_e2e/start_sft_mock_backend.sh`
  - Starts backend services only: mock OpenAI, gateway, scheduler.
  - Does not start host jiuwenswarm and does not use GPU training.
  - Scheduler uses `TRAIN_BACKEND=SFT`, `SFT_ROLLOUTER=scenario2_1`, `SFT_DRY_RUN=1`, `ONLINE_RL_DRAIN_PENDING_ON_TRAIN=True`.
- `examples/jiuwenrl_online/sft_e2e/clean_sft_mock_e2e_env.sh`
  - Stops mock backend processes.
  - Clears Redis keys for SFT raw/sample and training tasks.
  - Cleans dry-run tmp output and local mock LoRA repo.
- `examples/jiuwenrl_online/sft_e2e/validate_sft_mock_e2e.py`
  - Validates raw phase and final phase directly from Redis plus gateway stats.
  - Checks `sft-raw-v1` metadata: `original_task`, `dataset_case.docker_image`, `dataset_case.instance_id`, non-empty LLM steps.
  - Checks final state: raw `processed`, samples `trained`, dry-run `train.json` exists and assistant messages have `loss_mask=1`.
- `examples/jiuwenrl_online/sft_e2e/run_sft_mock_e2e.sh`
  - One-command flow:
    1. clean environment;
    2. start mock backend;
    3. run `sft_rollout/run_swe_task_rollout.py` to launch CPU-only SWE Docker + jiuwenclaw/jiuwenswarm;
    4. validate raw trajectory;
    5. call `/v1/training/tasks`;
    6. wait for scheduler rollout and dry-run SFT dataset;
    7. validate final state.
- `examples/jiuwenrl_online/sft_e2e/run_sft_mock_stage1_raw.sh`
  - Runs only backend startup + task_rollouter original raw collection.
  - Validates SFT raw protocol, session metadata, `dataset_case`, Docker image, original task, and at least one LLM response.
- `examples/jiuwenrl_online/sft_e2e/run_sft_mock_stage2_rollout.sh`
  - Runs original raw collection, calls `/v1/training/tasks`, then waits for scheduler-driven supervisor rollout.
  - Validates original raw and supervisor rollout raw exist and all raw statuses are `processed`.
- `examples/jiuwenrl_online/sft_e2e/run_sft_mock_stage3_train.sh`
  - Runs original raw collection, API-triggered rollout, and mock SFT training.
  - Validates `sft-sample-v1` samples are `trained` and dry-run `train.json` keeps ChatML roles with assistant `loss_mask=1`.

Default command for a 3-run reproducibility check:

```bash
cd /data1/lll/workspace/openjiuwen/code-opt/agent-core
SFT_E2E_REPEAT=3 examples/jiuwenrl_online/sft_e2e/run_sft_mock_e2e.sh
```

Latest successful command:

```bash
cd /data1/lll/workspace/openjiuwen/code-opt/agent-core
source /data1/lll/miniconda3/etc/profile.d/conda.sh
conda activate openjiuwen-rl
export PYTHONPATH=/data1/lll/workspace/openjiuwen/code-opt/agent-core:/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw
SFT_E2E_REPEAT=3 \
SFT_E2E_CASE_LIMIT=1 \
SFT_TASK_PRINT_APP_LOG=0 \
SFT_DOCKER_ROLLOUT_DEBUG_LOG=0 \
examples/jiuwenrl_online/sft_e2e/run_sft_mock_e2e.sh
```

Latest result:

- `all 3 run(s) passed`
- Each run produced:
  - `original_raw=1`
  - `rollout_raw=1`
  - `samples=1`
  - gateway stats ended with `sft_pending_raw=0`, `sft_pending_samples=0`
  - dry-run SFT `train.json` under `/tmp/agent_rl_online_sft_mock_e2e/sft_run_*/train.json`
- The wrapper cleaned Redis SFT raw/sample/task keys after exit; post-run check returned 0 matching keys.
- The wrapper stopped its own mock/gateway/scheduler processes on ports `18102` and `18180`.

Latest revalidation after direct replay-to-training changes:

```bash
cd /data1/lll/workspace/openjiuwen/code-opt/agent-core
SFT_E2E_REPEAT=3 \
SFT_E2E_CONCURRENCY=1 \
SFT_ROLLOUT_CONCURRENCY=1 \
SFT_E2E_CASE_LIMIT=1 \
SFT_TASK_PRINT_APP_LOG=0 \
SFT_DOCKER_ROLLOUT_DEBUG_LOG=0 \
examples/jiuwenrl_online/sft_e2e/run_sft_mock_e2e.sh
```

- Result: `all 3 run(s) passed`.
- Each run produced `original_raw=1`, `rollout_raw=1`, `samples=1`, and a dry-run `train.json`.
- Final gateway stats ended with `sft_pending_raw=0` and `sft_pending_samples=0`.

Latest concurrency validation:

```bash
cd /data1/lll/workspace/openjiuwen/code-opt/agent-core
SFT_E2E_REPEAT=1 \
SFT_E2E_CONCURRENCY=4 \
SFT_ROLLOUT_CONCURRENCY=4 \
SFT_E2E_CASE_LIMIT=4 \
SFT_TASK_PRINT_APP_LOG=0 \
SFT_DOCKER_ROLLOUT_DEBUG_LOG=0 \
examples/jiuwenrl_online/sft_e2e/run_sft_mock_e2e.sh
```

- Result: `all 1 run(s) passed`.
- Raw task rollout launched four SWE containers for `django__django-11790`, `django__django-11815`, `django__django-11848`, and `django__django-11880`.
- Final validation produced `original_raw=4`, `rollout_raw=4`, `samples=4`, and dry-run dataset `/tmp/agent_rl_online_sft_mock_e2e/sft_run_0_034f4990/train.json`.
- Final gateway stats ended with `sft_pending_raw=0` and `sft_pending_samples=0`.

Latest real artifact-driven SFT validation:

- Supervisor replay raw artifact:
  - `/tmp/agent_rl_online/sft_rollouts/20260731T082606659491Z_local-web-user_aa3c323f-af27-43d5-94a5-1f211fc24e38.raw.json`
- Scheduler-produced train JSON:
  - `/tmp/agent_rl_online/sft_run_0_60c2e979/train.json`
- Manual direct SFT + SP=4 + LoRA training output:
  - `/tmp/agent_rl_online/manual_train_from_artifact/run_20260731_163010`
- Exported PEFT adapter:
  - `/tmp/agent_rl_online/manual_export_adapter`
- vLLM hot-load check:
  - `POST /v1/load_lora_adapter` returned 200 for `lora_name=sft_manual_export_test`
  - `POST /v1/chat/completions` with `model=sft_manual_export_test` returned 200

Latest real direct supervisor-replay-to-training validation:

- Scope:
  - One SWE case: `django-11790`
  - `SFT_TASK_MAX_ITERATIONS=1`
  - vLLM on GPU `0,1`, SFT training on GPU `4,5,6,7`
  - Host jiuwenswarm stopped so only the SWE Docker container uploads raw trajectories.
- Result:
  - Raw rollout validation passed: `original_raw=1`
  - Training task: `task-338c829bd6c7`
  - Scheduler replayed the Docker case and logged: `Converted supervisor rollout raw directly raw_count=1 sample_count=1`
  - No `/tmp/agent_rl_online/sft_rollouts/*.raw.json` artifact was produced.
  - SFT train JSON: `/tmp/agent_rl_online/sft_run_0_7083e51d/train.json`
  - Exported adapter: `/tmp/agent_rl_online/sft_run_0_7083e51d/lora/adapter`
  - Published LoRA repo path: `/data1/lll/workspace/openjiuwen/code-opt/agent-core/examples/jiuwenrl_online/lora_repo/local-web-user/v1`
  - vLLM hot-load returned 200 and `POST /v1/chat/completions` with `model=local-web-user` returned 200.
  - GPU `4,5,6,7` peaked around 19.3GB and was released after training.

Important defaults:

- Gateway local URL: `http://127.0.0.1:18180`
- Mock OpenAI local URL: `http://127.0.0.1:18102`
- Docker containers use the current container `172.*` IP to call gateway/mock services.
- Host-side `PYTHONPATH` defaults to `/data1/lll/workspace/openjiuwen/code-opt/agent-core:/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw`.
- SWE Docker containers mount host `agent-core`, `jiuwenclaw`, and `/data1/lll/miniconda3` read-only, then activate `openjiuwen-rl`.

## llm-data-proxy Coverage Notes

Analyzed repo:

- `/data1/lll/workspace/openjiuwen/code-opt/llm-data-proxy/src/llmdataproxy/session.py`
- `/data1/lll/workspace/openjiuwen/code-opt/llm-data-proxy/src/llmdataproxy/server.py`
- `/data1/lll/workspace/openjiuwen/code-opt/llm-data-proxy/src/llmdataproxy/chatml_schema.json`
- `/data1/lll/workspace/openjiuwen/code-opt/llm-data-proxy/tests/test_session.py`
- `/data1/lll/workspace/openjiuwen/code-opt/llm-data-proxy/tests/test_integration.py`

Comparison summary:

- `llm-data-proxy` collects at OpenAI proxy level and writes ChatML files: `{messages, tools, remarks}`.
- `SFTOnlineRail` collects at agent rail level and uploads `sft-raw-v1`, then scheduler rollouter converts to `sft-sample-v1` and dry-run/full SFT `train.json`.
- Current SFT raw/sample fields cover the important ChatML fields:
  - message `role/content/name` -> raw step `messages[]` and sample `messages[]`;
  - assistant content -> raw `response/response_text` and sample `assistant_message/response_text`;
  - assistant tool calls -> raw `response.tool_calls` and sample `assistant_message.tool_calls`;
  - request tools -> raw/sample `tools`;
  - tool outputs -> raw `type=tool` steps with `tool_name/tool_args/tool_result`;
  - timestamps -> `created_at`, trajectory metadata, and step metadata;
  - incomplete/session split -> `session_done`, `flush_reason`, and `context_compression`;
  - token/logprob data -> raw step metadata/token fields when available; SFT text training does not require them.
- Detailed mapping is also recorded in `examples/jiuwenrl_online/sft_e2e/LLM_DATA_PROXY_COVERAGE.md`.

Open coverage risk:

- `llm-data-proxy` has first-class hint injection via `/hint`. Current SFT branch has a reserved `HintRewardpackRollouter` for scenario 2-3, but the rewardpack/hint path is intentionally not implemented yet.
- Current RL legacy path still does delayed judge in gateway ingestion. The desired unified design is: upload raw once, then scheduler decides RL reward or SFT rollout/training and updates trajectory status.

## Latest Validation Notes

- GPU E2E is blocked on this node until GPU 3 / NVML is recovered. Evidence:
  - vLLM failed with `World size (2) is larger than the number of available GPUs (0)`.
  - `CUDA_VISIBLE_DEVICES=0,1 python -c 'import torch; ...'` returned `device_count=0`.
  - `CUDA_VISIBLE_DEVICES=4,5,6,7 python -c 'import torch; ...'` returned `device_count=0`.
  - `docker run --gpus '"device=0,1"' ...` failed in `nvidia-container-cli` with `nvml error: unknown`.
- 2026-07-31 local recheck:
  - Existing local Qwen3 vLLM health endpoints on `18002` and `18012` were not healthy.
  - `openjiuwen-rl`, `base`, `jiuwenclaw-vllm`, `lll-vllm-dev`, `lll-vllm-verl-dev`, `openjiuwen-rl-new`, and `openjiuwen-rl-refactor` all reported CUDA `device_count=0` with healthy-card subsets. This confirms the current blocker is node-level CUDA/NVML visibility, not only the online-RL launch script.
  - Until Qwen3 vLLM is reachable again, continue validating the non-GPU part of the chain: SWE Docker launch, source/conda mounts, injected task metadata, jiuwenswarm startup, and gateway raw-upload path.
- 2026-07-31 SWE container dispatch/environment recheck:
  - Ran one `django-11790` SWE Docker container with a model-free environment check command.
  - Verified `/testbed` exists, `openjiuwen` imports from `/data1/lll/workspace/openjiuwen/code-opt/agent-core`, `jiuwenswarm` imports from `/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw`, and the container activates `/data1/lll/miniconda3/envs/openjiuwen-rl/bin/python`.
  - Verified injected `SFT_DATASET_CASE_JSON` matches `SFT_DOCKER_IMAGE` / `SFT_INSTANCE_ID`, and the generated task prompt contains `Problem Statement`, `Patch Output Format`, and the SWE issue statement.
  - Ran a second `django-11790` SWE Docker container that started jiuwenswarm app inside the SWE image and connected to `ws://127.0.0.1:19000/ws`; app log showed AgentServer and WebChannel started normally.
  - Current `18180` gateway is only reachable at `127.0.0.1:18180`; `http://<172.*>:18180/health` fails. Raw upload from SWE Docker needs gateway rebound to `0.0.0.0` or another Docker-reachable port.
- Non-GPU control plane is healthy:
  - gateway health passed on `http://172.17.0.5:18080/health`.
  - scheduler started with `backend=SFT`, `drain_pending=True`, `min_samples=2`.
  - task API and SFT raw rollout path worked in `SFT_DRY_RUN=1`.
- 2026-07-30 latest scenario 2-1 validation:
  - Initial SWE container launched `swebench/sweb.eval.x86_64.django_1776_django-11790:latest` CPU-only.
  - Historical note: this validation used a temporary `sitecustomize` patch. That approach is superseded by agent-core env autoload, where `TRAIN_BACKEND=SFT` selects `SFTOnlineRail`.
  - Gateway accepted one `sft-raw-v1` raw trajectory for `local-web-user`; metadata included `docker_image`, `instance_id`, and `original_task`.
  - `/v1/training/tasks` created `task-476829bec468`.
  - Scheduler started `DockerJiuwenSwarmRollouter` with the same SWE image from raw metadata.
  - Supervisor rollout uploaded a second raw trajectory under rollout user `local-web-user:sft-rollout:<raw_id>`.
  - Scheduler converted that rollout raw into one `sft-sample-v1` sample and dry-run wrote `/tmp/agent_rl_online_sft_debug/sft_run_0_d64b3adb/train.json`.
  - Task `task-476829bec468` finished `succeeded`; gateway stats returned `sft_pending_raw=0`, `sft_pending_samples=0`.
- 2026-07-30 mock E2E reproducibility validation:
  - Initial failed run exposed Docker URL detection bug: SWE containers received `API_BASE=http://127.0.0.1:18102/v1` and could not reach the mock OpenAI server.
  - After fixing URL detection, initial raw upload succeeded but final strict validation failed because scheduler rollout did not upload supervisor raw.
  - Scheduler debug log showed the second Docker failed immediately with `ModuleNotFoundError: No module named 'jiuwenclaw'`.
  - Root cause was a bad explicit path override in `start_sft_mock_backend.sh`: `/data1/lll/workspace/openjiuwen/code-opt/jiuwenclaw` does not exist; the intended path for this debug environment is `/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw`.
  - After centralizing `SFT_E2E_JIUWENCLAW_HOST_PATH` detection in `sft_mock_e2e_common.sh`, one single-run validation passed, then a 3-run validation passed.
  - Final validated flow per run: clean Redis -> start mock backend -> launch initial SWE Docker + jiuwenswarm -> upload original `sft-raw-v1` -> trigger `/v1/training/tasks` -> scheduler launches supervisor SWE Docker -> upload rollout `sft-raw-v1` -> build `sft-sample-v1` -> dry-run write `train.json` -> mark raw `processed` and sample `trained`.
- 2026-07-30 latest mock E2E rerun:
  - `run_sft_mock_stage1_raw.sh` passed with `original_raw=1`.
  - `run_sft_mock_stage2_rollout.sh` initially failed because `validate_sft_mock_e2e.py --phase rollout` checked Redis immediately after creating the async training task. This was a validator race, not a scheduler/rollouter failure.
  - Fixed rollout validation to poll Redis until original raw + supervisor rollout raw are present and all raw statuses are `processed`.
  - After the fix, `run_sft_mock_stage2_rollout.sh` passed with `original_raw=1`, `rollout_raw=1`, `raw_statuses=["processed"]`.
  - `run_sft_mock_stage3_train.sh` passed with `original_raw=1`, `rollout_raw=1`, `samples=1`, and dry-run train file `/tmp/agent_rl_online_sft_mock_e2e/sft_run_0_f8d6a01d/train.json`.
  - Full wrapper command with `SFT_E2E_REPEAT=3 SFT_E2E_CASE_LIMIT=1` passed all 3 runs. Per run: `original_raw=1`, `rollout_raw=1`, `samples=1`, `sft_pending_raw=0`, `sft_pending_samples=0`.
  - Post-run cleanup check found `matching_keys 0` for Redis `rl:sft_*` and `rl:training_task*` keys.
- 2026-07-30 env rail autoload validation:
  - Host probe with `USE_RL_ONLINE_RAIL=1 TRAIN_BACKEND=SFT` printed `['SFTOnlineRail']`.
  - SWE Docker probe with mounted code-opt `agent-core`, refactor `jiuwenclaw`, and host `openjiuwen-rl` conda also printed `['SFTOnlineRail']`.
  - Targeted unit test `tests/unit_tests/agent_evolving/agent_rl/online/test_online_rail_autoload.py` passed: 5 tests.

## Open Questions / Risks

- Full GPU E2E is not complete because current node CUDA/NVML initialization is broken while GPU3 is fallen off.
- Need to rerun the full flow after GPU recovery:
  - start vLLM on GPU `0,1` with the normal local backend script, not a VLLM Docker container;
  - run two real CPU-only SWE + jiuwenswarm Docker sessions with `SFTOnlineRail`, mounting host conda/source paths unchanged;
  - trigger `/v1/training/tasks`;
  - verify Docker rollouter replay uses the supervisor model endpoint;
  - verify SFT + SP + LoRA training runs on GPU `4,5,6,7` and publishes LoRA output.
- Need to replace the debug prompt generated from instance id with the real SWE task prompt if `/data1/lll/workspace/sft_train_demo/` later provides structured case metadata.
- GPU allocation for this task is fixed unless the user changes it:
  - vLLM: GPU `0,1`
  - SFT training: GPU `4,5,6,7`
- `/data1/lll/workspace/sft_train_demo/` currently contains image list and Python requirements, but no separate SWE task prompt JSON. For the first debug pass, generate prompts from the selected instance ids and record the Docker image in `dataset_case`.
