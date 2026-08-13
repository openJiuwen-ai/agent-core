#!/usr/bin/env bash
set -euo pipefail

# Smoke test for online-RL gateway management APIs.
# Start services first:
#   bash start_online_rl_services.sh
# Then run:
#   bash test_manage_api.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/online_rl_local_env.sh"

GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:18080}"
WORK_DIR="$(mktemp -d /tmp/online_rl_manage_api.XXXXXX)"
LAST_BODY="${WORK_DIR}/last_response.json"
trap 'rm -rf "${WORK_DIR}"' EXIT

AUTH_ARGS=()
if [[ -n "${GATEWAY_API_KEY:-}" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${GATEWAY_API_KEY}")
fi

api_json() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local expected="${4:-200}"
  local status
  if [[ -n "${data}" ]]; then
    status="$(curl -sS -o "${LAST_BODY}" -w '%{http_code}' \
      -X "${method}" "${GATEWAY_URL}${path}" \
      -H 'Content-Type: application/json' "${AUTH_ARGS[@]}" \
      --data-binary @"${data}")"
  else
    status="$(curl -sS -o "${LAST_BODY}" -w '%{http_code}' \
      -X "${method}" "${GATEWAY_URL}${path}" \
      "${AUTH_ARGS[@]}")"
  fi
  if [[ "${status}" != "${expected}" ]]; then
    echo "[FAIL] ${method} ${path}: expected ${expected}, got ${status}" >&2
    cat "${LAST_BODY}" >&2 || true
    exit 1
  fi
}

assert_json() {
  local expr="$1"
  python - "$LAST_BODY" "$expr" <<'PY'
import json
import sys

path, expr = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
if not eval(expr, {"data": data}):
    raise SystemExit(f"assertion failed: {expr}\nresponse={json.dumps(data, ensure_ascii=False, indent=2)}")
PY
}

echo "[1/12] health"
api_json GET /v1/rl/health "" 200
assert_json "data['status'] == 'ready'"

cat > "${WORK_DIR}/trajectory_batch.json" <<'JSON'
{
  "protocol_version": "agent-rollout-v1",
  "source": "manage_api_test",
  "model_id": "manage-model",
  "base_model": "ManageBase",
  "trajectories": [
    {
      "trajectory_id": "manage-traj-001",
      "rollout_id": "manage-rollout-001",
      "session_id": "manage-session-001",
      "task_id": "coding",
      "user_id": "manage-user",
      "policy_version": "base",
      "steps": [
        {
          "step_id": "step-001",
          "turn_num": 1,
          "type": "llm",
          "request": {
            "messages": [
              {"role": "user", "content": "write a tiny add function"}
            ]
          },
          "response": {
            "content": "def add(a, b): return a + b",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 6, "completion_tokens": 8, "total_tokens": 14}
          },
          "token_trace": {
            "prompt_ids": [1, 2, 3, 4],
            "response_ids": [5, 6, 7],
            "response_logprobs": [-0.1, -0.2, -0.3],
            "response_mask": [1, 1, 1]
          },
          "metadata": {
            "reserved_review_status": null
          }
        }
      ],
      "reward": {"score": 0.8, "source": "manual", "details": {"pass_tests": true}},
      "metadata": {"case": "manage-api"}
    }
  ]
}
JSON

TRAJ_ID="manage-traj-001:0"

echo "[2/12] trajectory batchCreate"
api_json POST /v1/rl/trajectories:batchCreate "${WORK_DIR}/trajectory_batch.json" 200
assert_json "data['accepted'] == 1 and data['rejected'] == 0"

echo "[3/12] trajectory list"
api_json GET "/v1/rl/trajectories?user_id=manage-user&status=pending&limit=10" "" 200
assert_json "any(item['trajectory_id'] == 'manage-traj-001:0' for item in data['items'])"

