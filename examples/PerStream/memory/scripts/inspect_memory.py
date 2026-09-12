from __future__ import annotations

import argparse
import json

from video_memory.config import load_config
from video_memory.memory.store import SQLiteMemoryStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    config = load_config(args.config)
    store = SQLiteMemoryStore(config.paths.memory_db)
    nodes = store.list_nodes()[: args.limit]
    entities = store.list_entities()[: args.limit]
    print(
        json.dumps(
            {
                "node_count": len(store.list_nodes()),
                "entity_count": len(store.list_entities()),
                "nodes": [node.to_dict() for node in nodes],
                "entities": [entity.to_dict() for entity in entities],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    store.close()


if __name__ == "__main__":
    main()

