# LLaMA-Factory SFT Flow

## Scope

This directory keeps the LLaMA-Factory SFT integration artifacts together:

- `requirements.txt`: package snapshot for the copied `openjiuwen-sft` env.
- `train_selected_v1_dataset.py`: scan and train V1 / `sft-sample-v1` trajectory data.
- `train_selected_v1_dataset.sh`: shell wrapper that activates `openjiuwen-sft`.
- `train_sft_llama_factory_from_v1.py`: small standalone conversion/training entrypoint.
- `run_skill_supervisor_replay_e2e.sh`: one-case E2E wrapper for the direct supervisor replay flow.
- `single_sft.sh`: short alias for the offline V1-to-LLaMA-Factory training path.
- `LLAMA_FACTORY_SFT_TASK.md`: resumable implementation and validation notes.

## Skill Install

Install the `sft-optimize` skill into local jiuwenswarm before using the
direct supervisor replay flow:

```bash
bash examples/jiuwenrl_online/skills/install_sft_optimize_skill.sh
```

The installed skill calls
`examples/jiuwenrl_online/skills/sft-optimize/scripts/run_sft_optimize_skill.py`
with the user's original request, which then delegates to the rollout/training
CLI.

## Main Chain

The optimized SFT path is:

```text
local jiuwenswarm + sft-optimize skill
  -> examples/jiuwenrl_online/sft_rollout/run_sft_optimize.py
  -> task_rollouter launches SWE Docker containers
  -> container jiuwenswarm uses supervisor LLM and SFTOnlineRail(sample mode)
  -> gateway stores sft-sample-v1
  -> POST /v1/training/tasks
  -> scheduler SFTTrainingExecutor
  -> LLaMA-Factory LoRA SFT
  -> LoRARepository publish and optional vLLM hot-load
```

There is no raw replay stage in this direct skill flow. The older raw replay
flow remains available when `SFT_ONLINE_UPLOAD_MODE=raw`.

## Key Config

- `ONLINE_RL_CONDA_ENV=openjiuwen-sft`
- `TRAIN_BACKEND=SFT`
- `SFT_TRAINER_COMMAND=`: keep empty to use the native LLaMA-Factory adapter.
- `RL_GATEWAY_URL` / `TRAJECTORY_GATEWAY_URL`: gateway reachable by Docker cases.
- `SUPERVISOR_URL`, `SUPERVISOR_TOKEN`, `SUPERVISOR_MODEL`: supervisor model for Docker jiuwenswarm.
- `SFT_ONLINE_UPLOAD_MODE=sample`: direct supervisor sample upload.
- `SFT_LLAMAFACTORY_CUTOFF_LEN`: LLaMA-Factory `cutoff_len`.
- `SFT_LLAMAFACTORY_MAX_STEPS`: optional smoke limit; `-1` trains normally.
- `TRAIN_GPU`: GPUs exposed to LLaMA-Factory.

## Direct V1 Dataset Training

Scan the full selected V1 dataset and write overlength/invalid samples:

```bash
bash examples/jiuwenrl_online/llama_factory_sft/train_selected_v1_dataset.sh \
  /data1/lll/workspace/swe-dataset/selected_datasets_llamav1 \
  --model-path /data1/lll/models/Qwen3-0.6B \
  --cutoff-len 32768 \
  --scan-only
```

Train all samples that fit the cutoff without truncation:

```bash
bash examples/jiuwenrl_online/llama_factory_sft/train_selected_v1_dataset.sh \
  /data1/lll/workspace/swe-dataset/selected_datasets_llamav1 \
  --model-path /data1/lll/models/Qwen3-0.6B \
  --cutoff-len 32768 \
  --train-gpu 4 \
  --max-steps 1
```

Outputs are written under
`/data1/lll/workspace/swe-dataset/llama_factory_sft_runs/run_*`:

- `dataset/train.json`
- `dataset/dataset_info.json`
- `dataset/train.yaml`
- `reports/scan_report.json`
- `reports/failed_samples.json`
- `lora/adapter_model.safetensors` after training succeeds

The script scans every input sample first. Samples exceeding `cutoff_len` are
listed in `failed_samples.json` and are not passed to training, so the training
set is not silently truncated. Add `--fail-on-overlength` to abort instead of
training the remaining fit samples.

## Direct Skill/Supervisor E2E Smoke

Run one SWE case through the direct supervisor path and perform a real
LLaMA-Factory LoRA smoke train:

