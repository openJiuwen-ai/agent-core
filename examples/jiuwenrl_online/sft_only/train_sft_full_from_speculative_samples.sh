#!/usr/bin/env bash
set -euo pipefail

# Full-parameter hard-label SFT from speculative trajectory JSON.
# Defaults to all-parameter SFT. Set SFT_FULL_TRAIN_LAYER_SPEC to train only
# selected transformer layers while still saving a full HuggingFace checkpoint.
#
# Examples:
#   TRAIN_GPU=4,5,6,7 bash train_sft_full_from_speculative_samples.sh samples.json
#   SFT_FULL_TRAIN_LAYER_SPEC=last:4 TRAIN_GPU=4,5,6,7 \
#     bash train_sft_full_from_speculative_samples.sh samples.json
#   ONLINE_RL_DEVICE_BACKEND=ascend TRAIN_GPU=4,5,6,7 \
#     bash train_sft_full_from_speculative_samples.sh samples.json

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${JIUWENRL_ROOT}/../../.." && pwd)"

# shellcheck disable=SC1091
source "${JIUWENRL_ROOT}/deploy_scripts/online_rl_local_env.sh"

: "${SFT_FULL_PROFILE:=auto}"
if [[ "${SFT_FULL_PROFILE}" == "auto" ]]; then
  case "${ONLINE_RL_DEVICE_BACKEND}" in
    ascend|npu)
      SFT_FULL_PROFILE="a5_27b"
      ;;
    *)
      SFT_FULL_PROFILE="gpu_smoke"
      ;;
  esac
fi

if [[ $# -gt 0 ]]; then
  SAMPLES_JSON="$1"
else
  case "${SFT_FULL_PROFILE}" in
    a5_27b)
      SAMPLES_JSON="${SCRIPT_DIR}/../train_only/long_context_4x50k_trajectories.json"
      ;;
    *)
      SAMPLES_JSON="${SCRIPT_DIR}/speculative_sft_sample_trajectory.json"
      ;;
  esac
