#!/usr/bin/env python3
# coding: utf-8

"""Generate synthetic long-context trajectory samples for PPO resource tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
JIUWENRL_ROOT = SCRIPT_DIR.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic long-context trajectory JSON for resource testing.",
    )
    parser.add_argument(
        "--output",
        default=str(SCRIPT_DIR / "long_context_4x50k_trajectories.json"),
        help="Output JSON path.",
    )
    parser.add_argument("--samples", type=int, default=4, help="Number of trajectories.")
    parser.add_argument("--prompt-tokens", type=int, default=50000, help="Prompt token count per trajectory.")
    parser.add_argument("--response-tokens", type=int, default=1, help="Response token count per trajectory.")
    parser.add_argument(
        "--sample-id-prefix",
        default="long-context",
        help="Prefix for sample_id/session_id fields.",
    )
    parser.add_argument(
        "--prompt-token-id",
        type=int,
        default=2266,
        help="Token id repeated in prompt_ids. For Qwen3 tokenizer, 2266 is ' context'.",
    )
    parser.add_argument(
        "--response-token-id",
        type=int,
        default=16,
        help="Token id repeated in response_ids. For Qwen3 tokenizer, 16 is '1'.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.samples <= 0:
        raise ValueError("--samples must be positive")
    if args.prompt_tokens <= 0:
        raise ValueError("--prompt-tokens must be positive")
    if args.response_tokens <= 0:
        raise ValueError("--response-tokens must be positive")

    prompt_ids = [args.prompt_token_id] * args.prompt_tokens
    response_ids = [args.response_token_id] * args.response_tokens
    response_logprobs = [-0.1] * args.response_tokens

    samples = []
    for idx in range(args.samples):
        score = 1.0 if idx % 2 == 0 else 0.5
        samples.append(
            {
                "sample_id": f"{args.sample_id_prefix}-{idx}",
                "created_at": "",
                "user_id": "local-web-user",
                "session_id": args.sample_id_prefix,
                "turn_num": idx + 1,
                "mode": "synthetic_long_context",
                "io_mode": "direct",
                "model": "Qwen3-4B-Thinking-2507",
                "trajectory": {
                    "prompt_ids": prompt_ids,
                    "response_ids": response_ids,
                    "response_logprobs": response_logprobs,
                    "prompt_text": "",
                    "response_text": "1",
                },
                "judge": {
                    "score": score,
                },
            }
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"samples": samples}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    total_tokens = args.samples * (args.prompt_tokens + args.response_tokens)
    print(f"wrote {output}")
    print(
        f"samples={args.samples} prompt_tokens={args.prompt_tokens} "
        f"response_tokens={args.response_tokens} total_tokens={total_tokens}"
    )


if __name__ == "__main__":
    main()
