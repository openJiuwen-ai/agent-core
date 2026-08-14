#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/online_rl_local_env.sh"

# shellcheck disable=SC1090
source "${CONDA_SH}"
conda activate "${CONDA_ENV}"
export PYTHONPATH="${PYTHONPATH_VALUE}"

python - <<'PY'
import asyncio
import logging

from examples.jiuwenrl_online.sandbox_plugins import BasicSandboxRollouter
from openjiuwen.agent_evolving.agent_rl.online.scheduler import RolloutRequest
from openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins import call_rollouter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

async def main():
    rollout = await call_rollouter(
        BasicSandboxRollouter(),
        RolloutRequest(
            user_id="sandbox-smoke-user",
            samples=[{"sample_id": "s1", "request": {"messages": [{"role": "user", "content": "hello"}]}}],
            prompts=[[{"role": "user", "content": "hello"}]],
            training_count=1,
        ),
    )
    print("rollout", rollout)

asyncio.run(main())
PY