fi
if [[ "${SAMPLES_JSON}" != /* ]]; then
  SAMPLES_JSON="$(cd "$(dirname "${SAMPLES_JSON}")" && pwd)/$(basename "${SAMPLES_JSON}")"
fi

cd "${WORKSPACE_ROOT}"

# shellcheck disable=SC1090
source "${CONDA_SH}"
conda activate "${CONDA_ENV}"

export PYTHONPATH="${PYTHONPATH_VALUE}"
export PYTHONUNBUFFERED=1
case "${SFT_FULL_PROFILE}" in
  a5_27b)
    if [[ -z "${STUDENT_MODEL_PATH:-}" && "${MODEL_PATH}" == "/data1/lll/models/Qwen3-4B-Thinking-2507" ]]; then
      STUDENT_MODEL_PATH="/data1/lll/models/Qwen3.5-27B"
    fi
    : "${TRAIN_GPU:=4,5,6,7}"
    : "${SFT_FULL_MAX_LENGTH:=65536}"
    : "${SFT_FULL_MAX_TOKEN_LEN_PER_GPU:=65536}"
    : "${SFT_FULL_ULYSSES_SP:=4}"
    : "${SFT_FULL_DTYPE:=bfloat16}"
    : "${SFT_FULL_MODEL_DTYPE:=bfloat16}"
    : "${SFT_FULL_TRAIN_LAYER_SPEC:=last:4}"
    : "${SFT_FULL_SAVE_HF_MODEL:=0}"
    : "${SFT_FULL_PARAM_OFFLOAD:=0}"
    : "${SFT_FULL_OPTIMIZER_OFFLOAD:=1}"
    : "${SFT_FULL_ACTIVATION_OFFLOAD:=1}"
    : "${SFT_FULL_USE_TORCH_COMPILE:=0}"
    : "${SFT_FULL_FSDP_SIZE:=-1}"
    : "${SFT_FULL_USE_ORIG_PARAMS:=1}"
    ;;
  gpu_smoke)
    : "${SFT_FULL_MAX_LENGTH:=4096}"
    : "${SFT_FULL_MAX_TOKEN_LEN_PER_GPU:=${SFT_FULL_MAX_LENGTH}}"
    : "${SFT_FULL_ULYSSES_SP:=1}"
    : "${SFT_FULL_DTYPE:=bfloat16}"
    : "${SFT_FULL_MODEL_DTYPE:=bfloat16}"
    : "${SFT_FULL_TRAIN_LAYER_SPEC:=all}"
    : "${SFT_FULL_SAVE_HF_MODEL:=1}"
    : "${SFT_FULL_PARAM_OFFLOAD:=0}"
    : "${SFT_FULL_OPTIMIZER_OFFLOAD:=0}"
    : "${SFT_FULL_ACTIVATION_OFFLOAD:=0}"
    : "${SFT_FULL_USE_TORCH_COMPILE:=1}"
    : "${SFT_FULL_FSDP_SIZE:=-1}"
    : "${SFT_FULL_USE_ORIG_PARAMS:=0}"
    ;;
  *)
    ;;
esac

export "${ONLINE_RL_VISIBLE_DEVICES_ENV}=${TRAIN_GPU}"
export ONLINE_RL_VISIBLE_DEVICES_ENV

: "${STUDENT_MODEL_PATH:=${MODEL_PATH}}"
: "${SFT_FULL_OUTPUT_DIR:=${JIUWENRL_ROOT}/records/speculative_sft_full}"
: "${SFT_FULL_MAX_LENGTH:=65536}"
: "${SFT_FULL_MAX_TOKEN_LEN_PER_GPU:=${SFT_FULL_MAX_LENGTH}}"
: "${SFT_FULL_ULYSSES_SP:=4}"
: "${SFT_FULL_DTYPE:=bfloat16}"
: "${SFT_FULL_MODEL_DTYPE:=${SFT_FULL_DTYPE}}"
: "${SFT_FULL_TRAIN_LAYER_SPEC:=last:4}"
: "${SFT_FULL_SAVE_HF_MODEL:=0}"
: "${SFT_FULL_TRAIN_EMBEDDINGS:=0}"
: "${SFT_FULL_TRAIN_LM_HEAD:=0}"
: "${SFT_FULL_PARAM_OFFLOAD:=0}"
: "${SFT_FULL_OPTIMIZER_OFFLOAD:=1}"
: "${SFT_FULL_ACTIVATION_OFFLOAD:=1}"
: "${SFT_FULL_USE_TORCH_COMPILE:=0}"
: "${SFT_FULL_FSDP_SIZE:=-1}"
: "${SFT_FULL_USE_ORIG_PARAMS:=1}"

echo "[spec-full-sft] profile=${SFT_FULL_PROFILE} backend=${ONLINE_RL_DEVICE_BACKEND} train_gpu=${TRAIN_GPU}"
echo "[spec-full-sft] model=${STUDENT_MODEL_PATH} samples=${SAMPLES_JSON}"
echo "[spec-full-sft] max_length=${SFT_FULL_MAX_LENGTH} sp=${SFT_FULL_ULYSSES_SP} layer_spec=${SFT_FULL_TRAIN_LAYER_SPEC}"

exec python "${SCRIPT_DIR}/train_sft_full_from_speculative_trajectory_json.py" \
  "${SAMPLES_JSON}" \
  --student-model-path "${STUDENT_MODEL_PATH}" \
  --train-gpu "${TRAIN_GPU}" \
  --output-dir "${SFT_FULL_OUTPUT_DIR}" \
  --max-length "${SFT_FULL_MAX_LENGTH}" \
  --max-token-len-per-gpu "${SFT_FULL_MAX_TOKEN_LEN_PER_GPU}" \
  --ulysses-sp "${SFT_FULL_ULYSSES_SP}" \
  --dtype "${SFT_FULL_DTYPE}" \
  --model-dtype "${SFT_FULL_MODEL_DTYPE}" \
  --train-layer-spec "${SFT_FULL_TRAIN_LAYER_SPEC}" \
  --save-hf-model "${SFT_FULL_SAVE_HF_MODEL}" \
  --fsdp-size "${SFT_FULL_FSDP_SIZE}" \
  --use-orig-params "${SFT_FULL_USE_ORIG_PARAMS}" \
  --use-torch-compile "${SFT_FULL_USE_TORCH_COMPILE}" \
  $([[ "${SFT_FULL_PARAM_OFFLOAD}" == "1" ]] && printf '%s' "--param-offload") \
  $([[ "${SFT_FULL_OPTIMIZER_OFFLOAD}" == "1" ]] && printf '%s' "--optimizer-offload") \
  $([[ "${SFT_FULL_ACTIVATION_OFFLOAD}" == "1" ]] && printf '%s' "--activation-offload")
