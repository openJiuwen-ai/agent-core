from __future__ import annotations

import argparse
import json
import sys

from video_memory.config import load_config
from video_memory.pipelines.build_memory import BuildMemoryPipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--limit-windows", type=int)
    args = parser.parse_args()

    config = load_config(args.config)
    summary = BuildMemoryPipeline(config).run(clear_existing=args.clear, limit_windows=args.limit_windows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(_exit_code(summary))


def _exit_code(summary: dict) -> int:
    """Non-zero when the model cited frames that do not exist in their window.

    The build still completes and stores everything it could bind, so the API
    spend is not wasted and every violation is reported at once rather than one
    failure per re-run.
    """
    count = int(summary.get("rejected_node_count", 0))
    if not count:
        return 0
    print(
        f"\n{count} generated node(s) were dropped: the model cited frame ids that are "
        f"not in their window. See rejected_nodes above and in the traces.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    main()
