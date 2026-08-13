#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  cat >&2 <<'EOF'
Usage:
  send_online_rl_msg.sh "message"

Optional env vars:
  ONLINE_RL_SESSION_ID   Session id reused across calls. Default: manual_online_rl_cli
  ONLINE_RL_WS_URL       WebSocket URL. Default: ws://127.0.0.1:19000/ws
  ONLINE_RL_CWD          Project cwd sent to JiuwenClaw. Default: workspace root
  ONLINE_RL_CONDA_ENV    Conda env. Default: openjiuwen-rl
  USE_CONDA              Set to 0 to skip conda activation and use system python.
EOF
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIUWENRL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
AGENT_CORE_ROOT="$(cd "${JIUWENRL_ROOT}/../.." && pwd)"
WORKSPACE_ROOT="$(cd "${AGENT_CORE_ROOT}/.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/online_rl_local_env.sh"

WS_URL="${ONLINE_RL_WS_URL}"
SESSION_ID="${ONLINE_RL_SESSION_ID:-manual_online_rl_cli}"
RUN_CWD="${ONLINE_RL_CWD:-${WORKSPACE_ROOT}}"
MSG="$*"

online_rl_activate_python_env
export PYTHONPATH="${AGENT_CORE_ROOT}:${WORKSPACE_ROOT}/jiuwenclaw"
export ONLINE_RL_WS_URL="${WS_URL}"
export ONLINE_RL_SESSION_ID="${SESSION_ID}"
export ONLINE_RL_CWD="${RUN_CWD}"
export ONLINE_RL_MESSAGE="${MSG}"

"${ONLINE_RL_PYTHON}" - <<'PY'
import asyncio
import json
import os
import time
import uuid

import websockets


URL = os.environ["ONLINE_RL_WS_URL"]
SESSION = os.environ["ONLINE_RL_SESSION_ID"]
CWD = os.environ["ONLINE_RL_CWD"]
MESSAGE = os.environ["ONLINE_RL_MESSAGE"]


def extract_text(msg: dict, payload: dict) -> str:
    for obj in (payload, msg):
        for key in ("content", "text", "delta", "output"):
            value = obj.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


async def wait_done(ws, req_id: str) -> None:
    started = time.time()
    printed_header = False
    printed_any = False
    while time.time() - started < 900:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=900))
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        event = msg.get("event") or payload.get("event_type")
        if msg.get("type") == "res" and msg.get("id") == req_id:
            print(f"ack ok={msg.get('ok')}")

        text = extract_text(msg, payload)
        should_print = False
        if text and event in {"chat.delta", "answer", "llm_output", "content_chunk"}:
            should_print = True
        elif text and event == "chat.final" and not printed_any:
            should_print = True

        if should_print:
            if not printed_header:
                print("assistant:")
                printed_header = True
            print(text, end="", flush=True)
            printed_any = True

        if event == "chat.processing_status":
            print(f"processing={payload.get('is_processing')}")
            if payload.get("is_processing") is False:
                if printed_any:
                    print()
                return
    raise TimeoutError("message did not finish within 900s")


async def main() -> None:
    req_id = "chat-" + uuid.uuid4().hex[:12]
    frame = {
        "type": "req",
        "id": req_id,
        "method": "chat.send",
        "is_stream": True,
        "params": {
            "session_id": SESSION,
            "content": MESSAGE,
            "query": MESSAGE,
            "mode": "agent.plan",
            "cwd": CWD,
            "project_dir": CWD,
            "trusted_dirs": [CWD],
        },
    }

    async with websockets.connect(URL, max_size=8 * 2**20, close_timeout=2) as ws:
        hello = await asyncio.wait_for(ws.recv(), timeout=15)
        print(f"connected session={SESSION} req_id={req_id}")
        print(f"server={hello[:240]}")
        await ws.send(json.dumps(frame, ensure_ascii=False))
        await wait_done(ws, req_id)
        print("done")


asyncio.run(main())
PY
