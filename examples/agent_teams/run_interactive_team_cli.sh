#!/usr/bin/env bash
# Launch the interactive Team CLI from the repository root.
#
# Activates the local .venv, sets PYTHONPATH, exports the model /
# endpoint defaults that examples/agent_teams/main.py used to set in
# Python, then runs examples/agent_teams/interactive_team_cli.py. Safe
# to invoke from any working directory — the script resolves the repo
# root from its own location. Forwards extra args to the launcher, so
# you can pass an alternate yaml:
#
#     ./examples/agent_teams/run_interactive_team_cli.sh \
#         examples/agent_teams/config_hitt_blast_furnace.yaml
#
# Override any default by exporting the variable before invocation:
#
#     export MODEL_NAME=glm-5.1
#     ./examples/agent_teams/run_interactive_team_cli.sh
#
# Windows: the project keeps shell scripts unix-only. Run the launcher
# directly with `python examples\agent_teams\interactive_team_cli.py`
# from an activated venv instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${REPO_ROOT}"

if [[ ! -f ".venv/bin/activate" ]]; then
    echo "error: .venv/bin/activate not found in ${REPO_ROOT}" >&2
    echo "       run 'uv sync' first to create the virtual environment." >&2
    exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

# Default model / endpoint env (mirrors the os.environ.setdefault block
# in examples/agent_teams/main.py). `${VAR:-default}` keeps any value
# the user already exported.
export API_KEY="${API_KEY:-sk-1e61b6de1f9b4ccab4a117d3ce2e33b4}"
export LEADER_API_KEY="${LEADER_API_KEY:-sk-1e61b6de1f9b4ccab4a117d3ce2e33b4}"
export TEAMMATE_API_KEY="${TEAMMATE_API_KEY:-sk-1e61b6de1f9b4ccab4a117d3ce2e33b4}"
export API_BASE="${API_BASE:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
export MODEL_NAME="${MODEL_NAME:-glm-5}"

exec python examples/agent_teams/interactive_team_cli.py "$@"
