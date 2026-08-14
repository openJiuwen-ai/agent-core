#!/usr/bin/env bash
set -euo pipefail

# Build and run a local Redis service from an official Redis source tarball.
# This script is intentionally self-contained and does not download anything.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REDIS_TARBALL="${REDIS_TARBALL:-}"
REDIS_INSTALL_ROOT="${REDIS_INSTALL_ROOT:-${JIUWENRL_ROOT}/.local_redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_BIND="${REDIS_BIND:-127.0.0.1}"
REDIS_DB_FILENAME="${REDIS_DB_FILENAME:-dump.rdb}"

SRC_DIR="${REDIS_INSTALL_ROOT}/src"
BUILD_DIR="${REDIS_INSTALL_ROOT}/build"
RUN_DIR="${REDIS_INSTALL_ROOT}/run"
DATA_DIR="${REDIS_INSTALL_ROOT}/data"
LOG_DIR="${REDIS_INSTALL_ROOT}/logs"
CONF_FILE="${RUN_DIR}/redis.conf"
PID_FILE="${RUN_DIR}/redis.pid"
LOG_FILE="${LOG_DIR}/redis.log"

OFFICIAL_STABLE_URL="https://download.redis.io/redis-stable.tar.gz"
REMAINING_ARGS=()

usage() {
  cat <<EOF
Usage:
  $(basename "$0") install --tarball /path/to/redis-stable.tar.gz
  $(basename "$0") start [--tarball /path/to/redis-stable.tar.gz]
  $(basename "$0") stop
  $(basename "$0") restart [--tarball /path/to/redis-stable.tar.gz]
  $(basename "$0") status
  $(basename "$0") verify
  $(basename "$0") cli [redis-cli args...]
  $(basename "$0") clean

Environment:
  REDIS_TARBALL       Redis source tarball path.
  REDIS_INSTALL_ROOT  Runtime/build root. Default: ${REDIS_INSTALL_ROOT}
  REDIS_BIND          Bind address. Default: ${REDIS_BIND}
  REDIS_PORT          Port. Default: ${REDIS_PORT}

Official latest stable tarball:
  ${OFFICIAL_STABLE_URL}
EOF
}

parse_common_args() {
  REMAINING_ARGS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tarball)
        REDIS_TARBALL="${2:?missing value for --tarball}"
        shift 2
        ;;
      --root)
        REDIS_INSTALL_ROOT="${2:?missing value for --root}"
        shift 2
        ;;
      --bind)
        REDIS_BIND="${2:?missing value for --bind}"
        shift 2
        ;;
      --port)
        REDIS_PORT="${2:?missing value for --port}"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        REMAINING_ARGS=("$@")
        break
        ;;
    esac
  done
  SRC_DIR="${REDIS_INSTALL_ROOT}/src"
  BUILD_DIR="${REDIS_INSTALL_ROOT}/build"
  RUN_DIR="${REDIS_INSTALL_ROOT}/run"
  DATA_DIR="${REDIS_INSTALL_ROOT}/data"
  LOG_DIR="${REDIS_INSTALL_ROOT}/logs"
  CONF_FILE="${RUN_DIR}/redis.conf"
  PID_FILE="${RUN_DIR}/redis.pid"
  LOG_FILE="${LOG_DIR}/redis.log"
}

redis_server_bin() {
  printf '%s\n' "${BUILD_DIR}/src/redis-server"
}

redis_cli_bin() {
  printf '%s\n' "${BUILD_DIR}/src/redis-cli"
}

pid_running() {
  [[ -f "${PID_FILE}" ]] || return 1
  local pid
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1
}

require_tarball() {
  if [[ -z "${REDIS_TARBALL}" ]]; then
    echo "[redis] missing tarball. Use --tarball /path/to/redis-stable.tar.gz or REDIS_TARBALL=..." >&2
    exit 2
  fi
  if [[ ! -f "${REDIS_TARBALL}" ]]; then
    echo "[redis] tarball not found: ${REDIS_TARBALL}" >&2
    exit 2
  fi
}

