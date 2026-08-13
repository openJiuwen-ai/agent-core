#!/usr/bin/env bash
set -euo pipefail

# Backend-only launcher for Jiuwen online RL.
# Run this script from the current container/machine. It starts vLLM, Gateway,
# scheduler, JiuwenSwarm app, and the static web frontend. Set USE_CONDA=0 to
# skip conda activation and use the system python environment.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
AGENT_CORE_ROOT="$(cd "${JIUWENRL_ROOT}/../.." && pwd)"
WORKSPACE_ROOT="$(cd "${AGENT_CORE_ROOT}/.." && pwd)"
JIUWENSWARM_PATCH_PATH="${SCRIPT_DIR}/sitecustomize_online_rl"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/online_rl_local_env.sh"

mkdir -p "${LOG_DIR}" "${PID_DIR}" "${RECORD_DIR}" "${LORA_REPO}" "${JIUWEN_DATA_DIR}"

usage() {
  cat <<EOF
Usage: $(basename "$0") {start|stop|restart|status|logs [name]}

Defaults:
  use conda:   ${USE_CONDA}  env=${CONDA_ENV}
  model:       ${MODEL_PATH}
  redis:       ${REDIS_URL}
  gateway:     ${GATEWAY_URL}
  vLLM:        ${VLLM_URL}  gpu=${VLLM_GPU} tp=${VLLM_TP}
  train gpu:   ${TRAIN_GPU}
  jiuwenswarm: agentserver=${AGENT_SERVER_PORT} web-ws=${WEB_PORT} frontend=${FRONTEND_PORT}

Override with env vars, e.g.:
  TRAIN_THRESHOLD=2 VLLM_EXTRA_ARGS="--max-model-len 32768 --max-num-seqs 4 --enforce-eager" $0 start
EOF
}

run_detached() {
  local name="$1"
  local body="$2"
  local log="${LOG_DIR}/${name}.log"
  local pid="${PID_DIR}/${name}.pid"
  local run_file="${PID_DIR}/run_${name}.sh"
  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' 'set -euo pipefail'
    printf 'cd %q\n' "${JIUWENRL_ROOT}"
    printf 'export USE_CONDA=%q\n' "${USE_CONDA}"
    online_rl_print_python_env_prelude
    printf '%s\n' "${body}"
  } > "${run_file}"
  chmod +x "${run_file}"
  setsid nohup bash "${run_file}" >> "${log}" 2>&1 &
  echo "$!" > "${pid}"
  echo "started ${name}, pid=$!, log=${log}"
}

pid_running() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] || return 1
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1
}

stop_jiuwenswarm_children() {
  pkill -f '[j]iuwenswarm.server.app_agentserver' || true
  pkill -f '[j]iuwenswarm.gateway.app_gateway' || true
}

stop_web_children() {
  pkill -f '[j]iuwenswarm.channels.web.app_web' || true
}

