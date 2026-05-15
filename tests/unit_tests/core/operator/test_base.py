# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for openjiuwen.core.operator.base module."""

import inspect

import pytest

from openjiuwen.agent_evolving.types import UpdateValue
from openjiuwen.core.operator.base import Operator, TunableSpec


class TestTunableSpec:
    """Tests for TunableSpec class."""

    @staticmethod
    def test_init_with_all_params():
        """Test initialization with all parameters."""
        spec = TunableSpec(
            name="temperature",
            kind="continuous",
            path="model.temperature",
            constraint={"min": 0.0, "max": 1.0},
        )
        assert spec.name == "temperature"
        assert spec.kind == "continuous"
        assert spec.path == "model.temperature"
        assert spec.constraint == {"min": 0.0, "max": 1.0}

    @staticmethod
    def test_init_with_minimal_params():
        """Test initialization with minimal parameters."""
        spec = TunableSpec(
            name="prompt",
            kind="prompt",
            path="prompt",
        )
        assert spec.name == "prompt"
        assert spec.kind == "prompt"
        assert spec.path == "prompt"
        assert spec.constraint is None

    @staticmethod
    def test_slots_restriction():
        """Test that __slots__ restricts attribute assignment."""
        spec = TunableSpec(name="test", kind="discrete", path="test")
        with pytest.raises(AttributeError):
            spec.new_attr = "value"


class TestOperator:
    """Tests for Operator abstract base class."""

    @staticmethod
    def test_operator_id_property_is_abstract():
        """Test that operator_id is an abstract property."""
        assert hasattr(Operator, "operator_id")
        # The property should be abstract
        assert getattr(Operator.operator_id, "__isabstractmethod__", False)

    @staticmethod
    def test_get_tunables_is_abstract():
        """Test that get_tunables is an abstract method."""
        assert hasattr(Operator, "get_tunables")
        assert inspect.isfunction(Operator.get_tunables)

    @staticmethod
    def test_set_parameter_is_abstract():
        """Test that set_parameter is an abstract method."""
        assert hasattr(Operator, "set_parameter")
        assert inspect.isfunction(Operator.set_parameter)

    @staticmethod
    def test_get_state_is_abstract():
        """Test that get_state is an abstract method."""
        assert hasattr(Operator, "get_state")
        assert inspect.isfunction(Operator.get_state)

    @staticmethod
    def test_load_state_is_abstract():
        """Test that load_state is an abstract method."""
        assert hasattr(Operator, "load_state")
        assert inspect.isfunction(Operator.load_state)

    @staticmethod
    def test_apply_update_uses_default_compatibility_path():
        """Test apply_update delegates replace/state updates to set_parameter."""
        operator = _ConcreteOperator()

        result = operator.apply_update("system_prompt", UpdateValue(payload="new prompt"))

        assert operator.set_parameter_calls == [("system_prompt", "new prompt")]
        assert result.operator_id == "test_operator"
        assert result.target == "system_prompt"
        assert result.applied is True
        assert result.value == "new prompt"

    @staticmethod
    def test_apply_update_reports_noop_when_state_does_not_change():
        """Test compatibility path reports no-op when set_parameter changes nothing."""
        operator = _ConcreteOperator()

        result = operator.apply_update("unknown_target", UpdateValue(payload="new prompt"))

        assert operator.set_parameter_calls == [("unknown_target", "new prompt")]
        assert result.applied is False

    @staticmethod
    def test_apply_update_rejects_non_replace_update_in_default_path():
        """Test base compatibility path does not accept non-replace updates."""
        operator = _ConcreteOperator()

        result = operator.apply_update(
            "system_prompt",
            UpdateValue(payload="delta", mode="append"),
        )

        assert operator.set_parameter_calls == []
        assert result.applied is False
        assert result.errors == ["unsupported update mode/effect for compatibility operator: append/state"]

    @staticmethod
    def test_operator_is_not_executable():
        """Test that Operator no longer has invoke/stream methods.

        Per design v1.1, Operator is a parameter handle for self-evolution,
        not an executable unit. Execution is handled by the consumer.
        """
        assert not hasattr(Operator, "invoke")
        assert not hasattr(Operator, "stream")


class _ConcreteOperator(Operator):
    """Concrete implementation for testing base class."""

    def __init__(self):
        self.set_parameter_calls = []
        self._state = {}

    @property
    def operator_id(self) -> str:
        return "test_operator"

    def get_tunables(self):
        return {}

    def set_parameter(self, target, value):
        self.set_parameter_calls.append((target, value))
        if target == "system_prompt":
            self._state[target] = value

    def get_state(self):
        return dict(self._state)

    def load_state(self, state):
        pass
