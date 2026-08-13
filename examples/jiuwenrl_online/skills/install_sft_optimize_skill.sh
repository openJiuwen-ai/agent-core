#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/sft-optimize"
TARGET_ROOT="${JIUWENSWARM_SKILLS_DIR:-${HOME}/.jiuwenswarm/agent/workspace/skills}"
TARGET_DIR="${TARGET_ROOT}/sft-optimize"

mkdir -p "${TARGET_ROOT}"
rm -rf "${TARGET_DIR}"
cp -R "${SOURCE_DIR}" "${TARGET_DIR}"

echo "[sft-optimize] installed skill: ${TARGET_DIR}"
echo "[sft-optimize] restart local jiuwenswarm or run /reload-plugins if your channel supports it."
