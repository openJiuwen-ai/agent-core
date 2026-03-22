# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.base import BaseAgentBuilder
from openjiuwen.dev_tools.agent_builder.builders.factory import AgentBuilderFactory
from openjiuwen.dev_tools.agent_builder.utils.enums import AgentType


class _StubLlmBuilder(BaseAgentBuilder):
    """Minimal stub for registry tests that need only one agent type registered."""

    def _handle_initial(self, query, dialog_history):
        return ""

    def _handle_processing(self, query, dialog_history):
        return ""

    def _handle_completed(self, query, dialog_history):
        return ""

    def _reset_internal_state(self) -> None:
        pass

    def _is_workflow_builder(self) -> bool:
        return False


class TestAgentBuilderFactoryCreate:
    @pytest.fixture(autouse=True)
    def setup(self):
        AgentBuilderFactory.clear_registry()
        yield
        AgentBuilderFactory.clear_registry()

    @staticmethod
    def test_create_llm_agent_builder():
        mock_llm = Mock()
        mock_history_manager = Mock()

        builder = AgentBuilderFactory.create(
            agent_type=AgentType.LLM_AGENT,
            llm=mock_llm,
            history_manager=mock_history_manager,
        )

        assert builder is not None
        assert isinstance(builder, BaseAgentBuilder)

    @staticmethod
    def test_create_workflow_builder():
        mock_llm = Mock()
        mock_history_manager = Mock()

        builder = AgentBuilderFactory.create(
            agent_type=AgentType.WORKFLOW,
            llm=mock_llm,
            history_manager=mock_history_manager,
        )

        assert builder is not None
        assert isinstance(builder, BaseAgentBuilder)

    @staticmethod
    def test_create_unsupported_agent_type():
        mock_llm = Mock()
        mock_history_manager = Mock()

        AgentBuilderFactory.clear_registry()
        AgentBuilderFactory.register(AgentType.LLM_AGENT, _StubLlmBuilder)

        with pytest.raises(ValueError) as exc_info:
            AgentBuilderFactory.create(
                agent_type=AgentType.WORKFLOW,
                llm=mock_llm,
                history_manager=mock_history_manager,
            )

        assert "Unsupported agent type" in str(exc_info.value)

    @staticmethod
    def test_create_initializes_builders_dict():
        AgentBuilderFactory.clear_registry()
        mock_llm = Mock()
        mock_history_manager = Mock()

        AgentBuilderFactory.create(
            agent_type=AgentType.LLM_AGENT,
            llm=mock_llm,
            history_manager=mock_history_manager,
        )

        registered = AgentBuilderFactory.get_registered_builders()
        assert AgentType.LLM_AGENT in registered
        assert AgentType.WORKFLOW in registered

    @staticmethod
    def test_create_reuses_existing_builders_dict():
        AgentBuilderFactory.clear_registry()
        mock_llm = Mock()
        mock_history_manager = Mock()

        AgentBuilderFactory.create(
            agent_type=AgentType.LLM_AGENT,
            llm=mock_llm,
            history_manager=mock_history_manager,
        )

        snapshot_after_first = AgentBuilderFactory.get_registered_builders()

        AgentBuilderFactory.create(
            agent_type=AgentType.WORKFLOW,
            llm=mock_llm,
            history_manager=mock_history_manager,
        )

        snapshot_after_second = AgentBuilderFactory.get_registered_builders()
        assert snapshot_after_first == snapshot_after_second


class TestAgentBuilderFactoryRegister:
    @pytest.fixture(autouse=True)
    def setup(self):
        AgentBuilderFactory.clear_registry()
        yield
        AgentBuilderFactory.clear_registry()

    @staticmethod
    def test_register_valid_builder():
        class ValidBuilder(BaseAgentBuilder):
            def execute(self):
                pass

            def get_build_status(self):
                return {}

        AgentBuilderFactory.register(AgentType.LLM_AGENT, ValidBuilder)

        registered = AgentBuilderFactory.get_registered_builders()
        assert AgentType.LLM_AGENT in registered
        assert registered[AgentType.LLM_AGENT] is ValidBuilder

    @staticmethod
    def test_register_invalid_builder_raises_type_error():
        class InvalidBuilder:
            pass

        with pytest.raises(TypeError) as exc_info:
            AgentBuilderFactory.register(AgentType.LLM_AGENT, InvalidBuilder)

        assert "must inherit from BaseAgentBuilder" in str(exc_info.value)

    @staticmethod
    def test_register_overwrites_existing_builder():
        class FirstBuilder(BaseAgentBuilder):
            def execute(self):
                pass

            def get_build_status(self):
                return {}

        class SecondBuilder(BaseAgentBuilder):
            def execute(self):
                pass

            def get_build_status(self):
                return {}

        AgentBuilderFactory.register(AgentType.LLM_AGENT, FirstBuilder)
        AgentBuilderFactory.register(AgentType.LLM_AGENT, SecondBuilder)

        assert AgentBuilderFactory.get_registered_builders()[AgentType.LLM_AGENT] is SecondBuilder

    @staticmethod
    def test_register_custom_agent_type():
        class CustomBuilder(BaseAgentBuilder):
            def execute(self):
                pass

            def get_build_status(self):
                return {}

        custom_type = AgentType.LLM_AGENT
        AgentBuilderFactory.register(custom_type, CustomBuilder)

        assert custom_type in AgentBuilderFactory.get_registered_builders()