configure_jiuwenswarm_env() {
  local env_file="${JIUWEN_DATA_DIR}/config/.env"
  local tmp_file="${env_file}.tmp.$$"
  mkdir -p "$(dirname "${env_file}")"
  if [[ -f "${env_file}" ]]; then
    awk '
      /^# online-rl backend begin$/ { skip=1; next }
      /^# online-rl backend end$/ { skip=0; next }
      !skip { print }
    ' "${env_file}" > "${tmp_file}"
  else
    : > "${tmp_file}"
  fi
  cat >> "${tmp_file}" <<EOF
# online-rl backend begin
API_BASE="${VLLM_URL}/v1"
API_KEY="EMPTY"
MODEL_PROVIDER="OpenAI"
MODEL_NAME="${MODEL_NAME}"
EMBED_API_BASE="${VLLM_URL}/v1"
EMBED_API_KEY="EMPTY"
EMBED_MODEL="${MODEL_NAME}"
WEB_USER_ID="${WEB_USER_ID}"
RL_ONLINE_TENANT_ID="${WEB_USER_ID}"
CUSTOM_HEADERS='{"x-user-id":"${WEB_USER_ID}"}'
USE_RL_ONLINE_RAIL="1"
TRAIN_BACKEND="${TRAIN_BACKEND}"
RL_ONLINE_CAPTURE_MODE="${RL_ONLINE_CAPTURE_MODE}"
SFT_SCENARIO="${SFT_SCENARIO}"
RL_ONLINE_SESSION_DONE_ON_INVOKE_END="${RL_ONLINE_SESSION_DONE_ON_INVOKE_END}"
TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K="${TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K}"
USE_CONTEXT_COMPRESSION_RAIL="${USE_CONTEXT_COMPRESSION_RAIL}"
JIUWENSWARM_CONTEXT_WINDOW_TOKENS="${JIUWENSWARM_CONTEXT_WINDOW_TOKENS}"
JIUWENSWARM_CONTEXT_COMPRESSION_TRIGGER_TOKENS="${JIUWENSWARM_CONTEXT_COMPRESSION_TRIGGER_TOKENS}"
JIUWENSWARM_CONTEXT_COMPRESSION_TARGET_TOKENS="${JIUWENSWARM_CONTEXT_COMPRESSION_TARGET_TOKENS}"
JIUWENSWARM_CONTEXT_COMPRESSION_KEEP_RECENT_MESSAGES="${JIUWENSWARM_CONTEXT_COMPRESSION_KEEP_RECENT_MESSAGES}"
JIUWENSWARM_CONTEXT_COMPRESSION_CALL_MAX_TOKENS="${JIUWENSWARM_CONTEXT_COMPRESSION_CALL_MAX_TOKENS}"
JIUWENSWARM_CONTEXT_COMPRESSION_FIRST_PASS_TARGET_TOKENS="${JIUWENSWARM_CONTEXT_COMPRESSION_FIRST_PASS_TARGET_TOKENS}"
JIUWENSWARM_CONTEXT_COMPRESSION_SECOND_PASS_TARGET_TOKENS="${JIUWENSWARM_CONTEXT_COMPRESSION_SECOND_PASS_TARGET_TOKENS}"
JIUWENSWARM_CONTEXT_COMPRESSION_THIRD_PASS_TARGET_TOKENS="${JIUWENSWARM_CONTEXT_COMPRESSION_THIRD_PASS_TARGET_TOKENS}"
LORA_REPO_ROOT="${LORA_REPO}"
LORA_DEFAULT_POLICY="${LORA_DEFAULT_POLICY}"
ENABLE_TRAJECTORY_COLLECTION="false"
TRAJECTORY_GATEWAY_URL="${GATEWAY_URL}"
RL_GATEWAY_URL="${RL_GATEWAY_URL}"
SFT_DOCKER_GATEWAY_URL="${SFT_DOCKER_GATEWAY_URL}"
TRAJECTORY_TOKENIZER_PATH="${MODEL_PATH}"
TRAJECTORY_BATCH_SIZE="${TRAJECTORY_BATCH_SIZE:-4}"
TRAJECTORY_MODE="${TRAJECTORY_MODE:-feedback_level}"
MEMORY_ENGINE="none"
JIUWENSWARM_LIGHT_PROFILE="${JIUWENSWARM_LIGHT_PROFILE}"
JIUWENSWARM_ENABLE_FILESYSTEM_RAIL="false"
AGENT_SERVER_HOST="${AGENT_SERVER_HOST}"
AGENT_SERVER_PORT="${AGENT_SERVER_PORT}"
WEB_HOST="${WEB_HOST}"
WEB_PORT="${WEB_PORT}"
FRONTEND_HOST="${FRONTEND_HOST}"
FRONTEND_PORT="${FRONTEND_PORT}"
# online-rl backend end
EOF
  mv "${tmp_file}" "${env_file}"
}

