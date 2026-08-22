from __future__ import annotations

import argparse
import json

from video_memory.config import load_config
from video_memory.pipelines.evaluate import EvaluatePipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    config = load_config(args.config)
    summary = EvaluatePipeline(config).run(limit=args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

