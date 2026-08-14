#!/usr/bin/env bash
set -euo pipefail

SFT_E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SFT_E2E_DIR}/.." && pwd)"
DEPLOY_DIR="${JIUWENRL_ROOT}/deploy_scripts"
AGENT_CORE_ROOT="$(cd "${JIUWENRL_ROOT}/../.." && pwd)"
WORKSPACE_ROOT="$(cd "${AGENT_CORE_ROOT}/.." && pwd)"

# shellcheck disable=SC1091
source "${DEPLOY_DIR}/online_rl_local_env.sh"

: "${SFT_E2E_CONDA_ENV:=${CONDA_ENV}}"
: "${SFT_E2E_CONDA_SH:=${CONDA_SH}}"
: "${SFT_E2E_HOST_IP:=$(hostname -I | tr ' ' '\n' | awk '/^172\./ {print; found=1; exit} END {if (!found) print "127.0.0.1"}')}"
: "${SFT_E2E_GATEWAY_HOST:=0.0.0.0}"
: "${SFT_E2E_GATEWAY_PORT:=18180}"
: "${SFT_E2E_MOCK_HOST:=0.0.0.0}"
: "${SFT_E2E_MOCK_PORT:=18102}"
: "${SFT_E2E_MODEL:=mock-supervisor}"
: "${SFT_E2E_USER_ID:=local-web-user}"
: "${SFT_E2E_CASES:=/data1/lll/workspace/sft_train_demo/swebench_verified_mini_docker_model.md}"
: "${SFT_E2E_CONCURRENCY:=4}"
: "${SFT_E2E_CASE_LIMIT:=${SFT_E2E_CONCURRENCY}}"
: "${SFT_E2E_CASE_OFFSET:=0}"
: "${SFT_E2E_TIMEOUT:=300}"
: "${SFT_E2E_LOG_DIR:=${SFT_E2E_DIR}/logs}"
: "${SFT_E2E_PID_DIR:=${SFT_E2E_LOG_DIR}/pids}"
: "${SFT_E2E_RECORD_DIR:=${SFT_E2E_DIR}/records}"
: "${SFT_E2E_LORA_REPO:=${SFT_E2E_DIR}/lora_repo}"
: "${SFT_E2E_TMP_ROOT:=/tmp/agent_rl_online_sft_mock_e2e}"
: "${SFT_E2E_DRY_RUN:=1}"
: "${SFT_E2E_TRAIN_GPU:=${TRAIN_GPU}}"
: "${SFT_E2E_TRAINER_COMMAND:=${SFT_TRAINER_COMMAND}}"
: "${SFT_E2E_JIUWENCLAW_HOST_PATH:=}"
if [[ -z "${SFT_E2E_JIUWENCLAW_HOST_PATH}" ]]; then
  if [[ -d "$(cd "${WORKSPACE_ROOT}/.." && pwd)/refactor/jiuwenclaw" ]]; then
    SFT_E2E_JIUWENCLAW_HOST_PATH="$(cd "${WORKSPACE_ROOT}/.." && pwd)/refactor/jiuwenclaw"
  elif [[ -d "${WORKSPACE_ROOT}/jiuwenclaw" ]]; then
    SFT_E2E_JIUWENCLAW_HOST_PATH="${WORKSPACE_ROOT}/jiuwenclaw"
  elif [[ -d "$(cd "${WORKSPACE_ROOT}/.." && pwd)/jiuwenclaw" ]]; then
    SFT_E2E_JIUWENCLAW_HOST_PATH="$(cd "${WORKSPACE_ROOT}/.." && pwd)/jiuwenclaw"
  else
    SFT_E2E_JIUWENCLAW_HOST_PATH="${WORKSPACE_ROOT}/jiuwenclaw"
  fi
fi
: "${SFT_E2E_PYTHONPATH:=${AGENT_CORE_ROOT}:${SFT_E2E_JIUWENCLAW_HOST_PATH}}"
export SFT_E2E_JIUWENCLAW_HOST_PATH
export SFT_E2E_DRY_RUN SFT_E2E_TRAIN_GPU SFT_E2E_TRAINER_COMMAND
export SFT_DOCKER_JIUWENCLAW_HOST_PATH="${SFT_DOCKER_JIUWENCLAW_HOST_PATH:-${SFT_E2E_JIUWENCLAW_HOST_PATH}}"
export SFT_ROLLOUT_CONCURRENCY="${SFT_ROLLOUT_CONCURRENCY:-${SFT_E2E_CONCURRENCY}}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export HF_DATASETS_OFFLINE="${HF_DATASETS_OFFLINE:-1}"

SFT_E2E_GATEWAY_LOCAL_URL="http://127.0.0.1:${SFT_E2E_GATEWAY_PORT}"
SFT_E2E_GATEWAY_DOCKER_URL="http://${SFT_E2E_HOST_IP}:${SFT_E2E_GATEWAY_PORT}"
SFT_E2E_MOCK_LOCAL_URL="http://127.0.0.1:${SFT_E2E_MOCK_PORT}"
SFT_E2E_MOCK_DOCKER_URL="http://${SFT_E2E_HOST_IP}:${SFT_E2E_MOCK_PORT}"

mkdir -p "${SFT_E2E_LOG_DIR}" "${SFT_E2E_PID_DIR}" "${SFT_E2E_RECORD_DIR}" "${SFT_E2E_LORA_REPO}"

activate_sft_e2e_env() {
  # shellcheck disable=SC1090
  source "${SFT_E2E_CONDA_SH}"
  conda activate "${SFT_E2E_CONDA_ENV}"
  export PYTHONPATH="${SFT_E2E_PYTHONPATH}"
  export PYTHONUNBUFFERED=1
}

sft_e2e_pid_running() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] || return 1
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1
}

sft_e2e_wait_health() {
  local name="$1"
  local url="$2"
  local timeout="${3:-120}"
  echo "[sft-e2e] waiting for ${name}: ${url}"
  for _ in $(seq 1 "${timeout}"); do
    curl -sf "${url}" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "[sft-e2e] ${name} did not become healthy; log tail:" >&2
  tail -100 "${SFT_E2E_LOG_DIR}/${name}.log" >&2 || true
  return 1
}

sft_e2e_start_detached() {
  local name="$1"
  local body="$2"
  local pid_file="${SFT_E2E_PID_DIR}/${name}.pid"
  local run_file="${SFT_E2E_PID_DIR}/run_${name}.sh"
  local log_file="${SFT_E2E_LOG_DIR}/${name}.log"
  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' 'set -euo pipefail'
    printf 'cd %q\n' "${AGENT_CORE_ROOT}"
    printf 'source %q\n' "${SFT_E2E_CONDA_SH}"
    printf 'conda activate %q\n' "${SFT_E2E_CONDA_ENV}"
    printf 'export PYTHONPATH=%q\n' "${SFT_E2E_PYTHONPATH}"
    printf '%s\n' 'export PYTHONUNBUFFERED=1'
    printf '%s\n' "${body}"
  } > "${run_file}"
  chmod +x "${run_file}"
  setsid nohup bash "${run_file}" >> "${log_file}" 2>&1 &
  echo "$!" > "${pid_file}"
  echo "[sft-e2e] started ${name}, pid=$!, log=${log_file}"
}