configure_jiuwenswarm_extensions() {
  local config_file="${JIUWEN_DATA_DIR}/config/config.yaml"
  [[ -d "${SFT_OPTIMIZE_EXTENSION_DIR}" ]] || return 0
  mkdir -p "$(dirname "${config_file}")"
  AGENT_CORE_SFT_EXTENSION_DIR="${SFT_OPTIMIZE_EXTENSION_DIR}" \
  CONFIG_FILE="${config_file}" \
  "${ONLINE_RL_PYTHON}" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

import yaml


config_file = Path(os.environ["CONFIG_FILE"])
extension_dir = os.environ["AGENT_CORE_SFT_EXTENSION_DIR"]
config = {}
if config_file.exists():
    with config_file.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
extensions = config.setdefault("extensions", {})
current = str(extensions.get("extension_dirs") or "").strip()
parts = [item.strip() for item in current.split(";") if item.strip()]
if extension_dir not in parts:
    parts.append(extension_dir)
extensions["extension_dirs"] = ";".join(parts)
with config_file.open("w", encoding="utf-8") as fh:
    yaml.safe_dump(config, fh, allow_unicode=True, sort_keys=False)
PY
}

install_rl_online_rail_extension() {
  "${ONLINE_RL_PYTHON}" "${SCRIPT_DIR}/install_rl_online_rail_extension.py" \
    --agent-workspace "${JIUWEN_DATA_DIR}/agent/workspace" \
    --force
}

install_context_compression_rail_extension() {
  case "${USE_CONTEXT_COMPRESSION_RAIL:-0}" in
    1|true|TRUE|yes|YES|on|ON) ;;
    *) return 0 ;;
  esac
  "${ONLINE_RL_PYTHON}" "${SCRIPT_DIR}/install_context_compression_rail_extension.py" \
    --agent-workspace "${JIUWEN_DATA_DIR}/agent/workspace" \
    --force
}

wait_health() {
  local name="$1"
  local url="$2"
  local timeout="${3:-300}"
  echo "waiting for ${name}: ${url}"
  for _ in $(seq 1 "${timeout}"); do
    curl -sf "${url}" >/dev/null && return 0
    sleep 1
  done
  echo "${name} did not become healthy; tailing log:" >&2
  tail -80 "${LOG_DIR}/${name}.log" >&2 || true
  exit 1
}

start_vllm() {
  if curl -sf "${VLLM_URL}/health" >/dev/null 2>&1; then
    echo "vllm already healthy at ${VLLM_URL}"
    return
  fi
  run_detached "vllm" "
export ${ONLINE_RL_VISIBLE_DEVICES_ENV}='${VLLM_GPU}'
export VLLM_ALLOW_RUNTIME_LORA_UPDATING=1
exec '${ONLINE_RL_PYTHON}' '${SCRIPT_DIR}/vllm_patched_launcher.py' \
  --model '${MODEL_PATH}' \
  --served-model-name '${MODEL_NAME}' \
  --host '${VLLM_HOST}' \
  --port '${VLLM_PORT}' \
  --tensor-parallel-size '${VLLM_TP}' \
  --enable-lora \
  --max-loras 4 \
  --max-lora-rank 32 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  ${VLLM_EXTRA_ARGS}
"
  wait_health "vllm" "${VLLM_URL}/health" 900
}

start_gateway() {
  if curl -sf "${GATEWAY_URL}/health" >/dev/null 2>&1; then
    echo "gateway already healthy at ${GATEWAY_URL}"
    return
  fi
  local judge_url="${VLLM_URL}"
  local judge_model="${MODEL_NAME}"
  case "${ONLINE_RL_ENABLE_JUDGE:-1}" in
    0|false|FALSE|no|NO|off|OFF)
      judge_url=""
      judge_model=""
      ;;
  esac
  run_detached "gateway" "
