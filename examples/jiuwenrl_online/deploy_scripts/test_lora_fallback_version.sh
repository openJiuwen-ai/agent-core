#!/usr/bin/env bash
set -euo pipefail

# Verify that latest-LoRA fallback is visible on both online-RL inference paths.
# Start services first:
#   bash online_rl_backend.sh start
# Then run:
#   bash test_lora_fallback_version.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/online_rl_local_env.sh"

WORK_DIR="$(mktemp -d /tmp/online_rl_lora_version.XXXXXX)"
trap 'rm -rf "${WORK_DIR}"' EXIT

GATEWAY_BODY="${WORK_DIR}/gateway_response.json"
VLLM_BODY="${WORK_DIR}/vllm_models.json"
NEW_LOG="${WORK_DIR}/jiuwenswarm_new.log"

require_http() {
  local name="$1"
  local url="$2"
  if ! curl -sf "${url}" >/dev/null; then
    echo "[FAIL] ${name} is not reachable: ${url}" >&2
    echo "Start services with: bash ${SCRIPT_DIR}/online_rl_backend.sh start" >&2
    exit 1
  fi
}

json_get() {
  local path="$1"
  local expr="$2"
  python - "$path" "$expr" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = json.load(f)
value = eval(sys.argv[2], {"data": data})
if value is None:
    value = ""
print(value)
PY
}

assert_json() {
  local path="$1"
  local expr="$2"
  python - "$path" "$expr" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = json.load(f)
if not eval(sys.argv[2], {"data": data}):
    raise SystemExit(
        "assertion failed: " + sys.argv[2] + "\nresponse="
        + json.dumps(data, ensure_ascii=False, indent=2)
    )
PY
}

echo "[1/5] service health"
require_http "gateway" "${GATEWAY_URL}/health"
require_http "vLLM" "${VLLM_URL}/v1/models"

echo "[2/5] gateway fallback response contains concrete LoRA version"
curl -sf "${GATEWAY_URL}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -H "x-user-id: ${WEB_USER_ID}" \
  --data-binary @- > "${GATEWAY_BODY}" <<JSON
{
  "model": "${MODEL_NAME}",
  "messages": [{"role": "user", "content": "只输出 OK"}],
  "temperature": 0,
  "top_p": 1,
  "max_tokens": 8
}
JSON
assert_json "${GATEWAY_BODY}" "data.get('model') == '${WEB_USER_ID}'"
assert_json "${GATEWAY_BODY}" "data.get('rl_lora', {}).get('model_id') == '${WEB_USER_ID}'"
assert_json "${GATEWAY_BODY}" "bool(data.get('rl_lora', {}).get('lora_id'))"
assert_json "${GATEWAY_BODY}" "bool(data.get('rl_lora', {}).get('version'))"
assert_json "${GATEWAY_BODY}" "bool(data.get('rl_lora', {}).get('path'))"

GATEWAY_LORA_ID="$(json_get "${GATEWAY_BODY}" "data['rl_lora']['lora_id']")"
GATEWAY_LORA_VERSION="$(json_get "${GATEWAY_BODY}" "data['rl_lora']['version']")"
GATEWAY_LORA_PATH="$(json_get "${GATEWAY_BODY}" "data['rl_lora']['path']")"
echo "      gateway rl_lora=${GATEWAY_LORA_ID} path=${GATEWAY_LORA_PATH}"

echo "[3/5] vLLM has user adapter loaded"
curl -sf "${VLLM_URL}/v1/models" > "${VLLM_BODY}"
assert_json "${VLLM_BODY}" "any(item.get('id') == '${WEB_USER_ID}' for item in data.get('data', []))"

echo "[4/5] jiuwenswarm direct path emits concrete LoRA version"
LOG_FILE="${LOG_DIR}/jiuwenswarm.log"
if [[ ! -f "${LOG_FILE}" ]]; then
  echo "[FAIL] missing jiuwenswarm log: ${LOG_FILE}" >&2
  exit 1
fi
START_LINE="$(wc -l < "${LOG_FILE}")"
ONLINE_RL_SESSION_ID="lora_version_test_$(date +%s)" \
  bash "${SCRIPT_DIR}/send_online_rl_msg.sh" "请只输出 OK。" >/dev/null
tail -n +"$((START_LINE + 1))" "${LOG_FILE}" > "${NEW_LOG}"

if ! rg -q "\\[RLOnlineRail\\] using latest LoRA user=${WEB_USER_ID} lora_id=${WEB_USER_ID}:[^ ]+ version=[^ ]+ path=" "${NEW_LOG}"; then
  echo "[FAIL] jiuwenswarm Rail log did not include concrete LoRA version" >&2
  cat "${NEW_LOG}" >&2
  exit 1
fi
if ! rg -q '"model_name": "'"${WEB_USER_ID}"'"' "${NEW_LOG}"; then
  echo "[FAIL] jiuwenswarm LLM request did not use LoRA adapter model ${WEB_USER_ID}" >&2
  cat "${NEW_LOG}" >&2
  exit 1
fi
RAIL_LINE="$(rg "\\[RLOnlineRail\\] using latest LoRA user=${WEB_USER_ID}" "${NEW_LOG}" | tail -1)"
echo "      rail ${RAIL_LINE}"

echo "[5/5] latest API is available for comparison"
curl -sf "${GATEWAY_URL}/v1/rl/lora/latest?model_id=${WEB_USER_ID}" > "${WORK_DIR}/latest.json"
assert_json "${WORK_DIR}/latest.json" "data.get('model_id') == '${WEB_USER_ID}'"
assert_json "${WORK_DIR}/latest.json" "bool(data.get('version'))"
echo "      latest $(json_get "${WORK_DIR}/latest.json" "data['lora_id']")"

echo "[PASS] LoRA fallback version is visible on gateway and jiuwenswarm paths; gateway version=${GATEWAY_LORA_VERSION}"
