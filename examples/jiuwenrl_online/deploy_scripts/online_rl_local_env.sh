#!/usr/bin/env bash
# Source this file from local online-RL scripts to keep machine-specific
# paths, ports, and cross-script URLs in one place.

ONLINE_RL_DEPLOY_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ONLINE_RL_SCRIPT_DIR="$(cd "${ONLINE_RL_DEPLOY_SCRIPT_DIR}/.." && pwd)"
ONLINE_RL_AGENT_CORE_ROOT="$(cd "${ONLINE_RL_SCRIPT_DIR}/../.." && pwd)"
ONLINE_RL_WORKSPACE_ROOT="$(cd "${ONLINE_RL_AGENT_CORE_ROOT}/.." && pwd)"

: "${ONLINE_RL_CONDA_ENV:=openjiuwen-rl}"
: "${ONLINE_RL_CONDA_SH:=/data1/lll/miniconda3/etc/profile.d/conda.sh}"
: "${CONDA_ENV:=${ONLINE_RL_CONDA_ENV}}"
: "${CONDA_SH:=${ONLINE_RL_CONDA_SH}}"
: "${USE_CONDA:=1}"

online_rl_use_conda() {
  case "${USE_CONDA:-1}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

online_rl_detect_system_python() {
  if command -v python3 >/dev/null 2>&1; then
    local candidate
    candidate="$(command -v python3)"
    case "${candidate}" in
      *conda*|*miniconda*|*mamba*) ;;
      *) printf '%s\n' "${candidate}"; return ;;
    esac
  fi
  if command -v python >/dev/null 2>&1; then
    local candidate
    candidate="$(command -v python)"
    case "${candidate}" in
      *conda*|*miniconda*|*mamba*) ;;
      *) printf '%s\n' "${candidate}"; return ;;
    esac
  fi
  if [[ -x /usr/bin/python3 ]]; then
    printf '%s\n' /usr/bin/python3
    return
  fi
  if [[ -x /usr/local/bin/python3 ]]; then
    printf '%s\n' /usr/local/bin/python3
    return
  fi
  printf '%s\n' python3
}

: "${MODEL_PATH:=/data1/lll/models/Qwen3-4B-Thinking-2507}"
: "${MODEL_NAME:=Qwen3-4B-Thinking-2507}"

: "${ONLINE_RL_DEVICE_BACKEND:=cuda}"
case "${ONLINE_RL_DEVICE_BACKEND}" in
  ascend|npu)
    : "${ONLINE_RL_VISIBLE_DEVICES_ENV:=ASCEND_RT_VISIBLE_DEVICES}"
    ;;
  cuda|gpu)
    : "${ONLINE_RL_VISIBLE_DEVICES_ENV:=CUDA_VISIBLE_DEVICES}"
    ;;
  *)
    : "${ONLINE_RL_VISIBLE_DEVICES_ENV:=CUDA_VISIBLE_DEVICES}"
    ;;
esac
: "${PPO_CONFIG_PATH:=}"

: "${REDIS_CONTAINER_NAME:=pinchbench-redis}"
: "${REDIS_PORT:=6379}"

: "${WEB_USER_ID:=local-web-user}"
: "${JIUWENSWARM_LIGHT_PROFILE:=1}"
: "${USE_CONTEXT_COMPRESSION_RAIL:=1}"
: "${JIUWENSWARM_CONTEXT_WINDOW_TOKENS:=32768}"
: "${JIUWENSWARM_CONTEXT_COMPRESSION_TRIGGER_TOKENS:=28672}"
: "${JIUWENSWARM_CONTEXT_COMPRESSION_TARGET_TOKENS:=24576}"
: "${JIUWENSWARM_CONTEXT_COMPRESSION_KEEP_RECENT_MESSAGES:=6}"
: "${JIUWENSWARM_CONTEXT_COMPRESSION_CALL_MAX_TOKENS:=28672}"
: "${JIUWENSWARM_CONTEXT_COMPRESSION_FIRST_PASS_TARGET_TOKENS:=1800}"
: "${JIUWENSWARM_CONTEXT_COMPRESSION_SECOND_PASS_TARGET_TOKENS:=1200}"
: "${JIUWENSWARM_CONTEXT_COMPRESSION_THIRD_PASS_TARGET_TOKENS:=800}"