export LLM_URL='${VLLM_URL}'
export JUDGE_URL='${judge_url}'
export JUDGE_MODEL='${judge_model}'
export MODEL_ID='${MODEL_NAME}'
export MODEL_PATH='${MODEL_PATH}'
export GATEWAY_HOST='${GATEWAY_HOST}'
export GATEWAY_PORT='${GATEWAY_PORT}'
export RECORD_DIR='${RECORD_DIR}'
export REDIS_URL='${REDIS_URL}'
export LORA_REPO_ROOT='${LORA_REPO}'
export LORA_DEFAULT_POLICY='${LORA_DEFAULT_POLICY:-latest_by_user}'
export TRAIN_BACKEND='${TRAIN_BACKEND}'
export SUPERVISOR_URL='${SUPERVISOR_URL}'
export SUPERVISOR_TOKEN='${SUPERVISOR_TOKEN}'
export RL_ONLINE_CAPTURE_MODE='${RL_ONLINE_CAPTURE_MODE}'
export SFT_SCENARIO='${SFT_SCENARIO}'
export RL_ONLINE_SESSION_DONE_ON_INVOKE_END='${RL_ONLINE_SESSION_DONE_ON_INVOKE_END}'
export TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K='${TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K}'
export DISABLE_GATEWAY_TRAJECTORY_COLLECTION=true
export LOG_LEVEL=INFO
exec '${ONLINE_RL_PYTHON}' -m uvicorn openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app \
  --factory --host '${GATEWAY_HOST}' --port '${GATEWAY_PORT}' --log-level info
"
  wait_health "gateway" "${GATEWAY_URL}/health" 120
}

