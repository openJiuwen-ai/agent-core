#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/sft_mock_e2e_common.sh"

stop_one() {
  local name="$1"
  local pid_file="${SFT_E2E_PID_DIR}/${name}.pid"
  [[ -f "${pid_file}" ]] || return 0
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]]; then
    kill -TERM "${pid}" >/dev/null 2>&1 || true
  fi
}

clear_redis() {
  activate_sft_e2e_env
  python - <<'PY'
import os
import redis

patterns = [
    "rl:sft_raw*",
    "rl:sft_sample*",
    "rl:training_task*",
    "rl:training_tasks",
    "rl:training_task_active",
    "rl:task*",
]
r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
keys = []
for pattern in patterns:
    keys.extend(list(r.scan_iter(pattern)))
print(f"[sft-e2e] redis delete keys={len(keys)}")
if keys:
    r.delete(*keys)
PY
}

case "${1:-all}" in
  redis)
    clear_redis
    ;;
  processes)
    stop_one scheduler
    stop_one gateway
    stop_one mock_openai
    sleep 2
    ;;
  all|"")
    stop_one scheduler
    stop_one gateway
    stop_one mock_openai
    sleep 2
    clear_redis
    rm -rf "${SFT_E2E_RECORD_DIR:?}"/* "${SFT_E2E_LORA_REPO:?}"/* "${SFT_E2E_TMP_ROOT:?}"/*
    ;;
  *)
    echo "usage: $(basename "$0") [all|redis|processes]" >&2
    exit 2
    ;;
esac
