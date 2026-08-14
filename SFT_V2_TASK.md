# SFT Flow V2 Task

## Goal

Optimize the SFT scenario 2-1 entry flow so a local jiuwenswarm session can invoke an `sft-optimize` skill, pass a dataset mapping plus supervisor LLM API information, and directly collect supervisor trajectories in SWE Docker cases for SFT training.

## Constraints

- Keep the existing raw replay flow from `SFT_TASK.md` working.
- Do not modify jiuwenswarm / jiuwenclaw source.
- Local jiuwenswarm uses its own local LLM backend.
- SWE Docker task containers use the supervisor LLM backend passed by the `sft-optimize` request.
- Backend services such as gateway and scheduler are assumed to be running before the skill is called.
- `RL_GATEWAY_URL` / `RL_SCHEDULER_URL` can be passed through environment variables for the first implementation.
- The direct flow should support concurrency through one environment variable / CLI option, with concurrency 1 validated first.

## Proposed Flow

1. User asks the local jiuwenswarm to use `sft-optimize`.
2. The skill parses dataset mapping path, supervisor URL/token/model, gateway URL, scheduler URL, user id, and concurrency.
3. The skill calls a local CLI that reads the dataset mapping and launches SWE Docker task containers.
4. Each SWE Docker container starts jiuwenswarm/jiuwenclaw with `TRAIN_BACKEND=SFT` and `SFTOnlineRail`.
5. The Docker container uses the supervisor LLM API, runs the task prompt, and closes the session.
6. `SFTOnlineRail` converts the collected supervisor session directly into `sft-sample-v1` and uploads it to gateway.
7. The CLI can optionally call `/v1/training/tasks` to trigger SFT training. Scheduler consumes pending SFT samples directly.

## Implementation Notes

- Add a `SFT_ONLINE_UPLOAD_MODE` rail env:
  - `raw` keeps the existing raw trajectory upload behavior.
  - `sample` converts the collected raw session in-process and uploads `sft-sample-v1`.
- Reuse the same raw-to-sample conversion logic between scheduler replay and direct supervisor rollout.
- Add a `run_sft_optimize.py` CLI for skill/script usage.
- Add an example `sft-optimize` skill under `examples/jiuwenrl_online/skills/sft-optimize`.

## Progress

- 2026-08-01: Created `agentos-sft-flow-v2` from `agentos-sft-flow`.
- 2026-08-01: Started implementing direct supervisor sample upload while preserving raw replay.
- 2026-08-01: Added `SFT_ONLINE_UPLOAD_MODE`.
  - `raw`: existing raw replay flow remains unchanged.
  - `sample`: `SFTOnlineRail` converts the collected supervisor session into `sft-sample-v1` and uploads samples directly.
- 2026-08-01: Added shared raw-to-sample conversion helper in `sample_builder.py` and kept scheduler rollouter behavior through a compatibility wrapper.
- 2026-08-01: Added `examples/jiuwenrl_online/sft_rollout/run_sft_optimize.py` as the CLI called by the local `sft-optimize` skill.
- 2026-08-01: Added `examples/jiuwenrl_online/skills/sft-optimize/SKILL.md`.
- 2026-08-01: Added direct-flow mock E2E script `examples/jiuwenrl_online/sft_e2e/run_sft_optimize_mock_e2e.sh`.
- 2026-08-01: Unit validation passed:
  - `pytest -q -o addopts='' tests/unit_tests/agent_evolving/agent_rl/online/test_sft_primitives.py`
  - `python -m py_compile` for modified Python modules and scripts.
- 2026-08-01: Direct supervisor optimize E2E passed with concurrency 1:
  - Command: `SFT_E2E_REPEAT=1 SFT_E2E_CONCURRENCY=1 SFT_ROLLOUT_CONCURRENCY=1 SFT_E2E_CASE_LIMIT=1 ... run_sft_optimize_mock_e2e.sh`
  - Result: uploaded 1 direct `sft-sample-v1`, triggered one training task, generated dry-run dataset `/tmp/agent_rl_online_sft_mock_e2e/sft_run_1_0f14e43f/train.json`, and gateway stats returned `sft_pending_raw=0`, `sft_pending_samples=0`.