install_redis() {
  require_tarball
  mkdir -p "${SRC_DIR}" "${BUILD_DIR}" "${RUN_DIR}" "${DATA_DIR}" "${LOG_DIR}"

  echo "[redis] extracting ${REDIS_TARBALL}"
  rm -rf "${SRC_DIR}"/*
  tar -xzf "${REDIS_TARBALL}" -C "${SRC_DIR}" --strip-components=1

  echo "[redis] building under ${SRC_DIR}"
  make -C "${SRC_DIR}" -j"$(nproc)"

  rm -rf "${BUILD_DIR}"
  mkdir -p "${BUILD_DIR}"
  cp -a "${SRC_DIR}/." "${BUILD_DIR}/"

  "$(redis_server_bin)" --version
  "$(redis_cli_bin)" --version
}

write_config() {
  mkdir -p "${RUN_DIR}" "${DATA_DIR}" "${LOG_DIR}"
  cat > "${CONF_FILE}" <<EOF
bind ${REDIS_BIND}
port ${REDIS_PORT}
protected-mode yes
daemonize yes
supervised no
pidfile ${PID_FILE}
logfile ${LOG_FILE}
dir ${DATA_DIR}
dbfilename ${REDIS_DB_FILENAME}
appendonly no
save ""
tcp-keepalive 60
timeout 0
EOF
}

ensure_installed() {
  if [[ ! -x "$(redis_server_bin)" || ! -x "$(redis_cli_bin)" ]]; then
    install_redis
  fi
}

start_redis() {
  ensure_installed
  if pid_running; then
    echo "[redis] already running pid=$(cat "${PID_FILE}") redis://${REDIS_BIND}:${REDIS_PORT}/0"
    return 0
  fi

  write_config
  echo "[redis] starting redis://${REDIS_BIND}:${REDIS_PORT}/0"
  "$(redis_server_bin)" "${CONF_FILE}"

  for _ in $(seq 1 30); do
    if "$(redis_cli_bin)" -h "${REDIS_BIND}" -p "${REDIS_PORT}" PING >/dev/null 2>&1; then
      echo "[redis] ready: redis://${REDIS_BIND}:${REDIS_PORT}/0"
      echo "[redis] export REDIS_URL=redis://${REDIS_BIND}:${REDIS_PORT}/0"
      return 0
    fi
    sleep 1
  done

  echo "[redis] failed to become ready; log: ${LOG_FILE}" >&2
  tail -80 "${LOG_FILE}" >&2 || true
  exit 1
}

stop_redis() {
  if [[ -x "$(redis_cli_bin)" ]]; then
    "$(redis_cli_bin)" -h "${REDIS_BIND}" -p "${REDIS_PORT}" SHUTDOWN NOSAVE >/dev/null 2>&1 || true
  fi

  if pid_running; then
    local pid
    pid="$(cat "${PID_FILE}")"
    kill -TERM "${pid}" >/dev/null 2>&1 || true
    for _ in $(seq 1 10); do
      pid_running || break
      sleep 1
    done
  fi
  rm -f "${PID_FILE}"
  echo "[redis] stopped"
}

status_redis() {
  if pid_running; then
    echo "[redis] running pid=$(cat "${PID_FILE}") redis://${REDIS_BIND}:${REDIS_PORT}/0"
  else
    echo "[redis] stopped redis://${REDIS_BIND}:${REDIS_PORT}/0"
  fi

  if [[ -x "$(redis_cli_bin)" ]] && "$(redis_cli_bin)" -h "${REDIS_BIND}" -p "${REDIS_PORT}" PING >/dev/null 2>&1; then
    echo "[redis] ping: PONG"
  else
    echo "[redis] ping: failed"
  fi
}

verify_redis() {
  if [[ ! -x "$(redis_cli_bin)" ]]; then
    echo "[redis] redis-cli not found; run install/start first" >&2
    exit 1
  fi

  local key="online_rl:redis_verify:$$"
  local value="ok-$(date +%s)"

  echo "[redis] verifying redis://${REDIS_BIND}:${REDIS_PORT}/0"
  "$(redis_cli_bin)" -h "${REDIS_BIND}" -p "${REDIS_PORT}" PING | grep -qx "PONG"
  "$(redis_cli_bin)" -h "${REDIS_BIND}" -p "${REDIS_PORT}" SET "${key}" "${value}" | grep -qx "OK"
  local got
  got="$("$(redis_cli_bin)" -h "${REDIS_BIND}" -p "${REDIS_PORT}" GET "${key}")"
  if [[ "${got}" != "${value}" ]]; then
    echo "[redis] GET mismatch: expected=${value} got=${got}" >&2
    exit 1
  fi
  "$(redis_cli_bin)" -h "${REDIS_BIND}" -p "${REDIS_PORT}" DEL "${key}" >/dev/null
  if [[ -n "$("$(redis_cli_bin)" -h "${REDIS_BIND}" -p "${REDIS_PORT}" GET "${key}")" ]]; then
    echo "[redis] DEL failed for ${key}" >&2
    exit 1
  fi
  echo "[redis] verify ok"
}

clean_redis() {
  stop_redis
  if [[ -z "${REDIS_INSTALL_ROOT}" || "${REDIS_INSTALL_ROOT}" == "/" ]]; then
    echo "[redis] refusing to remove unsafe REDIS_INSTALL_ROOT=${REDIS_INSTALL_ROOT}" >&2
    exit 1
  fi
  rm -rf "${REDIS_INSTALL_ROOT}"
  echo "[redis] removed ${REDIS_INSTALL_ROOT}"
}

cmd="${1:-}"
[[ $# -gt 0 ]] && shift || true

case "${cmd}" in
  install)
    parse_common_args "$@"
    install_redis
    ;;
  start)
    parse_common_args "$@"
    start_redis
    ;;
  stop)
    parse_common_args "$@"
    stop_redis
    ;;
  restart)
    parse_common_args "$@"
    stop_redis
    start_redis
    ;;
  status)
    parse_common_args "$@"
    status_redis
    ;;
  verify)
    parse_common_args "$@"
    verify_redis
    ;;
  cli)
    parse_common_args "$@"
    if [[ ! -x "$(redis_cli_bin)" ]]; then
      echo "[redis] redis-cli not found; run install/start first" >&2
      exit 1
    fi
    "$(redis_cli_bin)" -h "${REDIS_BIND}" -p "${REDIS_PORT}" "${REMAINING_ARGS[@]}"
    ;;
  clean)
    parse_common_args "$@"
    clean_redis
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    echo "[redis] unknown command: ${cmd}" >&2
    usage
    exit 2
    ;;
esac
