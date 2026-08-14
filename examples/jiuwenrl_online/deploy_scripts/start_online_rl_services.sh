#!/usr/bin/env bash
set -euo pipefail

# Start the full local online-RL backend stack and print health checks.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${JIUWENRL_ROOT}/../../.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/online_rl_local_env.sh"

# Keep this user-facing entrypoint aligned with online_rl_backend.sh. These
# defaults can still be overridden by exporting env vars before invoking this
# script.
: "${USE_RL_ONLINE_RAIL:=1}"
: "${ENABLE_TRAJECTORY_COLLECTION:=false}"
: "${TRAJECTORY_GATEWAY_URL:=${GATEWAY_URL}}"
: "${TRAJECTORY_TOKENIZER_PATH:=${MODEL_PATH}}"
: "${LORA_REPO_ROOT:=${LORA_REPO}}"
: "${LORA_DEFAULT_POLICY:=latest_by_user}"
if [[ "${ENABLE_SANDBOX_PLUGINS:-0}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  : "${ONLINE_RL_ROLLOUTER:=examples.jiuwenrl_online.sandbox_plugins:BasicSandboxRollouter}"
fi

export USE_RL_ONLINE_RAIL ENABLE_TRAJECTORY_COLLECTION
export TRAJECTORY_GATEWAY_URL TRAJECTORY_TOKENIZER_PATH
export LORA_REPO_ROOT LORA_DEFAULT_POLICY
export ONLINE_RL_ROLLOUTER

cd "${WORKSPACE_ROOT}"

echo "[start] REDIS_URL=${REDIS_URL}"
echo "[start] VLLM_GPU=${VLLM_GPU} TRAIN_GPU=${TRAIN_GPU}"
echo "[start] TRAIN_THRESHOLD=${TRAIN_THRESHOLD} TRAJECTORY_BATCH_SIZE=${TRAJECTORY_BATCH_SIZE}"
echo "[start] USE_RL_ONLINE_RAIL=${USE_RL_ONLINE_RAIL} TRAJECTORY_GATEWAY_URL=${TRAJECTORY_GATEWAY_URL}"
echo "[start] LORA_REPO_ROOT=${LORA_REPO_ROOT} LORA_DEFAULT_POLICY=${LORA_DEFAULT_POLICY}"
echo "[start] ONLINE_RL_ROLLOUTER=${ONLINE_RL_ROLLOUTER:-}"
echo "[start] USE_CONTEXT_COMPRESSION_RAIL=${USE_CONTEXT_COMPRESSION_RAIL} ONLINE_RL_ENABLE_JUDGE=${ONLINE_RL_ENABLE_JUDGE}"

"${SCRIPT_DIR}/online_rl_backend.sh" start

echo
echo "[status]"
"${SCRIPT_DIR}/online_rl_backend.sh" status

echo
echo "[gateway stats]"
curl -sf "${GATEWAY_URL}/v1/gateway/stats"
echo

echo
echo "[urls]"
echo "Gateway:  ${GATEWAY_URL}"
echo "vLLM:     ${VLLM_URL}"
echo "Web UI:   ${FRONTEND_URL}"
echo "WS:       ${ONLINE_RL_WS_URL}"