: "${VLLM_GPU:=0,1}"
: "${VLLM_TP:=2}"
: "${VLLM_HOST:=127.0.0.1}"
: "${VLLM_PORT:=18002}"
: "${VLLM_EXTRA_ARGS:=--max-model-len 32768 --gpu-memory-utilization 0.85 --max-num-seqs 4 --enforce-eager}"

: "${GATEWAY_HOST:=127.0.0.1}"
: "${GATEWAY_PORT:=18080}"

: "${TRAIN_GPU:=4,5,6,7}"
: "${TRAIN_THRESHOLD:=4}"
: "${ONLINE_RL_ENABLE_JUDGE:=1}"
: "${ONLINE_RL_DRAIN_PENDING_ON_TRAIN:=0}"
: "${ONLINE_RL_MAX_SAMPLES_PER_RUN:=0}"
: "${ONLINE_RL_PPO_SAMPLES_PER_STEP:=${TRAIN_THRESHOLD}}"
: "${ONLINE_RL_ALLOW_PARTIAL_LAST_STEP:=0}"
: "${TRAIN_BACKEND:=PPO}"
: "${SFT_SCENARIO:=multi_turn_supervisor}"
: "${SFT_ROLLOUTER:=multi_turn_supervisor}"
: "${SUPERVISOR_URL:=http://${VLLM_HOST}:${VLLM_PORT}}"
: "${SUPERVISOR_TOKEN:=EMPTY}"
: "${SUPERVISOR_MODEL:=${MODEL_NAME}}"
: "${TARGET_MODEL_ID:=${MODEL_NAME}}"
: "${SFT_DRY_RUN:=0}"
: "${SFT_TASK_MAX_ITERATIONS:=}"
# Empty means SFTTrainingExecutor uses its native LLaMA-Factory adapter. Set a
# command explicitly only when a legacy/custom trainer needs to override it.
: "${SFT_TRAINER_COMMAND:=}"
: "${RL_ONLINE_CAPTURE_MODE:=ppo_turn}"
: "${RL_ONLINE_SESSION_DONE_ON_INVOKE_END:=1}"
: "${TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K:=0}"
: "${ENABLE_SANDBOX_PLUGINS:=0}"
: "${ONLINE_RL_ROLLOUTER:=}"
: "${ONLINE_RL_EVALER:=}"
: "${SCAN_INTERVAL:=10}"
: "${TRAJECTORY_BATCH_SIZE:=1}"
: "${TRAJECTORY_MODE:=feedback_level}"

: "${AGENT_SERVER_HOST:=127.0.0.1}"
: "${AGENT_SERVER_PORT:=18092}"
: "${WEB_HOST:=127.0.0.1}"
: "${WEB_PORT:=19000}"
: "${CLAW_GATEWAY_PORT:=19001}"
: "${FRONTEND_HOST:=127.0.0.1}"
: "${FRONTEND_PORT:=5173}"

: "${LOG_DIR:=${ONLINE_RL_SCRIPT_DIR}/logs}"
: "${PID_DIR:=${LOG_DIR}/pids}"
: "${RECORD_DIR:=${ONLINE_RL_SCRIPT_DIR}/records}"
: "${LORA_REPO:=${ONLINE_RL_SCRIPT_DIR}/lora_repo}"
: "${LORA_DEFAULT_POLICY:=latest_by_user}"
: "${JIUWEN_DATA_DIR:=${ONLINE_RL_SCRIPT_DIR}/.jiuwenswarm-online}"
: "${SFT_OPTIMIZE_EXTENSION_DIR:=${ONLINE_RL_SCRIPT_DIR}/jiuwenswarm_extensions}"

online_rl_detect_jiuwenclaw_root() {
  if [[ -n "${ONLINE_RL_JIUWENCLAW_ROOT:-}" ]]; then
    printf '%s\n' "${ONLINE_RL_JIUWENCLAW_ROOT}"
    return
  fi

  local workspace_parent=""
  workspace_parent="$(cd "${ONLINE_RL_WORKSPACE_ROOT}/.." && pwd)"
  if [[ -d "${workspace_parent}/refactor/jiuwenclaw" ]]; then
    printf '%s\n' "${workspace_parent}/refactor/jiuwenclaw"
    return
  fi
  if [[ -d "${ONLINE_RL_WORKSPACE_ROOT}/jiuwenclaw" ]]; then
    printf '%s\n' "${ONLINE_RL_WORKSPACE_ROOT}/jiuwenclaw"
    return
  fi
  if [[ -d "${workspace_parent}/jiuwenclaw" ]]; then
    printf '%s\n' "${workspace_parent}/jiuwenclaw"
    return
  fi
  printf '%s\n' "${ONLINE_RL_WORKSPACE_ROOT}/jiuwenclaw"
}

