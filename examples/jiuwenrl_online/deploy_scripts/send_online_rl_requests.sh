#!/usr/bin/env bash
set -euo pipefail

# Send a batch of messages through JiuwenClaw WebSocket. If no messages are
# provided, send eight default turns. This gives delayed reward enough turns to
# tolerate occasional slow/failed judge votes while still triggering training.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${JIUWENRL_ROOT}/../../.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/online_rl_local_env.sh"

cd "${WORKSPACE_ROOT}"

export ONLINE_RL_SESSION_ID="${ONLINE_RL_SESSION_ID:-manual_online_rl_batch}"

if [[ $# -gt 0 ]]; then
  prompts=("$@")
else
  prompts=(
    "第1轮，请只回复：1"
    "第2轮，请只回复：2"
    "第3轮，请只回复：3"
    "第4轮，请只回复：4"
    "第5轮，请只回复：5"
    "第6轮，请只回复：6"
    "第7轮，请只回复：7"
    "第8轮，用于结算上一轮奖励，请只回复：8"
  )
fi

echo "[send] session=${ONLINE_RL_SESSION_ID}"
for idx in "${!prompts[@]}"; do
  turn=$((idx + 1))
  echo
  echo "[send] turn ${turn}/${#prompts[@]}"
  "${SCRIPT_DIR}/send_online_rl_msg.sh" "${prompts[$idx]}"
done

echo
echo "[gateway stats]"
curl -sf "${GATEWAY_URL}/v1/gateway/stats"
echo

echo
echo "[recent scheduler evidence]"
tail -n 220 "${ONLINE_RL_SCRIPT_DIR}/logs/scheduler.log" | \
  rg "Triggering PPO training|Converted 4 samples|train_step metrics|Published PPO LoRA|hot-loaded" || true

echo
echo "[recent vLLM LoRA loads]"
tail -n 120 "${ONLINE_RL_SCRIPT_DIR}/logs/vllm.log" | \
  rg "Loaded new LoRA|POST /v1/load_lora_adapter" || true
