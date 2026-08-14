#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_CORE_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
WORKSPACE_ROOT="$(cd "${AGENT_CORE_ROOT}/.." && pwd)"
JIUWENRL_ENV="${AGENT_CORE_ROOT}/examples/jiuwenrl_online/deploy_scripts/online_rl_local_env.sh"
USER_SET_TRAIN_GPU="${TRAIN_GPU+x}"

if [[ -f "${JIUWENRL_ENV}" ]]; then
  # shellcheck disable=SC1090
  source "${JIUWENRL_ENV}"
fi

if [[ -n "${CONDA_SH:-}" && -f "${CONDA_SH}" && -n "${CONDA_ENV:-}" ]]; then
  # shellcheck disable=SC1090
  source "${CONDA_SH}"
  conda activate "${CONDA_ENV}"
fi

export PYTHONPATH="${AGENT_CORE_ROOT}:${WORKSPACE_ROOT}/jiuwenclaw:${PYTHONPATH:-}"
export PYTHONUNBUFFERED=1
: "${ST_TEST_RUN_TRAINING:=1}"
export ST_TEST_RUN_TRAINING

case "${ONLINE_RL_DEVICE_BACKEND:-cuda}" in
  ascend|npu)
    : "${ONLINE_RL_FSDP_MODEL_DTYPE:=bf16}"
    ;;
  *)
    : "${ONLINE_RL_FSDP_MODEL_DTYPE:=fp16}"
    if [[ -z "${USER_SET_TRAIN_GPU}" ]]; then
      : "${ST_TEST_TRAIN_GPU:=6,7}"
      export TRAIN_GPU="${ST_TEST_TRAIN_GPU}"
    fi
    ;;
esac
export ONLINE_RL_FSDP_MODEL_DTYPE

auto_start_services_if_needed() {
  : "${ST_TEST_AUTO_START_SERVICES:=1}"
  export ST_TEST_AUTO_START_SERVICES
  if [[ "${ST_TEST_AUTO_START_SERVICES}" != "1" ]]; then
    return
  fi

  local health_url="${VLLM_URL%/}/health"
  if curl -sf "${health_url}" >/dev/null 2>&1; then
    return
  fi

  local start_script="${AGENT_CORE_ROOT}/examples/jiuwenrl_online/deploy_scripts/start_online_rl_services.sh"
  if [[ ! -x "${start_script}" && ! -f "${start_script}" ]]; then
    echo "[st-test] vLLM is not healthy at ${health_url}, and start script is missing: ${start_script}" >&2
    return
  fi

  echo "[st-test] vLLM is not healthy at ${health_url}; starting online-RL services"
  bash "${start_script}"
}

auto_start_services_if_needed

cd "${AGENT_CORE_ROOT}"
if [[ "$#" -eq 0 ]]; then
  python -m pytest -o addopts='' -q "${SCRIPT_DIR}"
else
  pytest_args=()
  targets=()
  for arg in "$@"; do
    if [[ "${arg}" == -* ]]; then
      pytest_args+=("${arg}")
    elif [[ "${arg}" == /* || "${arg}" == */* ]]; then
      targets+=("${arg}")
    else
      targets+=("${SCRIPT_DIR}/${arg}")
    fi
  done
  if [[ "${#targets[@]}" -eq 0 ]]; then
    targets=("${SCRIPT_DIR}")
  fi
  python -m pytest -o addopts='' -q "${pytest_args[@]}" "${targets[@]}"
fi