online_rl_tcp_open() {
  local host="$1"
  local port="$2"
  if command -v timeout >/dev/null 2>&1; then
    timeout 1 bash -c "</dev/tcp/${host}/${port}" >/dev/null 2>&1
  else
    bash -c "</dev/tcp/${host}/${port}" >/dev/null 2>&1
  fi
}

online_rl_detect_redis_url() {
  if [[ -n "${REDIS_URL:-}" ]]; then
    printf '%s\n' "${REDIS_URL}"
    return
  fi

  if online_rl_tcp_open "127.0.0.1" "${REDIS_PORT}"; then
    printf 'redis://127.0.0.1:%s/0\n' "${REDIS_PORT}"
    return
  fi

  local redis_ip=""
  if command -v docker >/dev/null 2>&1; then
    redis_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${REDIS_CONTAINER_NAME}" 2>/dev/null || true)"
  fi

  if [[ -n "${redis_ip}" ]]; then
    printf 'redis://%s:%s/0\n' "${redis_ip}" "${REDIS_PORT}"
  else
    printf 'redis://127.0.0.1:%s/0\n' "${REDIS_PORT}"
  fi
}

online_rl_detect_container_reachable_host() {
  if [[ -n "${ONLINE_RL_CONTAINER_HOST:-}" ]]; then
    printf '%s\n' "${ONLINE_RL_CONTAINER_HOST}"
    return
  fi

  local host_ip=""
  if command -v hostname >/dev/null 2>&1; then
    host_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  if [[ -n "${host_ip}" ]]; then
    printf '%s\n' "${host_ip}"
  else
    printf '127.0.0.1\n'
  fi
}

REDIS_URL="$(online_rl_detect_redis_url)"
VLLM_URL="http://${VLLM_HOST}:${VLLM_PORT}"
GATEWAY_URL="${GATEWAY_URL:-http://${GATEWAY_HOST}:${GATEWAY_PORT}}"
: "${TRAJECTORY_GATEWAY_URL:=${GATEWAY_URL}}"
ONLINE_RL_CONTAINER_HOST="$(online_rl_detect_container_reachable_host)"
: "${ONLINE_RL_JIUWENCLAW_ROOT:=$(online_rl_detect_jiuwenclaw_root)}"
: "${PYTHONPATH_VALUE:=${ONLINE_RL_AGENT_CORE_ROOT}:${ONLINE_RL_JIUWENCLAW_ROOT}}"
: "${SFT_DOCKER_GATEWAY_URL:=http://${ONLINE_RL_CONTAINER_HOST}:${GATEWAY_PORT}}"
: "${RL_GATEWAY_URL:=${SFT_DOCKER_GATEWAY_URL}}"
FRONTEND_URL="${FRONTEND_URL:-http://${FRONTEND_HOST}:${FRONTEND_PORT}}"
ONLINE_RL_WS_URL="${ONLINE_RL_WS_URL:-ws://${WEB_HOST}:${WEB_PORT}/ws}"
ONLINE_RL_CWD="${ONLINE_RL_CWD:-${ONLINE_RL_WORKSPACE_ROOT}}"

if [[ -z "${ONLINE_RL_PYTHON:-}" ]]; then
  if online_rl_use_conda; then
    ONLINE_RL_PYTHON="python"
  else
    ONLINE_RL_PYTHON="$(online_rl_detect_system_python)"
  fi
fi

online_rl_activate_python_env() {
  if online_rl_use_conda; then
    if [[ ! -f "${CONDA_SH}" ]]; then
      echo "conda init script not found: ${CONDA_SH}; set USE_CONDA=0 to use system python" >&2
      return 1
    fi
    # shellcheck disable=SC1090
    source "${CONDA_SH}"
    conda activate "${CONDA_ENV}"
  fi
  export PYTHONPATH="${PYTHONPATH_VALUE}"
  export PYTHONUNBUFFERED=1
  export ONLINE_RL_PYTHON
}

