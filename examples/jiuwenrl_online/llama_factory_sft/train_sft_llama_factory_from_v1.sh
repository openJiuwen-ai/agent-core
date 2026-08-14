#!/usr/bin/env bash
set -euo pipefail

# LLaMA-Factory LoRA SFT from V1/sft-sample trajectory data.
# Usage:
#   CONDA_ENV=openjiuwen-sft TRAIN_GPU=4,5,6,7 \
#     bash train_sft_llama_factory_from_v1.sh /path/to/trajectory_dir

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${JIUWENRL_ROOT}/../.." && pwd)"

: "${ONLINE_RL_CONDA_ENV:=openjiuwen-sft}"
export ONLINE_RL_CONDA_ENV

# shellcheck disable=SC1091
source "${JIUWENRL_ROOT}/deploy_scripts/online_rl_local_env.sh"

if [[ $# -gt 0 ]]; then
  SAMPLES_PATH="$1"
  shift
else
  SAMPLES_PATH="${SFT_V1_DATA_PATH:-/data1/lll/workspace/swe-dataset/selected_datasets_llamav1}"
fi

cd "${WORKSPACE_ROOT}"

# shellcheck disable=SC1090
source "${CONDA_SH}"
conda activate "${CONDA_ENV:-openjiuwen-sft}"

export PYTHONPATH="${PYTHONPATH_VALUE}"
export PYTHONUNBUFFERED=1

exec python "${SCRIPT_DIR}/train_sft_llama_factory_from_v1.py" "${SAMPLES_PATH}" "$@"
