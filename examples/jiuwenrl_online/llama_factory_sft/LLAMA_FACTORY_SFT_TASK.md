# LLaMA-Factory SFT Task

## Goal

Replace the online SFT trainer backend on `agentos-sft-flow-v3` from the
previous verl-based SFT scripts to LLaMA-Factory, while keeping the existing
SFT rail, task rollouter, supervisor replay, scheduler, LoRA repository, and
manual training trigger flow reusable.

## Environment

- Repo: `/data1/lll/workspace/openjiuwen/code-opt/agent-core`
- Branch: `agentos-sft-flow-v3`
- Conda env: `/data1/lll/miniconda3/envs/openjiuwen-sft`
- Source env cloned from: `openjiuwen-rl`
- Validation data: `/data1/lll/workspace/swe-dataset/selected_datasets_llamav1`

## Requirements

- Do not modify `openjiuwen-rl`; use the copied `openjiuwen-sft` env for
  LLaMA-Factory dependencies.
- Preserve the existing SFT flow:
  local jiuwenswarm skill/task rollouter -> SWE Docker supervisor replay ->
  Gateway upload -> Scheduler manual training task -> LoRA publish/load.
- Start with standalone SFT validation from existing V1 trajectory JSONL files.
- Add a reusable conversion module between supervisor replay `sft-sample-v1`
  payloads and LLaMA-Factory training data.
- Keep progress and blockers in this file so the task can be resumed.

## Current Code Findings

- Current online SFT scheduler integration lives in
  `openjiuwen/agent_evolving/agent_rl/online/scheduler/online_training_scheduler.py`.
- Current SFT executor lives in
  `openjiuwen/agent_evolving/agent_rl/online/sft/executor.py`.
- The executor already separates rollouter sample generation from trainer
  execution:
  - `build_samples_from_raw()` converts raw replay inputs to normalized
    `sft-sample-v1`.
  - `train_batch()` writes a temporary dataset and runs `trainer_command`.
- The old standalone SFT scripts under `examples/jiuwenrl_online/sft_only/`
  prepare parquet and launch verl entrypoints.
- `SFTOnlineRail` can upload either:
  - `sft-raw-v1` for scheduler-side replay, or
  - direct `sft-sample-v1` when the Docker task has already used the
    supervisor model.

## Data Format Gap

### Existing V1 / supervisor replay sample

The existing trajectory data is message-based. A trainable sample usually has:

- `messages`: prompt context messages.
- `assistant_message`: final supervised assistant target.
- `tools`: optional tool schema.
- `metadata`: original raw id, dataset case, docker image, prompt, scenario.

Some standalone V1 files already store the assistant target inside
`messages` with `loss_mask=1`.

### LLaMA-Factory target format

Use LLaMA-Factory `openai` formatting as the first implementation target because
it preserves OpenAI-style `messages` and can keep tool call fields closer to the
supervisor replay output:

- `messages`: list of `{role, content}` messages.
- role mapping:
  - `system` -> `system`
  - `user` -> `user`
  - `assistant` -> `assistant`
  - `tool` -> `tool`
  - assistant tool calls -> `function_call` internally in LLaMA-Factory.
- `tools`: optional JSON string for tool definitions.
- `metadata`: kept for traceability; LLaMA-Factory ignores it during training.

The conversion must serialize non-string content and tool calls deterministically
so LLaMA-Factory receives plain text values.

## Two Upstream Chains, One Downstream Backend

The current target is a shared downstream chain after data conversion:

1. `supervisor replay -> sft trajectory v1 -> gateway -> scheduler -> llama factory data -> llama factory -> LoRA`
2. `v1data+v0train/data_openai -> single_sft.sh -> llama factory data -> llama factory -> LoRA`

The upstream inputs and preprocessing can differ, but once the records are
materialized as LLaMA-Factory data the remaining flow must be identical:
same dataset writer, same `train.yaml` shape, same `llamafactory-cli` launch,
and the same LoRA publish/load path.

## Implementation Plan

1. Add `llama_factory.py` under the online SFT package.
   - Load JSON, JSONL, directory trees, and `{"samples": [...]}` payloads.
   - Normalize V1 / `sft-sample-v1` samples into LLaMA-Factory openai records.
   - Write `train.json`, `dataset_info.json`, and `train.yaml`.
   - Emit basic conversion stats.
2. Add a LLaMA-Factory trainer adapter.
   - Build output under the executor run directory.
   - Use `llamafactory-cli train <yaml>`.
   - Keep custom `sft_trainer_command` as an override for debugging.
   - Publish the generated adapter through the existing `LoRARepository`.
