#!/usr/bin/env bash
set -euo pipefail

# Backward-compatible wrapper around the Python direct-training entrypoint.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${JIUWENRL_ROOT}/../../.." && pwd)"

# shellcheck disable=SC1091
source "${JIUWENRL_ROOT}/deploy_scripts/online_rl_local_env.sh"

SAMPLES_JSON="${1:-${SCRIPT_DIR}/direct_train_trajectories.json}"

cd "${WORKSPACE_ROOT}"

# shellcheck disable=SC1090
source "${CONDA_SH}"
conda activate "${CONDA_ENV}"

export PYTHONPATH="${PYTHONPATH_VALUE}"
export PYTHONUNBUFFERED=1
export "${ONLINE_RL_VISIBLE_DEVICES_ENV}=${TRAIN_GPU}"
export ONLINE_RL_VISIBLE_DEVICES_ENV

exec python "${SCRIPT_DIR}/train_online_rl_from_trajectory_json.py" "${SAMPLES_JSON}"
