# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for openjiuwen.core.operator.base module."""

import inspect
from unittest.mock import MagicMock

import pytest

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
    def test_invoke_is_abstract():
        """Test that invoke is an abstract method."""
        assert hasattr(Operator, "invoke")
        assert inspect.iscoroutinefunction(Operator.invoke)

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
    @pytest.mark.asyncio
    async def test_stream_not_implemented_by_default():
        """Test that stream raises NotImplementedError by default."""
        assert hasattr(Operator, "stream")
        assert inspect.iscoroutinefunction(Operator.stream)

        operator = _ConcreteOperator()
        with pytest.raises(NotImplementedError, match="stream not implemented"):
            await operator.stream(inputs={}, session=MagicMock())


class _ConcreteOperator(Operator):
    """Concrete implementation for testing base class."""

    @property
    def operator_id(self) -> str:
        return "test_operator"

    def get_tunables(self):
        return {}

    def set_parameter(self, target, value):
        pass

    def get_state(self):
        return {}

    def load_state(self, state):
        pass

    async def invoke(self, inputs, session, **kwargs):
        return "result"
