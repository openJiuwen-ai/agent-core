#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AGENT_CORE="$(cd -- "${SCRIPT_DIR}/../../../../.." && pwd)"
AIGW_REPO="${AIGW_REPO:-$(cd -- "${AGENT_CORE}/.." && pwd)/AgentBox-Platform/AgentBox-Platform/AgentInfra/Adapter}"
AIGW_BIN="${AIGW_BIN:-${AIGW_REPO}/output/aigw/aigw}"

MODULE_BACKUP="$(mktemp -d)"
cp "${AIGW_REPO}/go.mod" "${AIGW_REPO}/go.sum" "${MODULE_BACKUP}/"
restore_modules() {
  [[ -d "${MODULE_BACKUP}" ]] || return 0
  cp "${MODULE_BACKUP}/go.mod" "${MODULE_BACKUP}/go.sum" "${AIGW_REPO}/"
  rm -rf -- "${MODULE_BACKUP}"
}
exit_for_signal() {
  exit "$1"
}
trap restore_modules EXIT
trap 'exit_for_signal 129' HUP
trap 'exit_for_signal 130' INT
trap 'exit_for_signal 143' TERM

echo "Building and testing AgentBox Adapter (AIGW)"
(cd "${AIGW_REPO}" && bash build.sh --ut)
restore_modules
trap - EXIT HUP INT TERM

cd "${AGENT_CORE}"
export AIGW_REPO AIGW_BIN
exec python -m pytest -q \
  tests/unit_tests/agent_evolving/agent_rl/online \
  tests/system_tests/agent_evolving/agent_rl/online/test_aigw_system.py \
  "$@"
