#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/sft_mock_e2e_common.sh"

cleanup_all() {
  if [[ "${SFT_E2E_KEEP_ENV:-0}" != "1" ]]; then
    "${SCRIPT_DIR}/clean_sft_mock_e2e_env.sh" all >/dev/null 2>&1 || true
  fi
}

trap cleanup_all EXIT

"${SCRIPT_DIR}/clean_sft_mock_e2e_env.sh" all
"${SCRIPT_DIR}/start_sft_mock_backend.sh"
activate_sft_e2e_env

echo "[sft-e2e][stage2] collect original raw trajectories"
python "${JIUWENRL_ROOT}/sft_rollout/run_swe_task_rollout.py" \
  --cases "${SFT_E2E_CASES}" \
  --limit "${SFT_E2E_CASE_LIMIT}" \
  --offset "${SFT_E2E_CASE_OFFSET}" \
  --gateway-url "${SFT_E2E_GATEWAY_DOCKER_URL}" \
  --supervisor-url "${SFT_E2E_MOCK_DOCKER_URL}" \
  --supervisor-token "EMPTY" \
  --supervisor-model "${SFT_E2E_MODEL}" \
  --tenant-id "${SFT_E2E_USER_ID}" \
  --concurrency "${SFT_ROLLOUT_CONCURRENCY}" \
  --timeout "${SFT_E2E_TIMEOUT}"

python "${SCRIPT_DIR}/validate_sft_mock_e2e.py" \
  --phase raw \
  --redis-url "${REDIS_URL}" \
  --gateway-url "${SFT_E2E_GATEWAY_LOCAL_URL}" \
  --user-id "${SFT_E2E_USER_ID}" \
  --min-original-raw "${SFT_E2E_CASE_LIMIT}"

task_json="$(curl -sf -X POST "${SFT_E2E_GATEWAY_LOCAL_URL}/v1/training/tasks" \
  -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"${SFT_E2E_USER_ID}\",\"metadata\":{\"e2e\":\"sft_stage2_rollout\"}}")"
task_id="$(python -c 'import json,sys; print(json.load(sys.stdin)["task_id"])' <<< "${task_json}")"
echo "[sft-e2e][stage2] triggered task ${task_id}"

python "${SCRIPT_DIR}/validate_sft_mock_e2e.py" \
  --phase rollout \
  --redis-url "${REDIS_URL}" \
  --gateway-url "${SFT_E2E_GATEWAY_LOCAL_URL}" \
  --user-id "${SFT_E2E_USER_ID}" \
  --task-id "${task_id}" \
  --tmp-root "${SFT_E2E_TMP_ROOT}" \
  --min-original-raw "${SFT_E2E_CASE_LIMIT}" \
  --wait-timeout 600

echo "[sft-e2e][stage2] passed"
