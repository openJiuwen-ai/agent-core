#!/usr/bin/env bash
# Launch agent_team_lifecycle_e2e.py from the repository root.
#
# Activates the local .venv, sets PYTHONPATH, exports the model /
# endpoint defaults that tests/system_tests/agent_swarm/config.yaml interpolates,
# then runs the lifecycle E2E walkthrough. Safe to invoke from any
# working directory — the script resolves the repo root from its own
# location.
#
# Run all scenarios:
#     ./tests/system_tests/agent_swarm/run_lifecycle_e2e.sh
#
# Run a subset (pass scenario names after `--`):
#     ./tests/system_tests/agent_swarm/run_lifecycle_e2e.sh -- cold_recover resume
#
# Available scenarios (see agent_team_lifecycle_e2e.py for details):
#     create, resume, stop_new_session, cold_recover, session_switch
#
# Override the model / endpoint defaults by exporting before invocation:
#
#     export MODEL_NAME=glm-5.1
#     ./tests/system_tests/agent_swarm/run_lifecycle_e2e.sh
#
# Windows: project keeps shell scripts unix-only. Run the launcher
# directly with `python tests\system_tests\agent_swarm\agent_team_lifecycle_e2e.py`
# from an activated venv instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

cd "${REPO_ROOT}"

if [[ ! -f ".venv/bin/activate" ]]; then
    echo "error: .venv/bin/activate not found in ${REPO_ROOT}" >&2
    echo "       run 'uv sync' first to create the virtual environment." >&2
    exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

# Model / endpoint defaults consumed by tests/system_tests/agent_swarm/config.yaml
# (env interpolation happens in _e2e_utils.load_team_config). `${VAR:-default}`
# keeps any value the user already exported.
export API_KEY="${API_KEY:-sk-xxx}"
export LEADER_API_KEY="${LEADER_API_KEY:-sk-xxx}"
export TEAMMATE_API_KEY="${TEAMMATE_API_KEY:-sk-xxx}"
export API_BASE="${API_BASE:-https://xxx}"
export MODEL_NAME="${MODEL_NAME:-xxx}"

# Forward args after a `--` separator to the python entry; otherwise pass
# everything through so `run_lifecycle_e2e.sh cold_recover` keeps working
# without the extra `--`.
ARGS=()
saw_separator=0
for arg in "$@"; do
    if [[ "${saw_separator}" -eq 0 && "${arg}" == "--" ]]; then
        saw_separator=1
        continue
    fi
    ARGS+=("${arg}")
done

exec python tests/system_tests/agent_swarm/agent_team_lifecycle_e2e.py "${ARGS[@]}"