3. Add standalone validation script.
   - Input: `/data1/lll/workspace/swe-dataset/selected_datasets_llamav1`.
   - Output: `/data1/lll/workspace/swe-dataset/llama_factory_sft_runs`.
   - Default to LoRA SFT, single batch, one epoch, conservative sequence length.
4. Validate in stages.
   - Conversion-only on V1 dataset.
   - LLaMA-Factory CLI availability in `openjiuwen-sft`.
   - Short standalone training run.
   - Scheduler `SFTTrainingExecutor` prepare-only or dry-run path.

## Progress

- 2026-08-04: Switched to `agentos-sft-flow-v3`.
- 2026-08-04: Confirmed `openjiuwen-sft` env exists and can import torch and
  `openjiuwen`; `llamafactory` is not installed yet.
- 2026-08-04: Confirmed V1 validation data exists under
  `/data1/lll/workspace/swe-dataset/selected_datasets_llamav1` and is
  message-based JSONL.
- 2026-08-04: Identified minimal online-flow integration point:
  `SFTTrainingExecutor.train_batch()` should keep rollouter inputs unchanged
  and delegate dataset/trainer work to a LLaMA-Factory adapter.
- 2026-08-04: Installed `llamafactory==0.9.5` in the copied
  `openjiuwen-sft` env. This changed only the copied env; `openjiuwen-rl`
  was not modified.
- 2026-08-04: Added `openjiuwen/agent_evolving/agent_rl/online/sft/llama_factory.py`.
  It loads V1/sft-sample inputs, converts them to LLaMA-Factory openai format,
  writes `train.json`, `dataset_info.json`, `train.yaml`, and runs
  `llamafactory-cli train` or `python -m llamafactory.cli train`.
- 2026-08-04: Updated `SFTTrainingExecutor` so no `sft_trainer_command` is
  required by default; it now uses LLaMA-Factory. A custom command still
  overrides this path for debugging.
- 2026-08-04: Added standalone validation entrypoints:
  - `examples/jiuwenrl_online/llama_factory_sft/train_sft_llama_factory_from_v1.py`
  - `examples/jiuwenrl_online/llama_factory_sft/train_sft_llama_factory_from_v1.sh`
- 2026-08-04: Validation passed:
  - `python -m py_compile` on modified Python files.
  - `pytest -q -o addopts='' tests/unit_tests/agent_evolving/agent_rl/online/test_sft_primitives.py`
    returned `17 passed`.
  - Conversion-only from `/data1/lll/workspace/swe-dataset/selected_datasets_llamav1`
    wrote `/data1/lll/workspace/swe-dataset/llama_factory_sft_runs/run_20260804_214823/dataset/train.json`.
  - The first two converted V1 samples were about 7186 and 7336 tokens with
    `/data1/lll/models/Qwen3-0.6B`.
  - Real LLaMA-Factory LoRA smoke training passed with
    `/data1/lll/models/Qwen3-0.6B`, 2 V1 samples, `cutoff_len=8192`,
    `max_steps=1`, GPU 4. Output LoRA:
    `/data1/lll/workspace/swe-dataset/llama_factory_sft_smoke/run_20260804_215015/lora`.
- 2026-08-04: Cleaned up ruff issues in the new LLaMA-Factory files:
  import ordering, unnecessary encoding comments, timezone-aware run id,
  non-broad notifier exception handling, and invalid-type exception class.
- 2026-08-04: Re-validation after cleanup passed in `openjiuwen-sft`:
  - `ruff check` on the new/modified SFT files passed.
  - `python -m py_compile` on modified Python entrypoints passed.
  - `pytest -q -o addopts='' tests/unit_tests/agent_evolving/agent_rl/online/test_sft_primitives.py`
    returned `17 passed`.
  - Prepare-only conversion from
    `/data1/lll/workspace/swe-dataset/selected_datasets_llamav1`
    wrote `/tmp/llama_factory_v1_prepare_check/run_20260804_135555/dataset/train.json`.
    The generated dataset contained 2 records, 8 messages, and LLaMA-Factory
    `openai` formatting.
  - Real LLaMA-Factory LoRA smoke training passed again with
    `/data1/lll/models/Qwen3-0.6B`, 2 V1 samples, `cutoff_len=8192`,
    `max_steps=1`, GPU 4. Output LoRA:
    `/tmp/llama_factory_v1_train_smoke/run_20260804_135625/lora`.
    The output includes `adapter_config.json` and `adapter_model.safetensors`.
- 2026-08-05: Consolidated LLaMA-Factory SFT scripts/docs under
  `examples/jiuwenrl_online/llama_factory_sft/`.
