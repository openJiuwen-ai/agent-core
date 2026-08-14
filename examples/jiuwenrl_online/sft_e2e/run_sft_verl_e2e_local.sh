#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_DIR="${JIUWENRL_ROOT}/deploy_scripts"
AGENT_CORE_ROOT="$(cd "${JIUWENRL_ROOT}/../.." && pwd)"
WORKSPACE_ROOT="$(cd "${AGENT_CORE_ROOT}/.." && pwd)"

# shellcheck disable=SC1091
source "${DEPLOY_DIR}/online_rl_local_env.sh"

: "${SFT_E2E_CONDA_ENV:=${CONDA_ENV}}"
: "${SFT_E2E_CONDA_SH:=${CONDA_SH}}"
: "${SFT_E2E_MODEL_PATH:=/data1/lll/models/Qwen3-0.6B}"
: "${SFT_E2E_MODEL_NAME:=Qwen3-0.6B}"
: "${SFT_E2E_CASE_LIMIT:=5}"
: "${SFT_E2E_CONCURRENCY:=1}"
: "${SFT_E2E_TIMEOUT:=900}"
: "${SFT_E2E_TRAIN_GPU:=4,5,6,7}"
: "${SFT_E2E_USER_ID:=sft-verl-local-e2e}"
: "${SFT_E2E_TMP_ROOT:=/tmp/agent_rl_online}"
: "${SFT_E2E_MAX_LENGTH:=8192}"
: "${SFT_E2E_MAX_TOKEN_LEN_PER_GPU:=${SFT_E2E_MAX_LENGTH}}"
: "${SFT_E2E_LORA_RANK:=8}"
: "${SFT_E2E_LORA_ALPHA:=16}"
: "${SFT_E2E_ULYSSES_SP:=1}"
: "${SFT_E2E_TRAIN_WAIT_TIMEOUT:=1800}"
: "${SFT_OPTIMIZE_PYTHON:=${ONLINE_RL_PYTHON}}"
: "${SFT_E2E_LOCAL_PROGRAM_ROOT:=${JIUWENRL_ROOT}/sft_e2e/local_programs}"

host_ip() {
  hostname -I 2>/dev/null | tr ' ' '\n' | awk '/^172\./ { print; found=1; exit } END { if (!found) print "127.0.0.1" }'
}

HOST_IP="$(host_ip)"
DATASET_MAPPING_DEFAULT="${JIUWENRL_ROOT}/sft_e2e/data/local_python_cases.json"
: "${SFT_E2E_CASES:=${DATASET_MAPPING_DEFAULT}}"
SFT_E2E_GATEWAY_LOCAL_URL="http://127.0.0.1:${GATEWAY_PORT}"
# The business jiuwenswarm LLM and the SFT supervisor LLM are separate in
# production. This local E2E points both to the same vLLM only to keep the test
# self-contained.
SFT_E2E_SUPERVISOR_URL="http://127.0.0.1:${VLLM_PORT}"
SFT_E2E_EXPORT_PATH="/tmp/sft_samples_${SFT_E2E_USER_ID}.json"
SFT_E2E_REQUEST="我想要用 sft-optimize 技能对模型进行微调：数据集是 ${SFT_E2E_CASES}，${SFT_E2E_CASE_LIMIT}个本地 Python 用例，并发 ${SFT_E2E_CONCURRENCY}，不训练，只采集 supervisor replay 轨迹。"

rm -rf "${SFT_E2E_TMP_ROOT}" "${SFT_E2E_EXPORT_PATH}"
mkdir -p "${SFT_E2E_TMP_ROOT}"

cleanup_backend() {
  "${DEPLOY_DIR}/online_rl_backend.sh" stop >/dev/null 2>&1 || true
}
trap cleanup_backend EXIT

echo "[sft-verl-e2e-local] cleaning previous backend state"
"${DEPLOY_DIR}/clean_online_rl_env.sh"

