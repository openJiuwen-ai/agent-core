#!/usr/bin/env bash
set -euo pipefail

# Clean only the local Jiuwen online-RL runtime state. This removes Redis
# trajectories, Ray state, runtime dirs, and all local LoRA artifacts.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${JIUWENRL_ROOT}/../../.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/online_rl_local_env.sh"

cd "${WORKSPACE_ROOT}"

echo "[clean] stopping online-RL services"
"${SCRIPT_DIR}/online_rl_backend.sh" stop || true

online_rl_activate_python_env

echo "[clean] deleting online-RL Redis keys from ${REDIS_URL}"
for pattern in 'rl:traj*' 'rl:sft_raw*' 'rl:sft_sample*' 'rl:training_task*' 'pending_judge*'; do
  redis-cli -u "${REDIS_URL}" --scan --pattern "${pattern}" | \
    xargs -r redis-cli -u "${REDIS_URL}" DEL >/dev/null
done

echo "[clean] stopping Ray"
ray stop --force || true

echo "[clean] removing runtime dirs"
rm -rf "${ONLINE_RL_SCRIPT_DIR}/logs" \
       "${ONLINE_RL_SCRIPT_DIR}/records" \
       "${ONLINE_RL_SCRIPT_DIR}/.jiuwenswarm-online"

echo "[clean] removing local LoRA artifacts"
rm -rf "${ONLINE_RL_SCRIPT_DIR}/lora_repo"
shopt -s nullglob
for stale_lora in "${ONLINE_RL_SCRIPT_DIR}"/lora_repo.stale.*; do
  if ! rm -rf "${stale_lora}"; then
    echo "[clean] warning: failed to remove ${stale_lora}; check file ownership/permissions" >&2
  fi
done
shopt -u nullglob

mkdir -p "${ONLINE_RL_SCRIPT_DIR}/logs" \
         "${ONLINE_RL_SCRIPT_DIR}/records" \
         "${ONLINE_RL_SCRIPT_DIR}/lora_repo" \
         "${ONLINE_RL_SCRIPT_DIR}/.jiuwenswarm-online"

echo "[clean] done"
