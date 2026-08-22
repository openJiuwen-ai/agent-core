from __future__ import annotations

import argparse
import json

from video_memory.config import load_config
from video_memory.pipelines.run_qa import RunQAPipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--qa-id")
    parser.add_argument("--qa-index", type=int, default=0)
    args = parser.parse_args()

    config = load_config(args.config)
    answer = RunQAPipeline(config).run_one(qa_id=args.qa_id, qa_index=args.qa_index)
    print(json.dumps(answer.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