- 2026-08-05: Changed `online_rl_local_env.sh` so `SFT_TRAINER_COMMAND`
  defaults to empty; this makes scheduler SFT training use
  `SFTTrainingExecutor`'s native LLaMA-Factory adapter instead of the legacy
  verl command. Legacy/custom trainers still work when explicitly configured.
- 2026-08-05: Added `requirements.txt` from the copied `openjiuwen-sft` conda
  env.
- 2026-08-05: Added `train_selected_v1_dataset.py/.sh`, which scans V1 samples
  with the target tokenizer before training. It writes `scan_report.json` and
  `failed_samples.json`, excludes samples over `cutoff_len`, and trains only
  non-truncated records.
- 2026-08-05: Full scan of
  `/data1/lll/workspace/swe-dataset/selected_datasets_llamav1` with
  `/data1/lll/models/Qwen3-0.6B` and `cutoff_len=32768`:
  731 input samples, 562 trainable records, 169 failed records. All failures
  were `over_cutoff_len`; max token count was 99,354. Overlength directories:
  `sphinx-doc__sphinx-8548` 75, `sphinx-doc__sphinx-10673` 43,
  `django__django-12308` 28, `sphinx-doc__sphinx-8056` 20,
  `sphinx-doc__sphinx-8551` 3.
- 2026-08-05: Real LLaMA-Factory smoke training over the 562 fit V1 records
  passed with `max_steps=1`, GPU 4. Output LoRA:
  `/tmp/llama_factory_selected_v1_train/run_20260805_002437/lora`.
- 2026-08-05: Direct supervisor replay E2E initially failed because
  `openjiuwen/core/sys_operation/local/_async_read_write_lock.py` contained
  unresolved merge conflict markers, causing SWE-container jiuwenswarm agent
  creation to fail before any LLM/Rail callback. Fixed the filelock
  compatibility fallback.
- 2026-08-05: Direct supervisor replay E2E passed after the fix:
  `sft-optimize` launched one SWE Docker case, container jiuwenswarm called the
  supervisor endpoint, `SFTOnlineRail(sample)` uploaded one `sft-sample-v1`,
  `/v1/training/tasks` triggered scheduler SFT, LLaMA-Factory ran one real LoRA
  training step, and LoRA was published under
  `examples/jiuwenrl_online/sft_e2e/lora_repo/local-web-user/v1`.
- 2026-08-05: Re-ran direct skill/supervisor replay E2E after consolidating
  scripts under `examples/jiuwenrl_online/llama_factory_sft/`. The chain passed
  again with `SFT_E2E_CASE_LIMIT=1`, `MODEL_PATH=/data1/lll/models/Qwen3-0.6B`,
  `SFT_LLAMAFACTORY_CUTOFF_LEN=8192`, and `SFT_LLAMAFACTORY_MAX_STEPS=1`.
  Output LoRA:
  `/tmp/agent_rl_online_sft_mock_e2e/sft_run_1_051987c9/lora/adapter_model.safetensors`.
- 2026-08-05: Re-ran full selected V1 dataset scan with `cutoff_len=32768`.
  Result remained 731 input samples, 562 trainable records, and 169
  `over_cutoff_len` failures. Reports:
  `/tmp/llama_factory_selected_v1_scan_latest/run_20260805_003654/reports`.
- 2026-08-05: Re-ran real LLaMA-Factory LoRA smoke training over the 562 fit V1
  records with `max_steps=1`, GPU 4. Output LoRA:
  `/tmp/llama_factory_selected_v1_train_latest/run_20260805_003805/lora/adapter_model.safetensors`.
- 2026-08-05: Added `single_sft.sh` as the short offline entrypoint alias for
  the V1-to-LLaMA-Factory training path.

## Open Issues

- LLaMA-Factory 0.9.5 installed successfully but downgraded a few packages in
  the copied env (`pydantic`, `aiofiles`, `datasets`, `accelerate`, etc.).
  Basic imports and tests pass, but keep this isolated from `openjiuwen-rl`.
- The smoke run used Qwen3-0.6B for speed and memory safety. A larger target
  model such as Qwen3-4B or Qwen3.5-2B still needs a resource-specific run.
- Standard LLaMA-Factory LoRA training does not provide the same verl SP path
  used by the old scripts. Long-context 32k/64k validation may need DeepSpeed,
  FSDP, or additional LLaMA-Factory parallel settings.
- Direct skill/supervisor replay E2E currently uses a local OpenAI-compatible
  supervisor endpoint for deterministic control-plane validation. Use a real
  `SUPERVISOR_URL`, `SUPERVISOR_TOKEN`, and `SUPERVISOR_MODEL` when validating
  supervisor model quality.
