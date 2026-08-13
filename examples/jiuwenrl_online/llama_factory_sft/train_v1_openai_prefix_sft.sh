#!/usr/bin/env bash
set -euo pipefail

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
  SAMPLES_PATH="${SFT_V1_OPENAI_DATA_PATH:-${SCRIPT_DIR}/v1data+v0train/data_openai}"
fi

cd "${WORKSPACE_ROOT}"

# shellcheck disable=SC1090
source "${CONDA_SH}"
conda activate "${CONDA_ENV:-openjiuwen-sft}"

export PYTHONPATH="${PYTHONPATH_VALUE}"
export PYTHONUNBUFFERED=1

exec python "${SCRIPT_DIR}/train_v1_openai_prefix_sft.py" "${SAMPLES_PATH}" "$@"
