# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Best-effort, versioned model cost estimation.

Providers already return cost on many responses and those values flow straight
through to span attributes (``OJ_GEN_AI_USAGE_*_COST``). This module fills the
gap when a provider omits cost: a versioned per-1M-token price table that users
can override without editing code. Unknown models are reported with zero cost
and ``known=False`` — never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICING_VERSION = "2026-09-01"


@dataclass(frozen=True)
class ModelPrice:
    input_usd_per_1m: float
    output_usd_per_1m: float


@dataclass(frozen=True)
class CostEstimate:
    input_cost: float
    output_cost: float
    total_cost: float
    pricing_version: str
    known: bool


_DEFAULT_PRICES: dict[str, ModelPrice] = {
    # Placeholder illustrative entries; fill from the current provider price
    # pages at implementation time and keep them conservative.
}

_PRICING: dict[str, ModelPrice] = dict(_DEFAULT_PRICES)
_VERSION = PRICING_VERSION


def register_model_prices(version: str, prices: dict[str, ModelPrice]) -> None:
    """Replace the active table (full replacement, not a merge).

    Callers who want to layer overrides must pass the complete table. This
    keeps a registered version a single authoritative snapshot.
    """
    global _PRICING, _VERSION
    _PRICING = dict(prices)
    _VERSION = version


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> CostEstimate:
    price = _PRICING.get(model)
    if price is None:
        return CostEstimate(0.0, 0.0, 0.0, _VERSION, False)
    input_cost = prompt_tokens / 1_000_000 * price.input_usd_per_1m
    output_cost = completion_tokens / 1_000_000 * price.output_usd_per_1m
    return CostEstimate(input_cost, output_cost, input_cost + output_cost, _VERSION, True)
