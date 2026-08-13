#!/usr/bin/env bash
set -euo pipefail

# Direct hard-label SFT from speculative trajectory JSON.
# Usage:
#   STUDENT_MODEL_PATH=/path/to/small/model TRAIN_GPU=4,5,6,7 \
#     bash sft_only/train_sft_from_speculative_samples.sh sft_only/speculative_sft_sample_trajectory.json

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${JIUWENRL_ROOT}/../../.." && pwd)"

# shellcheck disable=SC1091
source "${JIUWENRL_ROOT}/deploy_scripts/online_rl_local_env.sh"

SAMPLES_JSON="${1:-${SCRIPT_DIR}/speculative_sft_sample_trajectory.json}"
if [[ "${SAMPLES_JSON}" != /* ]]; then
  SAMPLES_JSON="$(cd "$(dirname "${SAMPLES_JSON}")" && pwd)/$(basename "${SAMPLES_JSON}")"
fi

cd "${WORKSPACE_ROOT}"

# shellcheck disable=SC1090
source "${CONDA_SH}"
conda activate "${CONDA_ENV}"

export PYTHONPATH="${PYTHONPATH_VALUE}"
export PYTHONUNBUFFERED=1
export "${ONLINE_RL_VISIBLE_DEVICES_ENV}=${TRAIN_GPU}"
export ONLINE_RL_VISIBLE_DEVICES_ENV

: "${STUDENT_MODEL_PATH:=${MODEL_PATH}}"
: "${SFT_OUTPUT_DIR:=${JIUWENRL_ROOT}/records/speculative_sft}"
: "${SFT_MAX_LENGTH:=4096}"
: "${SFT_MAX_TOKEN_LEN_PER_GPU:=${SFT_MAX_LENGTH}}"
: "${SFT_LORA_RANK:=16}"
: "${SFT_LORA_ALPHA:=32}"
: "${SFT_ULYSSES_SP:=1}"
: "${SFT_DTYPE:=bfloat16}"

exec python "${SCRIPT_DIR}/train_sft_from_speculative_trajectory_json.py" \
  "${SAMPLES_JSON}" \
  --student-model-path "${STUDENT_MODEL_PATH}" \
  --train-gpu "${TRAIN_GPU}" \
  --output-dir "${SFT_OUTPUT_DIR}" \
  --max-length "${SFT_MAX_LENGTH}" \
  --max-token-len-per-gpu "${SFT_MAX_TOKEN_LEN_PER_GPU}" \
  --lora-rank "${SFT_LORA_RANK}" \
  --lora-alpha "${SFT_LORA_ALPHA}" \
  --ulysses-sp "${SFT_ULYSSES_SP}" \
  --dtype "${SFT_DTYPE}"