echo "[4/12] trajectory get"
api_json GET "/v1/rl/trajectories/${TRAJ_ID}" "" 200
assert_json "data['trajectory_id'] == 'manage-traj-001:0' and data['status'] == 'pending'"

cat > "${WORK_DIR}/trajectory_patch.json" <<'JSON'
{
  "status": "failed",
  "metadata": {
    "reviewed_by": "test_manage_api",
    "reserved_human_label": null
  }
}
JSON

echo "[5/12] trajectory patch"
api_json PATCH "/v1/rl/trajectories/${TRAJ_ID}" "${WORK_DIR}/trajectory_patch.json" 200
assert_json "data['status'] == 'failed' and data['metadata']['reviewed_by'] == 'test_manage_api'"

echo "[6/12] trajectory stats"
api_json GET "/v1/rl/trajectories/stats?user_id=manage-user" "" 200
assert_json "data['by_status'].get('failed', 0) >= 1 and data['by_source'].get('manage_api_test', 0) >= 1"

mkdir -p "${WORK_DIR}/lora_a" "${WORK_DIR}/lora_b"
printf 'dummy-a\n' > "${WORK_DIR}/lora_a/adapter_model.safetensors"
printf '{"r": 8, "target_modules": ["q_proj"]}\n' > "${WORK_DIR}/lora_a/adapter_config.json"
printf 'dummy-b\n' > "${WORK_DIR}/lora_b/adapter_model.safetensors"
printf '{"r": 8, "target_modules": ["q_proj"]}\n' > "${WORK_DIR}/lora_b/adapter_config.json"

cat > "${WORK_DIR}/lora_a.json" <<JSON
{
  "model_id": "manage-user",
  "base_model": "ManageBase",
  "lora_path": "${WORK_DIR}/lora_a",
  "source": {"type": "manual_import", "reserved_job_id": null},
  "metrics": {"reward_mean": 0.7},
  "set_latest": true,
  "metadata": {"case": "manage-api"}
}
JSON

cat > "${WORK_DIR}/lora_b.json" <<JSON
{
  "model_id": "manage-user",
  "base_model": "ManageBase",
  "lora_path": "${WORK_DIR}/lora_b",
  "source": {"type": "manual_import", "reserved_job_id": null},
  "metrics": {"reward_mean": 0.9},
  "set_latest": true,
  "metadata": {"case": "manage-api"}
}
JSON

echo "[7/12] lora register v1"
api_json POST /v1/rl/lora "${WORK_DIR}/lora_a.json" 200
assert_json "data['lora_id'] == 'manage-user:v1' and data['status'] == 'latest'"

echo "[8/12] lora register v2"
api_json POST /v1/rl/lora "${WORK_DIR}/lora_b.json" 200
assert_json "data['lora_id'] == 'manage-user:v2' and data['status'] == 'latest'"

echo "[9/12] lora list/latest/get"
api_json GET "/v1/rl/lora?model_id=manage-user&limit=10" "" 200
assert_json "len(data['items']) >= 2"
api_json GET "/v1/rl/lora/latest?model_id=manage-user" "" 200
assert_json "data['lora_id'] == 'manage-user:v2'"
api_json GET "/v1/rl/lora/manage-user:v1" "" 200
assert_json "data['lora_id'] == 'manage-user:v1'"

echo "[10/12] lora setLatest"
api_json POST "/v1/rl/lora/manage-user:v1:setLatest" "" 200
assert_json "data['lora_id'] == 'manage-user:v1' and data['status'] == 'latest'"

echo "[11/12] lora latest delete conflict and non-latest delete"
api_json DELETE "/v1/rl/lora/manage-user:v1" "" 409
api_json DELETE "/v1/rl/lora/manage-user:v2" "" 200
assert_json "data['deleted'] is True"

echo "[12/12] trajectory delete"
api_json DELETE "/v1/rl/trajectories/${TRAJ_ID}" "" 200
assert_json "data['deleted'] is True"

echo "[PASS] online-RL management APIs are working at ${GATEWAY_URL}"
