"""Minimal early bootstrap for the maintained accidental-write guard."""
import os
from pathlib import Path
import sys

try:
    from examples.adaptive_multi_agent_collab.infrastructure.write_guard import deny_network, install

    install(Path(__file__).resolve().parents[2], scratch=os.environ["TMPDIR"])
    if os.environ.get("MA_COLLAB_NETWORK_DISABLED") == "1":
        sys.addaudithook(deny_network)
except BaseException:
    # Python normally prints and ignores sitecustomize failures. Never proceed
    # with an execution whose requested write protection did not initialize.
    sys.stderr.write("experiment filesystem guard initialization failed\n")
    os._exit(78)
