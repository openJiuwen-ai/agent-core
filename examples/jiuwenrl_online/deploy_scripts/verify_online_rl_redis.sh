#!/usr/bin/env bash
set -euo pipefail

# Verify the Redis service selected by online_rl_local_env.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/online_rl_local_env.sh"

# shellcheck disable=SC1090
source "${CONDA_SH}"
conda activate "${CONDA_ENV}"

key="online_rl:redis_verify:$$"
value="ok-$(date +%s)"

echo "[redis] REDIS_URL=${REDIS_URL}"
redis-cli -u "${REDIS_URL}" PING | grep -qx "PONG"
redis-cli -u "${REDIS_URL}" SET "${key}" "${value}" | grep -qx "OK"
got="$(redis-cli -u "${REDIS_URL}" GET "${key}")"
if [[ "${got}" != "${value}" ]]; then
  echo "[redis] GET mismatch: expected=${value} got=${got}" >&2
  exit 1
fi
redis-cli -u "${REDIS_URL}" DEL "${key}" >/dev/null
if [[ -n "$(redis-cli -u "${REDIS_URL}" GET "${key}")" ]]; then
  echo "[redis] DEL failed for ${key}" >&2
  exit 1
fi

echo "[redis] verify ok"