echo "[sft-verl-e2e-local] activating ${SFT_E2E_CONDA_ENV}"
if [[ "${USE_CONDA:-1}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  # shellcheck disable=SC1090
  source "${SFT_E2E_CONDA_SH}"
  conda activate "${SFT_E2E_CONDA_ENV}"
else
  echo "[sft-verl-e2e-local] USE_CONDA=0, skip conda activation"
fi

echo "[sft-verl-e2e-local] starting backend"
export AGENT_CORE_ROOT
export JIUWENRL_ROOT
export WORKSPACE_ROOT
export PYTHONPATH="${PYTHONPATH_VALUE}"
export PYTHONUNBUFFERED=1
export ONLINE_RL_CONDA_ENV="${SFT_E2E_CONDA_ENV}"
export CONDA_ENV="${SFT_E2E_CONDA_ENV}"
export SFT_OPTIMIZE_PYTHON
export VLLM_HOST=0.0.0.0
export VLLM_URL="http://${HOST_IP}:${VLLM_PORT}"
export GATEWAY_HOST=0.0.0.0
export GATEWAY_URL="http://${HOST_IP}:${GATEWAY_PORT}"
export SUPERVISOR_URL="http://127.0.0.1:${VLLM_PORT}"
export TRAIN_BACKEND=SFT
export TRAIN_THRESHOLD=999
export ONLINE_RL_DRAIN_PENDING_ON_TRAIN=1
export ONLINE_RL_ENABLE_JUDGE=0
export SFT_DRY_RUN=0
export SFT_ROLLOUT_CONCURRENCY="${SFT_E2E_CONCURRENCY}"
export TRAIN_GPU="${SFT_E2E_TRAIN_GPU}"
export MODEL_PATH="${SFT_E2E_MODEL_PATH}"
export MODEL_NAME="${SFT_E2E_MODEL_NAME}"
export SFT_MAX_LENGTH="${SFT_E2E_MAX_LENGTH}"
export SFT_MAX_TOKEN_LEN_PER_GPU="${SFT_E2E_MAX_TOKEN_LEN_PER_GPU}"
export SFT_LORA_RANK="${SFT_E2E_LORA_RANK}"
export SFT_LORA_ALPHA="${SFT_E2E_LORA_ALPHA}"
export SFT_ULYSSES_SP="${SFT_E2E_ULYSSES_SP}"
export SFT_UPLOAD_CHECK_TIMEOUT=0
export SFT_TRAINER_COMMAND="${ONLINE_RL_PYTHON} ${JIUWENRL_ROOT}/sft_only/train_sft_from_speculative_trajectory_json.py {dataset_path} --student-model-path {base_model_path} --output-dir {output_dir} --train-gpu {training_gpu_ids} --max-length ${SFT_E2E_MAX_LENGTH} --max-token-len-per-gpu ${SFT_E2E_MAX_TOKEN_LEN_PER_GPU} --lora-rank ${SFT_E2E_LORA_RANK} --lora-alpha ${SFT_E2E_LORA_ALPHA} --ulysses-sp ${SFT_E2E_ULYSSES_SP} --dtype bfloat16"
export SFT_DOCKER_CONDA_ENV="${SFT_E2E_CONDA_ENV}"
export SFT_DOCKER_AGENT_CORE_HOST_PATH="${AGENT_CORE_ROOT}"
export SFT_DOCKER_JIUWENCLAW_HOST_PATH="${ONLINE_RL_JIUWENCLAW_ROOT}"
: "${SFT_DOCKER_USE_HOST_CONDA:=${USE_CONDA}}"
export SFT_DOCKER_USE_HOST_CONDA
export SFT_DOCKER_ROLLOUT_TIMEOUT="${SFT_E2E_TIMEOUT}"
export SFT_TASK_MAX_ITERATIONS=1
export SFT_ROLLOUT_BACKEND=local_program
export SFT_LOCAL_REPO_WEB_PORT_BASE=19100
export SFT_LOCAL_REPO_AGENT_PORT_BASE=18192
export SFT_LOCAL_PROGRAM_ROOT="${SFT_E2E_LOCAL_PROGRAM_ROOT}"

"${DEPLOY_DIR}/online_rl_backend.sh" start

echo "[sft-verl-e2e-local] collecting samples via skill wrapper"
"${ONLINE_RL_PYTHON}" "${JIUWENRL_ROOT}/skills/sft-optimize/scripts/run_sft_optimize_skill.py" \
  --request "${SFT_E2E_REQUEST}" \
  --dataset-mapping "${SFT_E2E_CASES}" \
  --limit "${SFT_E2E_CASE_LIMIT}" \
  --concurrency "${SFT_E2E_CONCURRENCY}" \
  --backend local_program \
  --gateway-url "${SFT_E2E_GATEWAY_LOCAL_URL}" \
  --scheduler-url "http://127.0.0.1:${GATEWAY_PORT}" \
  --supervisor-url "${SFT_E2E_SUPERVISOR_URL}" \
  --supervisor-token "EMPTY" \
  --supervisor-model "${SFT_E2E_MODEL_NAME}" \
  --tenant-id "${SFT_E2E_USER_ID}" \
  --no-trigger-training

"${ONLINE_RL_PYTHON}" "${SCRIPT_DIR}/validate_sft_e2e.py" \
  --phase samples \
  --redis-url "${REDIS_URL}" \
  --gateway-url "${SFT_E2E_GATEWAY_LOCAL_URL}" \
  --user-id "${SFT_E2E_USER_ID}" \
  --min-samples "${SFT_E2E_CASE_LIMIT}"

echo "[sft-verl-e2e-local] exporting pending samples"
"${ONLINE_RL_PYTHON}" "${JIUWENRL_ROOT}/sft_transfer/export_sft_samples.py" \
  --redis-url "${REDIS_URL}" \
  --user-id "${SFT_E2E_USER_ID}" \
  --status pending \
  --limit "${SFT_E2E_CASE_LIMIT}" \
  --output "${SFT_E2E_EXPORT_PATH}"

echo "[sft-verl-e2e-local] triggering manual training task"
task_json="$("${ONLINE_RL_PYTHON}" "${JIUWENRL_ROOT}/sft_transfer/trigger_sft_training.py" \
  --gateway-url "${SFT_E2E_GATEWAY_LOCAL_URL}" \
  --user-id "${SFT_E2E_USER_ID}" \
  --sample-count "${SFT_E2E_CASE_LIMIT}" \
  --metadata "{\"source\":\"sft-verl-e2e-local\",\"cases\":${SFT_E2E_CASE_LIMIT},\"max_length\":${SFT_E2E_MAX_LENGTH}}")"
printf '%s\n' "${task_json}"
task_id="$("${ONLINE_RL_PYTHON}" -c 'import json,sys; print(json.load(sys.stdin)["task_id"])' <<<"${task_json}")"
echo "[sft-verl-e2e-local] task_id=${task_id}"

"${ONLINE_RL_PYTHON}" "${SCRIPT_DIR}/validate_sft_e2e.py" \
  --phase direct-final \
  --redis-url "${REDIS_URL}" \
  --gateway-url "${SFT_E2E_GATEWAY_LOCAL_URL}" \
  --user-id "${SFT_E2E_USER_ID}" \
  --task-id "${task_id}" \
  --tmp-root "${SFT_E2E_TMP_ROOT}" \
  --min-samples "${SFT_E2E_CASE_LIMIT}" \
  --wait-timeout "${SFT_E2E_TRAIN_WAIT_TIMEOUT}"

"${ONLINE_RL_PYTHON}" - <<PY
from __future__ import annotations

import json
from urllib.request import urlopen

gateway_url = "${SFT_E2E_GATEWAY_LOCAL_URL}"
user_id = "${SFT_E2E_USER_ID}"
payload = json.loads(urlopen(f"{gateway_url}/v1/rl/lora/latest?model_id={user_id}", timeout=20).read().decode())
assert payload["model_id"] == user_id, payload
assert payload["load_status"] == "loaded", payload
assert payload["status"] == "latest", payload
assert payload["trajectory_count"] >= ${SFT_E2E_CASE_LIMIT}, payload
print(json.dumps({"ok": True, "lora_id": payload["lora_id"], "path": payload["path"], "load_status": payload["load_status"]}, ensure_ascii=False))
PY

lora_path="$(find "${LORA_REPO}/${SFT_E2E_USER_ID}" -name adapter_model.safetensors -print | sort | tail -1)"
if [[ -z "${lora_path}" ]]; then
  echo "[sft-verl-e2e-local] missing adapter_model.safetensors under ${LORA_REPO}/${SFT_E2E_USER_ID}" >&2
  exit 1
fi
echo "[sft-verl-e2e-local] adapter=${lora_path}"
echo "[sft-verl-e2e-local] export=${SFT_E2E_EXPORT_PATH}"
echo "[sft-verl-e2e-local] backend logs=${LOG_DIR}"
