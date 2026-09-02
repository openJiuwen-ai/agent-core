# coding: utf-8

import pytest

from openjiuwen.extensions.observability import cost_tracker as ct
from openjiuwen.extensions.observability.cost_tracker import (
    PRICING_VERSION,
    ModelPrice,
    estimate_cost,
    register_model_prices,
)


@pytest.fixture
def reset_pricing():
    saved_prices = ct._PRICING
    saved_version = ct._VERSION
    yield
    ct._PRICING = saved_prices
    ct._VERSION = saved_version


def test_known_model_estimates_cost(reset_pricing):
    register_model_prices("test", {"my-model": ModelPrice(1.0, 2.0)})
    est = estimate_cost("my-model", 1000, 500)
    assert est.known is True
    assert est.pricing_version == "test"
    assert abs(est.input_cost - 0.001) < 1e-9
    assert abs(est.output_cost - 0.001) < 1e-9
    assert abs(est.total_cost - 0.002) < 1e-9


def test_unknown_model_is_marked_not_guessed(reset_pricing):
    register_model_prices("test", {})  # empty table: every model unknown
    est = estimate_cost("no-such-model", 1000, 500)
    assert est.known is False
    assert est.total_cost == 0.0


def test_default_table_versioned():
    assert PRICING_VERSION
