#!/usr/bin/env python3
# coding: utf-8

"""Repository-local entrypoint for direct supervisor SWE rollouts."""

from __future__ import annotations

from openjiuwen.agent_evolving.agent_rl.online.backends.sft.optimize_cli import (
    create_training_task,
    detect_redis_url,
    gateway_pending_sft_sample_count,
    main,
    pending_sft_sample_count,
    training_task_metadata,
    wait_for_pending_sft_samples,
)

__all__ = [
    "create_training_task",
    "detect_redis_url",
    "gateway_pending_sft_sample_count",
    "main",
    "pending_sft_sample_count",
    "training_task_metadata",
    "wait_for_pending_sft_samples",
]


if __name__ == "__main__":
    raise SystemExit(main())
