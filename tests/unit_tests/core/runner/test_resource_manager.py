# -*- coding: UTF-8 -*-
from unittest.mock import Mock
import pytest
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.resources_manager.resource_manager import ResourceMgr
from openjiuwen.core.single_agent import AgentCard


class TestResourceMgrAddMethodsValidation:
    @pytest.fixture
    def resource_mgr(self):
        return ResourceMgr()

    @pytest.fixture
    def mock_agent_card(self):
        card = AgentCard()
        card.id = "test_agent_1"
        card.name = "Test Agent"
        return card

    @pytest.fixture
    def mock_agent_provider(self):
        return Mock()

    @pytest.fixture
    def mock_tool(self):
        tool = Mock()
        tool.card = Mock()
        tool.card.id = "test_tool_1"
        tool.card.name = "Test Tool"
        return tool

    @pytest.fixture
    def mock_model_provider(self):
        return Mock()

    @staticmethod
    def assert_status_code(err, expect_error, expect_messsage):
        logger.info(err.value)
        assert err.value.code == expect_error.code
        assert expect_messsage in err.value.message

    @pytest.mark.asyncio
    async def test_add_agent_with_invalid_card_type(self, resource_mgr, mock_agent_provider):
        with pytest.raises(ValidationError) as err:
            resource_mgr.add_agent(None, mock_agent_provider)
        self.assert_status_code(
            err,
            StatusCode.RESOURCE_CARD_VALUE_INVALID,
            "agent card is invalid, reason='cannot be None, must be an instance of AgentCard'")

        with pytest.raises(ValidationError) as err:
            invalid_card = "not_a_card"
            resource_mgr.add_agent(invalid_card, mock_agent_provider)
        assert err.value.code == StatusCode.RESOURCE_CARD_VALUE_INVALID.code

        self.assert_status_code(
            err,
            StatusCode.RESOURCE_CARD_VALUE_INVALID,
            "agent card is invalid, reason='cannot be None, must be an instance of AgentCard'")

    @pytest.mark.asyncio
    async def test_add_agent_with_invalid_card_id(self, resource_mgr, mock_agent_provider):
        card = AgentCard()
        with pytest.raises(ValidationError) as err:
            card.id = ""
            resource_mgr.add_agent(card, mock_agent_provider)

        self.assert_status_code(
            err,
            StatusCode.RESOURCE_ID_VALUE_INVALID,
            "agent id is invalid, reason='cannot be empty or None'")

        with pytest.raises(ValidationError) as err:
            card.id = None
            resource_mgr.add_agent(card, mock_agent_provider)

        self.assert_status_code(
            err,
            StatusCode.RESOURCE_ID_VALUE_INVALID,
            "agent id is invalid, reason='cannot be empty or None'")
        with pytest.raises(ValidationError) as err:
            # 卡片ID为空格
            card.id = "   "
            resource_mgr.add_agent(card, mock_agent_provider)

        self.assert_status_code(
            err,
            StatusCode.RESOURCE_ID_VALUE_INVALID,
            "agent id is invalid, reason='string id cannot be empty or whitespace only")

    @pytest.mark.asyncio
    async def test_add_agent_with_invalid_provider(self, resource_mgr, mock_agent_card):
        with pytest.raises(ValidationError) as err:
            resource_mgr.add_agent(mock_agent_card, None)
        self.assert_status_code(
            err,
            StatusCode.RESOURCE_PROVIDER_INVALID,
            "agent provider is invalid, reason='provider cannot be None, must be a callable function'")
        with pytest.raises(ValidationError) as err:
            invalid_provider = "not_callable"
            resource_mgr.add_agent(mock_agent_card, invalid_provider)

        self.assert_status_code(
            err,
            StatusCode.RESOURCE_PROVIDER_INVALID,
            "agent provider is invalid, reason='invalid provider type, expected callable, got str'")

    @pytest.mark.asyncio
    async def test_add_agents_with_invalid_cards_and_providers(self, resource_mgr):
        with pytest.raises(ValidationError) as err:
            agents = [(None, Mock())]
            resource_mgr.add_agents(agents)
        self.assert_status_code(
            err,
            StatusCode.RESOURCE_PROVIDER_INVALID,
            "agent provider is invalid, reason='invalid card at idx 0: card cannot be None, "
            "must be an instance of AgentCard'")

        with pytest.raises(ValidationError) as err:
            card = Mock()
            card.id = "test_agent"
            agents = [(card, None)]
            resource_mgr.add_agents(agents)
        self.assert_status_code(
            err,
            StatusCode.RESOURCE_PROVIDER_INVALID,
            "agent provider is invalid, reason='invalid provider at idx 0: provider cannot be None, "
            "must be a callable function'")

        with pytest.raises(ValidationError) as err:
            agents = [("not_a_card", Mock())]
            resource_mgr.add_agents(agents)
        self.assert_status_code(
            err,
            StatusCode.RESOURCE_PROVIDER_INVALID,
            "agent provider is invalid, reason='invalid agent card type at idx 0: expected AgentCard, "
            "got str'")

    @pytest.mark.asyncio
    async def test_add_tool_with_invalid_tool(self, resource_mgr):
        with pytest.raises(ValidationError) as err:
            resource_mgr.add_tool(None)

        self.assert_status_code(
            err,
            StatusCode.RESOURCE_VALUE_INVALID,
            "tool value is invalid, reason='tool cannot be None: expected an instance or list of Tool'")

        with pytest.raises(ValidationError) as err:
            resource_mgr.add_tool("not_a_tool")
        self.assert_status_code(
            err,
            StatusCode.RESOURCE_VALUE_INVALID,
            "tool value is invalid, reason='invalid tool type: expected Tool, got str'")

        with pytest.raises(ValidationError) as err:
            resource_mgr.add_tool([])
        self.assert_status_code(
            err,
            StatusCode.RESOURCE_VALUE_INVALID,
            "tool value is invalid, reason='tool list cannot be empty: "
            "expected a non-empty list of Tool'")

        with pytest.raises(ValidationError) as err:
            mock_tool = Mock()
            mock_tool.card = Mock()
            mock_tool.card.id = "test_tool"
            resource_mgr.add_tool([mock_tool, None])
        self.assert_status_code(
            err,
            StatusCode.RESOURCE_VALUE_INVALID,
            "tool value is invalid, reason='invalid tool type at index 0: expected Tool, "
            "got Mock'")

    @pytest.mark.asyncio
    async def test_add_tool_with_invalid_tool_card(self, resource_mgr):
        with pytest.raises(ValidationError) as err:
            tool = Mock()
            tool.card = None
            resource_mgr.add_tool(tool)

        self.assert_status_code(
            err,
            StatusCode.RESOURCE_VALUE_INVALID,
            "tool value is invalid, reason='invalid tool type: expected Tool, got Mock'")

        with pytest.raises(ValidationError) as err:
            tool = Mock()
            tool.card = Mock()
            tool.card.id = ""
            resource_mgr.add_tool(tool)

        self.assert_status_code(
            err,
            StatusCode.RESOURCE_VALUE_INVALID,
            "tool value is invalid, reason='invalid tool type: expected Tool, got Mock'")
