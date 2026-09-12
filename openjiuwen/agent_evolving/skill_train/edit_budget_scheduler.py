# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-step edit budget schedules for skill_train optimization loops."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod


def _progress(step_index: int, total_steps: int) -> float:
    if total_steps <= 1:
        return 0.0
    return min(step_index, total_steps) / float(total_steps)


class LRScheduler(ABC):
    def __init__(self, max_lr: int, min_lr: int, total_steps: int) -> None:
        self.max_lr = int(max_lr)
        self.min_lr = int(min_lr)
        self.total_steps = int(total_steps)
        self._cursor = 0

    @abstractmethod
    def budget_for(self, step_index: int) -> int:
        raise NotImplementedError

    def step(self) -> int:
        self._cursor += 1
        return self.budget_for(self._cursor)

    def get_lr(self, step: int) -> int:
        return self.budget_for(int(step))

    def state_dict(self) -> dict:
        return {"current_step": self._cursor}

    def load_state_dict(self, state: dict) -> None:
        self._cursor = int(state.get("current_step", 0))


class ConstantScheduler(LRScheduler):
    def budget_for(self, step_index: int) -> int:
        return self.max_lr


class LinearScheduler(LRScheduler):
    def budget_for(self, step_index: int) -> int:
        p = _progress(step_index, self.total_steps)
        return max(self.min_lr, round(self.max_lr * (1.0 - p) + self.min_lr * p))


class CosineScheduler(LRScheduler):
    def budget_for(self, step_index: int) -> int:
        p = _progress(step_index, self.total_steps)
        # Equivalent half-cosine decay rewritten without (1+cos)/2 form.
        wave = math.cos(math.pi * p)
        return max(self.min_lr, round(self.min_lr + (self.max_lr - self.min_lr) * (wave + 1.0) / 2.0))


class AutonomousScheduler(LRScheduler):
    NO_LIMIT = 999

    def budget_for(self, step_index: int) -> int:
        return type(self).NO_LIMIT


def build_scheduler(
    mode: str = "constant",
    max_lr: int = 8,
    min_lr: int = 2,
    total_steps: int = 8,
) -> LRScheduler:
    table = {
        "constant": ConstantScheduler,
        "linear": LinearScheduler,
        "cosine": CosineScheduler,
        "autonomous": AutonomousScheduler,
    }
    cls = table.get(mode)
    if cls is None:
        raise ValueError(f"Unknown scheduler mode '{mode}'. Available: {sorted(table)}")
    return cls(max_lr=max_lr, min_lr=min_lr, total_steps=total_steps)