class TestAgentBuilderFactoryGetSupportedTypes:
    @pytest.fixture(autouse=True)
    def setup(self):
        AgentBuilderFactory.clear_registry()
        yield
        AgentBuilderFactory.clear_registry()

    @staticmethod
    def test_get_supported_types_empty():
        types = AgentBuilderFactory.get_supported_types()

        assert types == []

    @staticmethod
    def test_get_supported_types_after_create():
        mock_llm = Mock()
        mock_history_manager = Mock()

        AgentBuilderFactory.create(
            agent_type=AgentType.LLM_AGENT,
            llm=mock_llm,
            history_manager=mock_history_manager,
        )

        types = AgentBuilderFactory.get_supported_types()

        assert AgentType.LLM_AGENT in types
        assert AgentType.WORKFLOW in types
        assert len(types) == 2

    @staticmethod
    def test_get_supported_types_after_register():
        class CustomBuilder(BaseAgentBuilder):
            def execute(self):
                pass

            def get_build_status(self):
                return {}

        AgentBuilderFactory.register(AgentType.LLM_AGENT, CustomBuilder)

        types = AgentBuilderFactory.get_supported_types()

        assert AgentType.LLM_AGENT in types
        assert len(types) == 1

    @staticmethod
    def test_get_supported_types_returns_copy():
        class CustomBuilder(BaseAgentBuilder):
            def execute(self):
                pass

            def get_build_status(self):
                return {}

        AgentBuilderFactory.register(AgentType.LLM_AGENT, CustomBuilder)

        types1 = AgentBuilderFactory.get_supported_types()
        types2 = AgentBuilderFactory.get_supported_types()

        assert types1 is not types2
        assert types1 == types2


class TestAgentBuilderFactoryIntegration:
    @pytest.fixture(autouse=True)
    def setup(self):
        AgentBuilderFactory.clear_registry()
        yield
        AgentBuilderFactory.clear_registry()

    @staticmethod
    def test_register_then_create():
        class CustomBuilder(BaseAgentBuilder):
            def __init__(self, llm, history_manager):
                super().__init__(llm, history_manager)
                self.custom_initialized = True

            def execute(self, query):
                return "custom result"

            def get_build_status(self):
                return {"state": "custom"}

            def _handle_initial(self, query, dialog_history):
                return "initial"

            def _handle_processing(self, query, dialog_history):
                return "processing"

            def _handle_completed(self, query, dialog_history):
                return "completed"

            def _reset_internal_state(self):
                pass

            def _is_workflow_builder(self):
                return False

        AgentBuilderFactory.register(AgentType.LLM_AGENT, CustomBuilder)

        mock_llm = Mock()
        mock_history_manager = Mock()

        builder = AgentBuilderFactory.create(
            agent_type=AgentType.LLM_AGENT,
            llm=mock_llm,
            history_manager=mock_history_manager,
        )

        assert isinstance(builder, CustomBuilder)
        assert builder.custom_initialized is True

    @staticmethod
    def test_multiple_registrations():
        class Builder1(BaseAgentBuilder):
            def execute(self):
                pass

            def get_build_status(self):
                return {}

        class Builder2(BaseAgentBuilder):
            def execute(self):
                pass

            def get_build_status(self):
                return {}

        AgentBuilderFactory.register(AgentType.LLM_AGENT, Builder1)
        AgentBuilderFactory.register(AgentType.WORKFLOW, Builder2)

        registered = AgentBuilderFactory.get_registered_builders()
        assert len(registered) == 2
        assert registered[AgentType.LLM_AGENT] is Builder1
        assert registered[AgentType.WORKFLOW] is Builder2
