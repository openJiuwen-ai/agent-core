#!/usr/bin/env python3
# coding: utf-8

"""Export SFT samples from a source Redis into a portable JSON package."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from redis.asyncio import from_url as redis_from_url

AGENT_CORE_ROOT = Path(__file__).resolve().parents[3]
if str(AGENT_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_CORE_ROOT))

from openjiuwen.agent_evolving.agent_rl.online.backends.sft.redis_store import RedisSFTStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--redis-url",
        default=os.getenv("SFT_REDIS_URL") or os.getenv("REDIS_URL") or "redis://127.0.0.1:6379/0",
        help="Source Redis URL holding SFT samples.",
    )
    parser.add_argument(
        "--user-id",
        default=os.getenv("RL_ONLINE_TENANT_ID") or os.getenv("WEB_USER_ID") or "local-web-user",
        help="User/tenant id to export.",
    )
    parser.add_argument(
        "--status",
        default="pending",
        help="Sample status to export, or 'all' for all statuses.",
    )
    parser.add_argument("--limit", type=int, default=int(os.getenv("SFT_EXPORT_LIMIT", "100000")))
    parser.add_argument(
        "--output",
        default=str(Path.cwd() / f"sft_samples_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"),
        help="Output JSON package path.",
    )
    return parser.parse_args()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            dumped = value.model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return _json_safe(dumped)
    return str(value)


def _strip_runtime_fields(sample: dict[str, Any]) -> dict[str, Any]:
    out = dict(sample)
    out.pop("_store_status", None)
    return _json_safe(out)


async def _export_samples(args: argparse.Namespace) -> dict[str, Any]:
    redis = redis_from_url(args.redis_url, decode_responses=False)
    try:
        store = RedisSFTStore(redis)
        status = None if str(args.status).strip().lower() == "all" else str(args.status).strip() or None
        samples = await store.list_samples(user_id=args.user_id, status=status, limit=args.limit)
        return {
            "protocol_version": "sft-transfer-v1",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "redis_url": args.redis_url,
                "user_id": args.user_id,
                "status": status or "all",
                "limit": int(args.limit),
            },
            "samples": [_strip_runtime_fields(sample) for sample in samples],
        }
    finally:
        await redis.aclose()


def main() -> int:
    args = parse_args()
    package = asyncio.run(_export_samples(args))
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported={output}")
    print(f"samples={len(package['samples'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
