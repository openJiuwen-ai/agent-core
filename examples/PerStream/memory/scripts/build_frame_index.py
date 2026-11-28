from __future__ import annotations

import argparse
import json

from video_memory.config import load_config
from video_memory.data.frame_index import build_frame_index


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    frame_index = build_frame_index(config.paths.frames_dir)
    print(json.dumps({"frames": len(frame_index), "first": frame_index.frames[0].to_dict()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

