#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/sft_mock_e2e_common.sh"

activate_sft_e2e_env

if ! sft_e2e_pid_running "${SFT_E2E_PID_DIR}/mock_openai.pid"; then
  sft_e2e_start_detached "mock_openai" "
exec python '${SFT_E2E_DIR}/mock_openai_server.py' \
  --host '${SFT_E2E_MOCK_HOST}' \
  --port '${SFT_E2E_MOCK_PORT}' \
  --model '${SFT_E2E_MODEL}'
"
fi
sft_e2e_wait_health "mock_openai" "${SFT_E2E_MOCK_LOCAL_URL}/health" 60

if ! sft_e2e_pid_running "${SFT_E2E_PID_DIR}/gateway.pid"; then
  sft_e2e_start_detached "gateway" "
export LLM_URL='${SFT_E2E_MOCK_LOCAL_URL}'
export JUDGE_URL=''
export JUDGE_MODEL=''
export MODEL_ID='${SFT_E2E_MODEL}'
export MODEL_PATH='${MODEL_PATH}'
export GATEWAY_HOST='${SFT_E2E_GATEWAY_HOST}'
export GATEWAY_PORT='${SFT_E2E_GATEWAY_PORT}'
export RECORD_DIR='${SFT_E2E_RECORD_DIR}'
export REDIS_URL='${REDIS_URL}'
export LORA_REPO_ROOT='${SFT_E2E_LORA_REPO}'
export LORA_DEFAULT_POLICY='disabled'
export TRAIN_BACKEND='SFT'
export SUPERVISOR_URL='${SFT_E2E_MOCK_LOCAL_URL}'
export SUPERVISOR_TOKEN='EMPTY'
export RL_ONLINE_CAPTURE_MODE='raw_session'
export SFT_SCENARIO='scenario2_1'
export RL_ONLINE_SESSION_DONE_ON_INVOKE_END='1'
export TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K='0'
export DISABLE_GATEWAY_TRAJECTORY_COLLECTION=true
export LOG_LEVEL=INFO
exec python -m uvicorn openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app \
  --factory --host '${SFT_E2E_GATEWAY_HOST}' --port '${SFT_E2E_GATEWAY_PORT}' --log-level info
"
fi
sft_e2e_wait_health "gateway" "${SFT_E2E_GATEWAY_LOCAL_URL}/health" 90

if ! sft_e2e_pid_running "${SFT_E2E_PID_DIR}/scheduler.pid"; then
  sft_e2e_start_detached "scheduler" "
export REDIS_URL='${REDIS_URL}'
export TRAJECTORY_GATEWAY_URL='${SFT_E2E_GATEWAY_DOCKER_URL}'
export SUPERVISOR_URL='${SFT_E2E_MOCK_DOCKER_URL}'
export SUPERVISOR_TOKEN='EMPTY'
export SUPERVISOR_MODEL='${SFT_E2E_MODEL}'
export MODEL_PATH='${MODEL_PATH}'
export SFT_DOCKER_USE_HOST_CONDA='1'
export SFT_DOCKER_CONDA_ROOT='/data1/lll/miniconda3'
export SFT_DOCKER_CONDA_ENV='${SFT_E2E_CONDA_ENV}'
export SFT_DOCKER_AGENT_CORE_HOST_PATH='${AGENT_CORE_ROOT}'
export SFT_DOCKER_JIUWENCLAW_HOST_PATH='${SFT_E2E_JIUWENCLAW_HOST_PATH}'
export SFT_DOCKER_ROLLOUT_FETCH_TIMEOUT='60'
export SFT_DOCKER_ROLLOUT_TIMEOUT='${SFT_E2E_TIMEOUT}'
export SFT_TASK_UPLOAD_SETTLE_SECONDS='8'
export SFT_TASK_PRINT_APP_LOG='${SFT_TASK_PRINT_APP_LOG:-0}'
export SFT_TASK_APP_LOG_TAIL='${SFT_TASK_APP_LOG_TAIL:-240}'
export SFT_DOCKER_ROLLOUT_DEBUG_LOG='${SFT_DOCKER_ROLLOUT_DEBUG_LOG:-0}'
export SFT_ROLLOUT_CONCURRENCY='${SFT_ROLLOUT_CONCURRENCY}'
exec python - <<'PY'
import logging
import signal
import time

from openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler import OnlineTrainingScheduler
from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRARepository

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
stop = False

def _stop(signum, frame):
    global stop
    stop = True

signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

scheduler = OnlineTrainingScheduler(
    redis_url='${REDIS_URL}',
    poll_interval=2.0,
    min_samples_for_training=1,
    base_model_path='${MODEL_PATH}',
    lora_repo=LoRARepository('${SFT_E2E_LORA_REPO}'),
    notifier=None,
    nproc_per_node=1,
    training_gpu_ids='${SFT_E2E_TRAIN_GPU}',
    tmp_root='${SFT_E2E_TMP_ROOT}',
    drain_pending_on_train=True,
    max_samples_per_run=int('${SFT_E2E_CASE_LIMIT}'),
    train_backend='SFT',
    sft_rollouter='scenario2_1',
    supervisor_url='${SFT_E2E_MOCK_DOCKER_URL}',
    supervisor_token='EMPTY',
    supervisor_model='${SFT_E2E_MODEL}',
    target_model_id='${SFT_E2E_MODEL}',
    sft_trainer_command='${SFT_E2E_TRAINER_COMMAND}',
    sft_dry_run='${SFT_E2E_DRY_RUN}'.lower() in {'1', 'true', 'yes', 'on'},
)
scheduler.start()
try:
    while not stop:
        time.sleep(1)
finally:
    scheduler.stop()
PY
"
fi

echo "[sft-e2e] backend ready"
echo "[sft-e2e] gateway local: ${SFT_E2E_GATEWAY_LOCAL_URL}"
echo "[sft-e2e] gateway docker: ${SFT_E2E_GATEWAY_DOCKER_URL}"
echo "[sft-e2e] mock local: ${SFT_E2E_MOCK_LOCAL_URL}"
echo "[sft-e2e] mock docker: ${SFT_E2E_MOCK_DOCKER_URL}"