```bash
SFT_E2E_CASE_LIMIT=1 \
SFT_E2E_CONCURRENCY=1 \
SFT_E2E_TRAIN_GPU=4 \
MODEL_PATH=/data1/lll/models/Qwen3-0.6B \
SFT_LLAMAFACTORY_CUTOFF_LEN=8192 \
SFT_LLAMAFACTORY_MAX_STEPS=1 \
bash examples/jiuwenrl_online/llama_factory_sft/run_skill_supervisor_replay_e2e.sh
```

The wrapper uses the existing `sft-optimize` command path. It starts a local
OpenAI-compatible supervisor for control-plane validation, launches the SWE task
container, verifies pending `sft-sample-v1` records, triggers
`/v1/training/tasks`, waits for success, and checks the generated LLaMA-Factory
dataset/LoRA output. This validates the direct skill -> supervisor replay ->
gateway -> scheduler -> LLaMA-Factory -> LoRA chain. It does not validate the
quality of a stronger external supervisor model.

Validated on 2026-08-05 with:

- `MODEL_PATH=/data1/lll/models/Qwen3-0.6B`
- `SFT_E2E_CASE_LIMIT=1`
- `SFT_LLAMAFACTORY_CUTOFF_LEN=8192`
- `SFT_LLAMAFACTORY_MAX_STEPS=1`
- `SFT_E2E_TRAIN_GPU=4`

Observed output:

- Uploaded samples: 1 `sft-sample-v1`
- Training task: succeeded
- LLaMA-Factory dataset:
  `/tmp/agent_rl_online_sft_mock_e2e/sft_run_1_9922bb26/llama_factory/train.json`
- LoRA output:
  `/tmp/agent_rl_online_sft_mock_e2e/sft_run_1_9922bb26/lora/adapter_model.safetensors`
- Published LoRA:
  `examples/jiuwenrl_online/sft_e2e/lora_repo/local-web-user/v1`

Latest local rerun:

- Command:
  `SFT_E2E_CASE_LIMIT=1 SFT_E2E_CONCURRENCY=1 SFT_E2E_TRAIN_GPU=4 MODEL_PATH=/data1/lll/models/Qwen3-0.6B MODEL_NAME=Qwen3-0.6B SFT_LLAMAFACTORY_CUTOFF_LEN=8192 SFT_LLAMAFACTORY_MAX_STEPS=1 bash examples/jiuwenrl_online/llama_factory_sft/run_skill_supervisor_replay_e2e.sh`
- Result: 1 uploaded `sft-sample-v1`, training task succeeded, LoRA generated.
- LoRA output:
  `/tmp/agent_rl_online_sft_mock_e2e/sft_run_1_051987c9/lora/adapter_model.safetensors`

## Format Mapping

Supervisor replay samples use the online SFT schema:

- `protocol_version=sft-sample-v1`
- `messages`: prompt context
- `assistant_message`: supervised answer
- `tools`: optional tool schemas
- `metadata`: case, Docker image, prompt, raw id, model info

LLaMA-Factory consumes OpenAI-format records:

- `messages`: alternating `system/user/assistant/tool` style messages
- `tools`: JSON string when tools exist
- `metadata`: preserved for traceability and ignored by training

The converter appends `assistant_message` as the supervised assistant turn,
normalizes non-string content to text, merges consecutive same-parity messages,
and keeps only records ending in an assistant/function-call target.

## Selected V1 Dataset Scan Result

Validated on 2026-08-05 with
`/data1/lll/workspace/swe-dataset/selected_datasets_llamav1`,
`/data1/lll/models/Qwen3-0.6B`, and `cutoff_len=32768`.

- Input samples: 731
- Trainable records: 562
- Failed records: 169
- Failure reason: all `over_cutoff_len`
- Max token count: 99,354
- Max token count among trainable records: 32,637

Overlength cases by directory:

- `sphinx-doc__sphinx-8548`: 75
- `sphinx-doc__sphinx-10673`: 43
- `django__django-12308`: 28
- `sphinx-doc__sphinx-8056`: 20
- `sphinx-doc__sphinx-8551`: 3

The same script completed real LLaMA-Factory LoRA smoke trains over the 562 fit
records with `max_steps=1`.

- Earlier output:
  `/tmp/llama_factory_selected_v1_train/run_20260805_002437/lora/adapter_model.safetensors`
- Latest rerun output:
  `/tmp/llama_factory_selected_v1_train_latest/run_20260805_003805/lora/adapter_model.safetensors`