start_scheduler() {
  if pid_running "${PID_DIR}/scheduler.pid"; then
    echo "scheduler already running"
    return
  fi
  run_detached "scheduler" "
export ${ONLINE_RL_VISIBLE_DEVICES_ENV}='${TRAIN_GPU}'
export ONLINE_RL_VISIBLE_DEVICES_ENV='${ONLINE_RL_VISIBLE_DEVICES_ENV}'
export TRAJECTORY_GATEWAY_URL='${TRAJECTORY_GATEWAY_URL}'
export SUPERVISOR_URL='${SUPERVISOR_URL}'
export SUPERVISOR_TOKEN='${SUPERVISOR_TOKEN}'
export SUPERVISOR_MODEL='${SUPERVISOR_MODEL}'
export SFT_DOCKER_USE_HOST_CONDA='${SFT_DOCKER_USE_HOST_CONDA:-${USE_CONDA}}'
export SFT_DOCKER_CONDA_ROOT='${SFT_DOCKER_CONDA_ROOT:-/data1/lll/miniconda3}'
export SFT_DOCKER_CONDA_ENV='${SFT_DOCKER_CONDA_ENV:-openjiuwen-rl}'
export SFT_DOCKER_AGENT_CORE_HOST_PATH='${SFT_DOCKER_AGENT_CORE_HOST_PATH:-${AGENT_CORE_ROOT}}'
export SFT_DOCKER_JIUWENCLAW_HOST_PATH='${SFT_DOCKER_JIUWENCLAW_HOST_PATH:-}'
export SFT_DOCKER_ROLLOUT_FETCH_TIMEOUT='${SFT_DOCKER_ROLLOUT_FETCH_TIMEOUT:-30}'
export SFT_DOCKER_ROLLOUT_TIMEOUT='${SFT_DOCKER_ROLLOUT_TIMEOUT:-600}'
export SFT_TASK_UPLOAD_SETTLE_SECONDS='${SFT_TASK_UPLOAD_SETTLE_SECONDS:-8}'
export SFT_TASK_PRINT_APP_LOG='${SFT_TASK_PRINT_APP_LOG:-0}'
export SFT_TASK_APP_LOG_TAIL='${SFT_TASK_APP_LOG_TAIL:-240}'
export SFT_TASK_MAX_ITERATIONS='${SFT_TASK_MAX_ITERATIONS:-}'
export SFT_DOCKER_ROLLOUT_DEBUG_LOG='${SFT_DOCKER_ROLLOUT_DEBUG_LOG:-0}'
export SFT_ROLLOUT_CONCURRENCY='${SFT_ROLLOUT_CONCURRENCY:-1}'
if [[ '${USE_CONDA}' =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
export LLAMAFACTORY_CLI='${LLAMAFACTORY_CLI:-/data1/lll/miniconda3/envs/openjiuwen-sft/bin/llamafactory-cli}'
else
export LLAMAFACTORY_CLI='${LLAMAFACTORY_CLI:-llamafactory-cli}'
fi
exec '${ONLINE_RL_PYTHON}' - <<'PY'
import logging
import signal
import time

from openjiuwen.agent_evolving.agent_rl.online.inference.notifier import InferenceNotifier
from openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler import OnlineTrainingScheduler
from openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins import load_plugin
from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRARepository

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
stop = False

def handle_stop(signum, frame):
    global stop
    stop = True

signal.signal(signal.SIGTERM, handle_stop)
signal.signal(signal.SIGINT, handle_stop)

train_gpu = '${TRAIN_GPU}'
num_gpus = len([x for x in train_gpu.split(',') if x.strip()]) or 1
ppo_config_path = '${PPO_CONFIG_PATH}'
scheduler = OnlineTrainingScheduler(
    redis_url='${REDIS_URL}',
    poll_interval=float('${SCAN_INTERVAL}'),
    min_samples_for_training=int('${TRAIN_THRESHOLD}'),
    base_model_path='${MODEL_PATH}',
    lora_repo=LoRARepository('${LORA_REPO}'),
    notifier=InferenceNotifier('${VLLM_URL}'),
    nproc_per_node=num_gpus,
    training_gpu_ids=train_gpu,
    ppo_config_path=ppo_config_path or None,
    drain_pending_on_train='${ONLINE_RL_DRAIN_PENDING_ON_TRAIN}'.lower() in {'1', 'true', 'yes', 'on'},
    max_samples_per_run=int('${ONLINE_RL_MAX_SAMPLES_PER_RUN}'),
    ppo_samples_per_step=int('${ONLINE_RL_PPO_SAMPLES_PER_STEP}'),
    allow_partial_last_step='${ONLINE_RL_ALLOW_PARTIAL_LAST_STEP}'.lower() in {'1', 'true', 'yes', 'on'},
    rollouter=load_plugin('${ONLINE_RL_ROLLOUTER}' or None),
    evaler=load_plugin('${ONLINE_RL_EVALER}' or None),
    train_backend='${TRAIN_BACKEND}',
    sft_rollouter='${SFT_ROLLOUTER}',
    supervisor_url='${SUPERVISOR_URL}',
    supervisor_token='${SUPERVISOR_TOKEN}',
    supervisor_model='${SUPERVISOR_MODEL}',
    target_model_id='${TARGET_MODEL_ID}',
    sft_trainer_command='${SFT_TRAINER_COMMAND}',
    sft_dry_run='${SFT_DRY_RUN}'.lower() in {'1', 'true', 'yes', 'on'},
)
scheduler.start()
try:
    while not stop:
        time.sleep(1)
finally:
    scheduler.stop()
PY
"
}

start_jiuwenswarm() {
  configure_jiuwenswarm_env
  configure_jiuwenswarm_extensions
  install_rl_online_rail_extension
  install_context_compression_rail_extension
  if pid_running "${PID_DIR}/jiuwenswarm.pid"; then
    echo "jiuwenswarm app already running"
  else
    stop_jiuwenswarm_children
    run_detached "jiuwenswarm" "
export JIUWENSWARM_DATA_DIR='${JIUWEN_DATA_DIR}'
export PYTHONPATH='${JIUWENSWARM_PATCH_PATH}:${PYTHONPATH_VALUE}'
export API_BASE='${VLLM_URL}/v1'
export API_KEY='EMPTY'
export MODEL_PROVIDER='OpenAI'
export MODEL_NAME='${MODEL_NAME}'
export EMBED_API_BASE='${VLLM_URL}/v1'
export EMBED_API_KEY='EMPTY'
export EMBED_MODEL='${MODEL_NAME}'
export WEB_USER_ID='${WEB_USER_ID}'
export RL_ONLINE_TENANT_ID='${WEB_USER_ID}'
export CUSTOM_HEADERS='{\"x-user-id\":\"${WEB_USER_ID}\"}'
export USE_RL_ONLINE_RAIL=1
export TRAIN_BACKEND='${TRAIN_BACKEND}'
export RL_ONLINE_CAPTURE_MODE='${RL_ONLINE_CAPTURE_MODE}'
export SFT_SCENARIO='${SFT_SCENARIO}'
export RL_ONLINE_SESSION_DONE_ON_INVOKE_END='${RL_ONLINE_SESSION_DONE_ON_INVOKE_END}'
export TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K='${TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K}'
export USE_CONTEXT_COMPRESSION_RAIL='${USE_CONTEXT_COMPRESSION_RAIL}'
export JIUWENSWARM_CONTEXT_WINDOW_TOKENS='${JIUWENSWARM_CONTEXT_WINDOW_TOKENS}'
export JIUWENSWARM_CONTEXT_COMPRESSION_TRIGGER_TOKENS='${JIUWENSWARM_CONTEXT_COMPRESSION_TRIGGER_TOKENS}'
export JIUWENSWARM_CONTEXT_COMPRESSION_TARGET_TOKENS='${JIUWENSWARM_CONTEXT_COMPRESSION_TARGET_TOKENS}'
export JIUWENSWARM_CONTEXT_COMPRESSION_KEEP_RECENT_MESSAGES='${JIUWENSWARM_CONTEXT_COMPRESSION_KEEP_RECENT_MESSAGES}'
export JIUWENSWARM_CONTEXT_COMPRESSION_CALL_MAX_TOKENS='${JIUWENSWARM_CONTEXT_COMPRESSION_CALL_MAX_TOKENS}'
export JIUWENSWARM_CONTEXT_COMPRESSION_FIRST_PASS_TARGET_TOKENS='${JIUWENSWARM_CONTEXT_COMPRESSION_FIRST_PASS_TARGET_TOKENS}'
export JIUWENSWARM_CONTEXT_COMPRESSION_SECOND_PASS_TARGET_TOKENS='${JIUWENSWARM_CONTEXT_COMPRESSION_SECOND_PASS_TARGET_TOKENS}'
export JIUWENSWARM_CONTEXT_COMPRESSION_THIRD_PASS_TARGET_TOKENS='${JIUWENSWARM_CONTEXT_COMPRESSION_THIRD_PASS_TARGET_TOKENS}'
export LORA_REPO_ROOT='${LORA_REPO}'
export LORA_DEFAULT_POLICY='${LORA_DEFAULT_POLICY}'
export ENABLE_TRAJECTORY_COLLECTION=false
export TRAJECTORY_GATEWAY_URL='${GATEWAY_URL}'
export RL_GATEWAY_URL='${RL_GATEWAY_URL}'
export SFT_DOCKER_GATEWAY_URL='${SFT_DOCKER_GATEWAY_URL}'
export TRAJECTORY_TOKENIZER_PATH='${MODEL_PATH}'
export TRAJECTORY_BATCH_SIZE='${TRAJECTORY_BATCH_SIZE:-4}'
export TRAJECTORY_MODE='${TRAJECTORY_MODE:-feedback_level}'
export MEMORY_ENGINE='none'
export JIUWENSWARM_LIGHT_PROFILE='${JIUWENSWARM_LIGHT_PROFILE}'
export JIUWENSWARM_ENABLE_FILESYSTEM_RAIL=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export JIUWENSWARM_ENABLE_TASK_PLANNING_RAIL=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export JIUWENSWARM_ENABLE_SUBAGENT_RAIL=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export JIUWENSWARM_ENABLE_CONTEXT_ASSEMBLE_RAIL=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export JIUWENSWARM_ENABLE_CONTEXT_PROCESSOR_RAIL=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export JIUWENSWARM_ENABLE_RUNTIME_SESSION_TOOLS=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export JIUWENSWARM_ENABLE_CRON_RUNTIME_TOOLS=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export JIUWENSWARM_ENABLE_USER_TODOS_TOOL=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export JIUWENSWARM_ENABLE_WEB_TOOLS=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export JIUWENSWARM_ENABLE_WIKI_TOOLS=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export JIUWENSWARM_ENABLE_SKILL_TOOLKIT=\$([[ '${JIUWENSWARM_LIGHT_PROFILE}' == '1' ]] && echo false || echo true)
export AGENT_SERVER_HOST='${AGENT_SERVER_HOST}'
export AGENT_SERVER_PORT='${AGENT_SERVER_PORT}'
export WEB_HOST='${WEB_HOST}'
export WEB_PORT='${WEB_PORT}'
export GATEWAY_HOST='${WEB_HOST}'
export GATEWAY_PORT='${CLAW_GATEWAY_PORT}'
exec '${ONLINE_RL_PYTHON}' -m jiuwenswarm.app
"
  fi

  if pid_running "${PID_DIR}/web.pid"; then
    echo "frontend already running"
    return
  fi
  stop_web_children
  local jiuwenclaw_root="${ONLINE_RL_JIUWENCLAW_ROOT:-${WORKSPACE_ROOT}/jiuwenclaw}"
  local dist="${jiuwenclaw_root}/jiuwenclaw/web/dist"
  run_detached "web" "
export JIUWENSWARM_DATA_DIR='${JIUWEN_DATA_DIR}'
export FRONTEND_HOST='${FRONTEND_HOST}'
export FRONTEND_PORT='${FRONTEND_PORT}'
export WEB_PORT='${WEB_PORT}'
export GATEWAY_URL='http://${WEB_HOST}:${WEB_PORT}'
exec '${ONLINE_RL_PYTHON}' -m jiuwenswarm.channels.web.app_web \
  --host '${FRONTEND_HOST}' \
  --port '${FRONTEND_PORT}' \
  --dist '${dist}' \
  --proxy-target 'http://${WEB_HOST}:${WEB_PORT}'
"
}

start_all() {
  online_rl_activate_python_env
  start_vllm
  start_gateway
  start_scheduler
  start_jiuwenswarm
  status
  echo
  echo "Gateway:  ${GATEWAY_URL}"
  echo "vLLM:     ${VLLM_URL}"
  echo "Web UI:   http://${FRONTEND_HOST}:${FRONTEND_PORT}"
  echo "Logs:     ${LOG_DIR}"
}

stop_one() {
  local name="$1"
  local pid_file="${PID_DIR}/${name}.pid"
  [[ -f "${pid_file}" ]] || return 0
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  [[ -n "${pid}" ]] || return 0
  kill -TERM "${pid}" >/dev/null 2>&1 || true
}

stop_all() {
  stop_one web
  stop_one jiuwenswarm
  stop_jiuwenswarm_children
  stop_web_children
  stop_one scheduler
  sleep 5
  if online_rl_use_conda && [[ -f "${CONDA_SH}" ]]; then
    online_rl_activate_python_env
    ray stop --force >/tmp/online_rl_ray_stop.log 2>&1 || true
  elif ! online_rl_use_conda && command -v ray >/dev/null 2>&1; then
    ray stop --force >/tmp/online_rl_ray_stop.log 2>&1 || true
  fi
  stop_one gateway
  stop_one vllm
  sleep 3
  pkill -f 'vllm.entrypoints.openai.api_server' || true
  pkill -f 'openjiuwen.agent_evolving.agent_rl.online.gateway' || true
  echo "stopped online RL backend services"
}

status() {
  for name in vllm gateway scheduler jiuwenswarm web; do
    if pid_running "${PID_DIR}/${name}.pid"; then
      echo "${name}: running (pid $(cat "${PID_DIR}/${name}.pid"))"
    else
      echo "${name}: stopped"
    fi
  done
  curl -sf "${VLLM_URL}/health" >/dev/null && echo "vllm health: ok" || echo "vllm health: down"
  curl -sf "${GATEWAY_URL}/health" >/dev/null && echo "gateway health: ok" || echo "gateway health: down"
}

show_logs() {
  local name="${2:-gateway}"
  tail -f "${LOG_DIR}/${name}.log"
}

case "${1:-}" in
  start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; start_all ;;
  status) status ;;
  logs) show_logs "$@" ;;
  -h|--help|help|"") usage ;;
  *) usage; exit 2 ;;
esac