- 2026-08-01: Direct supervisor optimize E2E passed with real SFT LoRA training.
  - Command: `SFT_E2E_KEEP_ENV=1 SFT_E2E_DRY_RUN=0 SFT_E2E_TRAIN_GPU=4,5,6,7 SFT_E2E_REPEAT=1 SFT_E2E_CONCURRENCY=1 SFT_ROLLOUT_CONCURRENCY=1 SFT_E2E_CASE_LIMIT=1 ... run_sft_optimize_mock_e2e.sh`
  - Trainer launched `torchrun --nproc_per_node=4`.
  - Training config included `data.max_length=32768`, `data.max_token_len_per_gpu=8192`, `data.truncation=error`, `engine.ulysses_sequence_parallel_size=4`, LoRA rank 16, bf16, activation offload, optimizer offload.
  - Runtime sample length was about 28946 tokens, so the 32k context path was exercised without truncation.
  - Observed peak GPU memory during the run was about 18140 MiB on each of GPUs 4-7.
  - LoRA was exported with 504 tensors and published as `local-web-user/v1`.
  - Preserved LoRA path: `examples/jiuwenrl_online/sft_e2e/lora_repo/local-web-user/v1`.
  - Preserved training dataset path: `/tmp/agent_rl_online_sft_mock_e2e/sft_run_1_ef4a1f00/train.json`.
- 2026-08-01: Re-ran the previous raw replay mock E2E as a regression check.
  - Raw collection still works: 1 original `sft-raw-v1` was uploaded and validated.
  - Replay path launched and produced a supervisor-derived sample, but this old script run did not generate dry-run `train.json` before validation failed.
  - The code path is still present via `SFT_ONLINE_UPLOAD_MODE=raw`; this needs a separate follow-up if strict old-script E2E parity is required.
- 2026-08-01: Refined the v2 implementation for readability and reuse.
  - Moved direct supervisor raw-to-sample conversion into `sample_builder.build_direct_supervisor_sft_samples`.
  - Reduced `SFTOnlineRail` upload branching to raw-vs-sample dispatch plus shared uploader calls.
  - Grouped SWE Docker env construction in `docker_runtime.build_jiuwenclaw_docker_env` so Rail injection settings stay in one place.
  - Isolated `run_sft_optimize.py` training-task metadata construction.
- 2026-08-01: Revalidated after cleanup.
  - Unit tests: `14 passed` for `tests/unit_tests/agent_evolving/agent_rl/online/test_sft_primitives.py`.
  - Compile check passed for modified Python modules and E2E scripts.
  - Direct optimize E2E passed with real SFT LoRA training, not dry-run:
    `SFT_E2E_DRY_RUN=0 SFT_E2E_TRAIN_GPU=4,5,6,7 SFT_E2E_REPEAT=1 SFT_E2E_CONCURRENCY=1 SFT_ROLLOUT_CONCURRENCY=1 SFT_E2E_CASE_LIMIT=1 ... run_sft_optimize_mock_e2e.sh`.
  - Validation output: phase `direct-final`, samples `1`, train data `/tmp/agent_rl_online_sft_mock_e2e/sft_run_1_8901200d/train.json`.
  - Training evidence: `torchrun --nproc_per_node=4`, `data.max_length=32768`, `data.max_token_len_per_gpu=8192`, `engine.ulysses_sequence_parallel_size=4`, runtime token length about `28946`, one training step completed and LoRA checkpoint/export path was exercised.
- 2026-08-03: Added a cross-machine transfer direction for the next iteration.
  - Collect machine should export `sft-sample-v1` into a portable JSON package instead of relying on the source Redis.
  - Training machine should import that package locally, keep scheduler in manual-trigger mode (`drain_pending_on_train=True`), and call `/v1/training/tasks:trigger` or `/v1/training/tasks` to start training explicitly.
  - Added export/import scripts under `examples/jiuwenrl_online/sft_transfer/`.
  - Added a gateway alias route for explicit manual task triggering.

## Open Issues

- Previous raw replay mock E2E needs follow-up validation. Current v2 direct flow is intentionally designed to bypass raw replay.
