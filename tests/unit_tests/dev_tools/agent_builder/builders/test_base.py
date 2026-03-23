# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, Dict, List, Union
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.core.common.exception.errors import ApplicationError
from openjiuwen.core.foundation.llm import Model
from openjiuwen.dev_tools.agent_builder.builders.base import BaseAgentBuilder
from openjiuwen.dev_tools.agent_builder.builders.factory import AgentBuilderFactory
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
from openjiuwen.dev_tools.agent_builder.utils.enums import AgentType, BuildState


class ConcreteAgentBuilder(BaseAgentBuilder):
    """Concrete implementation of BaseAgentBuilder for testing"""
    
    def _handle_initial(self, query: str, dialog_history: List[Dict[str, str]]) -> str:
        self.state = BuildState.PROCESSING
        return f"Initial: {query}"
    
    def _handle_processing(self, query: str, dialog_history: List[Dict[str, str]]) -> str:
        return f"Processing: {query}"
    
    def _handle_completed(self, query: str, dialog_history: List[Dict[str, str]]) -> Union[str, Dict[str, Any]]:
        return {"result": f"Completed: {query}"}
    
    def _reset_internal_state(self) -> None:
        pass
    
    def _is_workflow_builder(self) -> bool:
        return False


class TestBaseAgentBuilder:
    @pytest.fixture
    def mock_llm(self):
        return Mock(spec=Model)
    
    @pytest.fixture
    def history_manager(self):
        return HistoryManager()
    
    @pytest.fixture
    def builder(self, mock_llm, history_manager):
        return ConcreteAgentBuilder(mock_llm, history_manager)

    @staticmethod
    def test_builder_creation(mock_llm, history_manager):
        builder = ConcreteAgentBuilder(mock_llm, history_manager)
        assert builder.llm == mock_llm
        assert builder.history_manager == history_manager
        assert builder.state == BuildState.INITIAL
        assert builder.resource == {}

    @staticmethod
    def test_state_property(builder):
        assert builder.state == BuildState.INITIAL
        
        builder.state = BuildState.PROCESSING
        assert builder.state == BuildState.PROCESSING
        
        builder.state = BuildState.COMPLETED
        assert builder.state == BuildState.COMPLETED

    @staticmethod
    def test_resource_property(builder):
        assert builder.resource == {}
        
        builder.resource = {"plugins": [{"id": "1"}]}
        assert builder.resource == {"plugins": [{"id": "1"}]}

    @staticmethod
    def test_execute_initial_state(builder):
        result = builder.execute("test query")
        assert result == "Initial: test query"
        assert builder.state == BuildState.PROCESSING

    @staticmethod
    def test_execute_processing_state(builder):
        builder.state = BuildState.PROCESSING
        result = builder.execute("test query")
        assert result == "Processing: test query"

    @staticmethod
    def test_execute_completed_state(builder):
        builder.state = BuildState.COMPLETED
        result = builder.execute("test query")
        assert result == {"result": "Completed: test query"}

    @staticmethod
    def test_reset(builder):
        builder.state = BuildState.PROCESSING
        builder.resource = {"test": "value"}
        
        builder.reset()
        
        assert builder.state == BuildState.INITIAL
        assert builder.resource == {}

    @staticmethod
    def test_get_build_status(builder):
        builder.resource = {"plugins": [{"id": "1"}, {"id": "2"}]}
        
        status = builder.get_build_status()
        
        assert status["state"] == "initial"
        assert status["resource_count"]["plugins"] == 2


class TestAgentBuilderFactory:
    @staticmethod
    def test_create_llm_agent_builder():
        mock_llm = Mock(spec=Model)
        history_manager = HistoryManager()
        
        builder = AgentBuilderFactory.create(
            AgentType.LLM_AGENT,
            mock_llm,
            history_manager
        )
        
        assert builder is not None
        assert hasattr(builder, 'execute')

    @staticmethod
    def test_create_workflow_builder():
        mock_llm = Mock(spec=Model)
        history_manager = HistoryManager()
        
        builder = AgentBuilderFactory.create(
            AgentType.WORKFLOW,
            mock_llm,
            history_manager
        )
        
        assert builder is not None
        assert hasattr(builder, 'execute')

    @staticmethod
    def test_create_unsupported_type_raises_error():
        mock_llm = Mock(spec=Model)
        history_manager = HistoryManager()
        
        with pytest.raises(ValueError):
            AgentBuilderFactory.create(
                AgentType("unsupported_type"),
                mock_llm,
                history_manager
            )

    @staticmethod
    def test_get_supported_types():
        types = AgentBuilderFactory.get_supported_types()
        assert isinstance(types, list)
