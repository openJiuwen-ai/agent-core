# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Provider-reported USD must survive SDK typing (OpenRouter cascade)."""

from types import SimpleNamespace

from openjiuwen.core.foundation.llm.model_clients.base_model_client import (
    BaseModelClient,
)


def _cost(usage) -> tuple[float, float, float]:
    return BaseModelClient._extract_cost_info(usage)


def test_openrouter_numeric_cost_on_dict_usage() -> None:
    """OpenRouter puts ``usage.cost`` on the wire as a float."""
    usage = {
        "prompt_tokens": 194,
        "completion_tokens": 2,
        "total_tokens": 196,
        "cost": 0.0015,
        "cost_details": {
            "upstream_inference_cost": 0.0012,
            "upstream_inference_prompt_cost": 0.0010,
            "upstream_inference_completions_cost": 0.0002,
        },
    }

    input_cost, output_cost, total_cost = _cost(usage)

    assert total_cost == 0.0015
    # Numeric top-level cost wins; details are only a fallback when cost is absent.
    assert input_cost == 0.0
    assert output_cost == 0.0


def test_openrouter_cost_in_pydantic_model_extra() -> None:
    """Typed OpenAI Usage often drops unknown fields into model_extra."""
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        model_extra={"cost": 0.00042},
    )

    _, _, total_cost = _cost(usage)

    assert total_cost == 0.00042


def test_openrouter_cost_in_pydantic_extra_dunder() -> None:
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        __pydantic_extra__={"cost": 0.0007},
    )

    _, _, total_cost = _cost(usage)

    assert total_cost == 0.0007


def test_cost_details_fallback_when_cost_missing() -> None:
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cost_details": {
            "upstream_inference_prompt_cost": 0.001,
            "upstream_inference_completions_cost": 0.002,
            "upstream_inference_cost": 0.003,
        },
    }

    input_cost, output_cost, total_cost = _cost(usage)

    assert input_cost == 0.001
    assert output_cost == 0.002
    assert total_cost == 0.003


def test_structured_cost_object() -> None:
    usage = SimpleNamespace(
        cost=SimpleNamespace(input_cost=0.01, output_cost=0.02, total_cost=0.0),
    )

    input_cost, output_cost, total_cost = _cost(usage)

    assert input_cost == 0.01
    assert output_cost == 0.02
    assert total_cost == 0.03


def test_structured_cost_dict() -> None:
    usage = {"cost": {"prompt_cost": 0.01, "completion_cost": 0.04}}

    input_cost, output_cost, total_cost = _cost(usage)

    assert input_cost == 0.01
    assert output_cost == 0.04
    assert total_cost == 0.05


def test_no_cost_fields_returns_zeros() -> None:
    """DeepSeek-shaped usage: tokens only, no USD."""
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_cache_hit_tokens": 80,
        "prompt_cache_miss_tokens": 20,
    }

    assert _cost(usage) == (0.0, 0.0, 0.0)


def test_attribute_cost_still_works() -> None:
    usage = SimpleNamespace(cost=0.99, cost_details=None)

    _, _, total_cost = _cost(usage)

    assert total_cost == 0.99