online_rl_print_python_env_prelude() {
  if online_rl_use_conda; then
    printf 'source %q\n' "${CONDA_SH}"
    printf 'conda activate %q\n' "${CONDA_ENV}"
  fi
  printf 'export PYTHONPATH=%q\n' "${PYTHONPATH_VALUE}"
  printf '%s\n' 'export PYTHONUNBUFFERED=1'
  printf 'export ONLINE_RL_PYTHON=%q\n' "${ONLINE_RL_PYTHON}"
}

export ONLINE_RL_DEPLOY_SCRIPT_DIR ONLINE_RL_SCRIPT_DIR ONLINE_RL_AGENT_CORE_ROOT ONLINE_RL_WORKSPACE_ROOT
export ONLINE_RL_CONDA_ENV ONLINE_RL_CONDA_SH CONDA_ENV CONDA_SH USE_CONDA ONLINE_RL_PYTHON
export MODEL_PATH MODEL_NAME
export ONLINE_RL_DEVICE_BACKEND ONLINE_RL_VISIBLE_DEVICES_ENV PPO_CONFIG_PATH
export REDIS_CONTAINER_NAME REDIS_PORT REDIS_URL
export WEB_USER_ID JIUWENSWARM_LIGHT_PROFILE
export USE_CONTEXT_COMPRESSION_RAIL JIUWENSWARM_CONTEXT_WINDOW_TOKENS
export JIUWENSWARM_CONTEXT_COMPRESSION_TRIGGER_TOKENS
export JIUWENSWARM_CONTEXT_COMPRESSION_TARGET_TOKENS
export JIUWENSWARM_CONTEXT_COMPRESSION_KEEP_RECENT_MESSAGES
export JIUWENSWARM_CONTEXT_COMPRESSION_CALL_MAX_TOKENS
export JIUWENSWARM_CONTEXT_COMPRESSION_FIRST_PASS_TARGET_TOKENS
export JIUWENSWARM_CONTEXT_COMPRESSION_SECOND_PASS_TARGET_TOKENS
export JIUWENSWARM_CONTEXT_COMPRESSION_THIRD_PASS_TARGET_TOKENS
export VLLM_GPU VLLM_TP VLLM_HOST VLLM_PORT VLLM_URL VLLM_EXTRA_ARGS
export GATEWAY_HOST GATEWAY_PORT GATEWAY_URL ONLINE_RL_CONTAINER_HOST
export SFT_DOCKER_GATEWAY_URL RL_GATEWAY_URL
export TRAJECTORY_GATEWAY_URL
export TRAIN_GPU TRAIN_THRESHOLD ONLINE_RL_ENABLE_JUDGE ONLINE_RL_DRAIN_PENDING_ON_TRAIN
export ONLINE_RL_MAX_SAMPLES_PER_RUN ONLINE_RL_PPO_SAMPLES_PER_STEP
export ENABLE_SANDBOX_PLUGINS ONLINE_RL_ROLLOUTER ONLINE_RL_EVALER
export TRAIN_BACKEND SFT_SCENARIO SFT_ROLLOUTER SUPERVISOR_URL SUPERVISOR_TOKEN
export SUPERVISOR_MODEL TARGET_MODEL_ID SFT_DRY_RUN SFT_TRAINER_COMMAND
export SFT_TASK_MAX_ITERATIONS
export RL_ONLINE_CAPTURE_MODE RL_ONLINE_SESSION_DONE_ON_INVOKE_END TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K
export LORA_DEFAULT_POLICY
export ONLINE_RL_ALLOW_PARTIAL_LAST_STEP
export SCAN_INTERVAL TRAJECTORY_BATCH_SIZE TRAJECTORY_MODE
export AGENT_SERVER_HOST AGENT_SERVER_PORT WEB_HOST WEB_PORT CLAW_GATEWAY_PORT
export FRONTEND_HOST FRONTEND_PORT FRONTEND_URL ONLINE_RL_WS_URL ONLINE_RL_CWD
export ONLINE_RL_JIUWENCLAW_ROOT
export LOG_DIR PID_DIR RECORD_DIR LORA_REPO JIUWEN_DATA_DIR PYTHONPATH_VALUE
export SFT_OPTIMIZE_EXTENSION_DIR
