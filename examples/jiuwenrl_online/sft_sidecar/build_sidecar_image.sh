#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_CORE_ROOT="${AGENT_CORE_ROOT:-/data1/lll/workspace/openjiuwen/code-opt/agent-core}"
JIUWENCLAW_ROOT="${JIUWENCLAW_ROOT:-/data1/lll/workspace/openjiuwen/refactor/jiuwenclaw}"
IMAGE_TAG="${SFT_SIDECAR_IMAGE_TAG:-openjiuwen-sft-sidecar:dev}"
BUILD_ROOT="${SFT_SIDECAR_BUILD_ROOT:-/tmp/openjiuwen-sft-sidecar-build}"
USE_CONDA="${USE_CONDA:-1}"

rm -rf "${BUILD_ROOT}"
mkdir -p "${BUILD_ROOT}"

python - <<'PY' "${AGENT_CORE_ROOT}" "${JIUWENCLAW_ROOT}" "${SCRIPT_DIR}" "${BUILD_ROOT}"
from __future__ import annotations

import shutil
import sys
from pathlib import Path

agent_core_root = Path(sys.argv[1]).resolve()
jiuwenclaw_root = Path(sys.argv[2]).resolve()
script_dir = Path(sys.argv[3]).resolve()
build_root = Path(sys.argv[4]).resolve()

def copytree(src: Path, dst: Path, *, ignore=None) -> None:
    if not src.exists():
        raise FileNotFoundError(src)
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)

def ignore_agent_core(_dir: str, names: list[str]) -> set[str]:
    skip = {
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "logs",
        "report",
        ".jiuwenswarm-online",
    }
    skip_files = {"*.pyc", "*.pyo", "*.log", "*.tar", "*.zip"}
    ignored = set()
    for name in names:
        if name in skip:
            ignored.add(name)
        if any(name.endswith(ext[1:]) for ext in skip_files):
            ignored.add(name)
    return ignored

def ignore_jiuwenclaw(_dir: str, names: list[str]) -> set[str]:
    skip = {
        ".git",
        ".pytest_cache",
        "__pycache__",
        ".ruff_cache",
        "htmlcov",
        "docs",
        "wiki",
        "memory",
        "context",
        "todo",
    }
    ignored = set()
    for name in names:
        if name in skip:
            ignored.add(name)
        if name.endswith((".pyc", ".pyo", ".log")):
            ignored.add(name)
    return ignored

copytree(agent_core_root / "openjiuwen", build_root / "agent-core" / "openjiuwen", ignore=ignore_agent_core)
copytree(agent_core_root / "examples" / "jiuwenrl_online" / "sft_rollout", build_root / "agent-core" / "examples" / "jiuwenrl_online" / "sft_rollout")
copytree(script_dir, build_root / "agent-core" / "examples" / "jiuwenrl_online" / "sft_sidecar")
for filename in ("pyproject.toml", "README.md"):
    src = agent_core_root / filename
    if src.exists():
        shutil.copy2(src, build_root / "agent-core" / filename)

copytree(jiuwenclaw_root / "jiuwenswarm", build_root / "jiuwenclaw" / "jiuwenswarm", ignore=ignore_jiuwenclaw)
copytree(jiuwenclaw_root / "jiuwenbox" / "src" / "jiuwenbox", build_root / "jiuwenclaw" / "jiuwenbox" / "src" / "jiuwenbox")
for filename in ("pyproject.toml", "README.md", "LICENSE"):
    src = jiuwenclaw_root / filename
    if src.exists():
        shutil.copy2(src, build_root / "jiuwenclaw" / filename)

shutil.copy2(script_dir / "Dockerfile.dev", build_root / "Dockerfile.dev")
shutil.copy2(script_dir / "Dockerfile.release", build_root / "Dockerfile.release")
PY

docker build \
  --network host \
  --build-arg "USE_CONDA=${USE_CONDA}" \
  --file "${BUILD_ROOT}/Dockerfile.dev" \
  --tag "${IMAGE_TAG}" \
  "${BUILD_ROOT}"

docker_run_args=(--rm -e "USE_CONDA=${USE_CONDA}")
if [[ "${USE_CONDA}" = "0" ]]; then
  docker_run_args+=(
    -e "AGENT_CORE_ROOT=/workspace/agent-core"
    -e "JIUWENCLAW_ROOT=/workspace/jiuwenclaw"
    -w /workspace/agent-core
  )
else
  docker_run_args+=(
    -v /data1/lll:/data1/lll:rw
    -v /data1/lll/miniconda3:/data1/lll/miniconda3:ro
    -e "AGENT_CORE_ROOT=${AGENT_CORE_ROOT}"
    -e "JIUWENCLAW_ROOT=${JIUWENCLAW_ROOT}"
    -w "${AGENT_CORE_ROOT}"
  )
fi
docker_run_args+=("${IMAGE_TAG}" bash -lc '
  set -euo pipefail
  if [[ "${USE_CONDA:-1}" != "0" ]]; then
    source /data1/lll/miniconda3/etc/profile.d/conda.sh
    conda activate "${SFT_SIDECAR_CONDA_ENV:-openjiuwen-sft}"
    export PYTHONPATH="${AGENT_CORE_ROOT}:${JIUWENCLAW_ROOT}:${PYTHONPATH:-}"
  fi
  python -c "from pathlib import Path; import openjiuwen, jiuwenswarm; print(\"openjiuwen:\", Path(openjiuwen.__file__).resolve()); print(\"jiuwenswarm:\", Path(jiuwenswarm.__file__).resolve())"
')
docker run "${docker_run_args[@]}"

echo "[sft-sidecar] built image=${IMAGE_TAG}"
