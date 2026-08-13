#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_CORE_HOST="${AGENT_CORE_HOST:-/data1/lll/workspace/openjiuwen/code-opt/agent-core}"
JIUWENCLAW_HOST="${JIUWENCLAW_HOST:-/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw}"
CONDA_ROOT="${CONDA_ROOT:-/data1/lll/miniconda3}"
USE_CONDA="${USE_CONDA:-1}"
IMAGE_TAG="${SFT_SIDECAR_IMAGE_TAG:-openjiuwen-sft-sidecar:dev}"
CASES="${SFT_SIDECAR_CASES:-/data1/lll/workspace/docker/tmp/sft_short_10_cases.json}"
LIMIT="${SFT_SIDECAR_LIMIT:-5}"
OFFSET="${SFT_SIDECAR_OFFSET:-0}"
GATEWAY_URL="${RL_GATEWAY_URL:-${TRAJECTORY_GATEWAY_URL:-http://172.17.0.5:18080}}"
SUPERVISOR_URL="${SUPERVISOR_URL:-http://172.17.0.5:18002}"
SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-EMPTY}"
SUPERVISOR_MODEL="${SUPERVISOR_MODEL:-Qwen3-0.6B}"
TENANT_ID="${RL_ONLINE_TENANT_ID:-${WEB_USER_ID:-local-web-user}}"
CONCURRENCY="${SFT_ROLLOUT_CONCURRENCY:-1}"
TIMEOUT="${SFT_TASK_ROLLOUT_TIMEOUT:-900}"
SIDECAR_CONDA_ENV="${SFT_SIDECAR_CONDA_ENV:-openjiuwen-sft}"
DRY_RUN="${SFT_SIDECAR_DRY_RUN:-0}"

docker run --rm --network host \
  -v /usr/bin/docker:/usr/bin/docker:ro \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /data1/lll:/data1/lll:rw \
  -e "SFT_SIDECAR_CONDA_ENV=${SIDECAR_CONDA_ENV}" \
  -e "SFT_SIDECAR_CASES=${CASES}" \
  -e "SFT_SIDECAR_LIMIT=${LIMIT}" \
  -e "SFT_SIDECAR_OFFSET=${OFFSET}" \
  -e "SFT_SIDECAR_GATEWAY_URL=${GATEWAY_URL}" \
  -e "SFT_SIDECAR_SUPERVISOR_URL=${SUPERVISOR_URL}" \
  -e "SFT_SIDECAR_SUPERVISOR_TOKEN=${SUPERVISOR_TOKEN}" \
  -e "SFT_SIDECAR_SUPERVISOR_MODEL=${SUPERVISOR_MODEL}" \
  -e "SFT_SIDECAR_TENANT_ID=${TENANT_ID}" \
  -e "SFT_SIDECAR_CONCURRENCY=${CONCURRENCY}" \
  -e "SFT_SIDECAR_TIMEOUT=${TIMEOUT}" \
  -e "SFT_SIDECAR_DRY_RUN=${DRY_RUN}" \
  -e "USE_CONDA=${USE_CONDA}" \
  -e "SFT_DOCKER_USE_HOST_CONDA=${USE_CONDA}" \
  -e "SFT_DOCKER_CONDA_ROOT=${CONDA_ROOT}" \
  -e "SFT_DOCKER_CONDA_ENV=openjiuwen-rl" \
  -e "SFT_DOCKER_AGENT_CORE_HOST_PATH=${AGENT_CORE_HOST}" \
  -e "SFT_DOCKER_JIUWENCLAW_HOST_PATH=${JIUWENCLAW_HOST}" \
  -e "SFT_ROLLOUT_BACKEND=docker" \
  -e "SFT_ROLLOUT_CONCURRENCY=${CONCURRENCY}" \
  -e "SFT_TASK_ROLLOUT_TIMEOUT=${TIMEOUT}" \
  -w "${AGENT_CORE_HOST}" \
  "${IMAGE_TAG}" \
  bash -lc '
    set -euo pipefail
    if [[ "${USE_CONDA:-1}" != "0" ]]; then
      source /data1/lll/miniconda3/etc/profile.d/conda.sh
      conda activate "${SFT_SIDECAR_CONDA_ENV}"
    fi
    export PYTHONPATH="${SFT_DOCKER_AGENT_CORE_HOST_PATH}:${SFT_DOCKER_JIUWENCLAW_HOST_PATH}:${PYTHONPATH:-}"
    dry_run_args=()
    if [[ "${SFT_SIDECAR_DRY_RUN}" = "1" || "${SFT_SIDECAR_DRY_RUN}" = "true" ]]; then
      dry_run_args=(--dry-run)
    fi
    python examples/jiuwenrl_online/sft_rollout/run_swe_task_rollout.py \
      --cases "${SFT_SIDECAR_CASES}" \
      --limit "${SFT_SIDECAR_LIMIT}" \
      --offset "${SFT_SIDECAR_OFFSET}" \
      --gateway-url "${SFT_SIDECAR_GATEWAY_URL}" \
      --supervisor-url "${SFT_SIDECAR_SUPERVISOR_URL}" \
      --supervisor-token "${SFT_SIDECAR_SUPERVISOR_TOKEN}" \
      --supervisor-model "${SFT_SIDECAR_SUPERVISOR_MODEL}" \
      --tenant-id "${SFT_SIDECAR_TENANT_ID}" \
      --concurrency "${SFT_SIDECAR_CONCURRENCY}" \
      --timeout "${SFT_SIDECAR_TIMEOUT}" \
      "${dry_run_args[@]}"
  '
