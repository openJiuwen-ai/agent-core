# -*- coding: UTF-8 -*-
from unittest.mock import Mock
import pytest
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.multi_agent import TeamCard
from openjiuwen.core.runner.resources_manager.base import GLOBAL
from openjiuwen.core.runner.resources_manager.resource_manager import ResourceMgr
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.sys_operation import SysOperationCard, SysOperation
from openjiuwen.core.sys_operation.config import LocalWorkConfig
from openjiuwen.core.sys_operation.base import OperationMode


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
            # Card ID is whitespace only
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


class _SimpleTool(Tool):
    """Simple Tool implementation for testing"""

    async def invoke(self, inputs, **kwargs):
        return "ok"

    async def stream(self, inputs, **kwargs):
        yield "ok"


def _make_tool(tool_id: str, name: str = "") -> Tool:
    """Create a Tool instance for testing"""
    card = ToolCard(id=tool_id, name=name or tool_id, description=f"tool {tool_id}")
    return _SimpleTool(card)


class TestResourceMgrToolTagIsolation:
    """
    Test add_tool / get_tool tag isolation.
    Covers commit: fix(controller): use agent_id as tag to get tool
    """

    @pytest.fixture
    def resource_mgr(self):
        return ResourceMgr()

    @staticmethod
    def test_add_tool_with_tag_and_get_by_same_tag(resource_mgr):
        """After add_tool with tag, get_tool with the same tag should find it"""
        tool = _make_tool("tool_a")
        result = resource_mgr.add_tool(tool, tag="agent_1")
        assert result.is_ok()

        found = resource_mgr.get_tool(tool_id="tool_a", tag="agent_1")
        assert found is not None

    @staticmethod
    def test_get_tool_by_tag_only_returns_tagged_tools(resource_mgr):
        """Without tool_id, get_tool by tag only returns tools under that tag"""
        tool_a = _make_tool("tool_a1", "search")
        tool_b = _make_tool("tool_b1", "calculator")
        resource_mgr.add_tool(tool_a, tag="agent_1")
        resource_mgr.add_tool(tool_b, tag="agent_2")

        # Query with tag="agent_1" should only return tool_a1
        found_list = resource_mgr.get_tool(tag="agent_1")
        found_ids = [t.card.id for t in found_list if t]
        assert "tool_a1" in found_ids
        assert "tool_b1" not in found_ids

    @staticmethod
    def test_add_tool_without_tag_gets_global(resource_mgr):
        """add_tool without tag should assign the GLOBAL tag"""
        tool = _make_tool("tool_c")
        resource_mgr.add_tool(tool)

        assert resource_mgr.resource_has_tag("tool_c", GLOBAL)

    @staticmethod
    def test_add_tool_with_tag_does_not_get_global(resource_mgr):
        """add_tool with tag should not assign the GLOBAL tag"""
        tool = _make_tool("tool_d")
        resource_mgr.add_tool(tool, tag="agent_1")

        assert not resource_mgr.resource_has_tag("tool_d", GLOBAL)
        assert resource_mgr.resource_has_tag("tool_d", "agent_1")

    @staticmethod
    def test_two_agents_tools_isolated_by_tag(resource_mgr):
        """Tools registered by two agents are isolated via tag query"""
        tool_1 = _make_tool("tool_for_agent1", "search")
        tool_2 = _make_tool("tool_for_agent2", "search2")
        resource_mgr.add_tool(tool_1, tag="agent_1")
        resource_mgr.add_tool(tool_2, tag="agent_2")

        # agent_1 can only see its own tool
        agent1_tools = resource_mgr.get_tool(tag="agent_1")
        agent1_ids = [t.card.id for t in agent1_tools if t]
        assert "tool_for_agent1" in agent1_ids
        assert "tool_for_agent2" not in agent1_ids

        # agent_2 can only see its own tool
        agent2_tools = resource_mgr.get_tool(tag="agent_2")
        agent2_ids = [t.card.id for t in agent2_tools if t]
        assert "tool_for_agent2" in agent2_ids
        assert "tool_for_agent1" not in agent2_ids

    @pytest.mark.asyncio
    async def test_get_tool_infos_with_tag(self, resource_mgr):
        """get_tool_infos with tag filter only returns tool infos under that tag"""
        tool_1 = _make_tool("info_tool_1", "tool_one")
        tool_2 = _make_tool("info_tool_2", "tool_two")
        resource_mgr.add_tool(tool_1, tag="agent_x")
        resource_mgr.add_tool(tool_2, tag="agent_y")

        infos = await resource_mgr.get_tool_infos(tag="agent_x")
        names = [info.name for info in infos if info]
        assert "tool_one" in names
        assert "tool_two" not in names

    @pytest.mark.asyncio
    async def test_add_workflow_with_tag_and_get_by_same_tag(self, resource_mgr):
        """After add_workflow with tag, get_workflow with the same tag should find it"""
        card = WorkflowCard(id="wf_1", name="workflow_1")
        provider = Mock()
        resource_mgr.add_workflow(card, provider, tag="agent_1")

        found = await resource_mgr.get_workflow(workflow_id="wf_1", tag="agent_1")
        assert found is not None

    @pytest.mark.asyncio
    async def test_get_workflow_by_tag_only_returns_tagged_workflows(self, resource_mgr):
        """Without workflow_id, get_workflow by tag only returns workflows under that tag"""
        card_1 = WorkflowCard(id="wf_agent1", name="workflow_1")
        card_2 = WorkflowCard(id="wf_agent2", name="workflow_2")
        provider_1 = Mock()
        provider_2 = Mock()
        resource_mgr.add_workflow(card_1, provider_1, tag="agent_1")
        resource_mgr.add_workflow(card_2, provider_2, tag="agent_2")

        # Query with tag="agent_1" should only return wf_agent1
        found_list = await resource_mgr.get_workflow(tag="agent_1")
        assert len(found_list) == 1


class TestResourceMgrGetSysOpToolCards:
    """
    Test get_sys_op_tool_cards method.
    """

    @pytest.fixture
    def resource_mgr(self):
        return ResourceMgr()

    @pytest.fixture
    def sys_operation_card(self):
        card = SysOperationCard(
            id="test_sys_op",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(work_dir="/tmp/test")
        )
        return card

    @staticmethod
    def test_scenario1_single_tool_card(resource_mgr, sys_operation_card):
        """Scenario 1: Get a single tool card"""
        result = resource_mgr.add_sys_operation(sys_operation_card)
        assert result.is_ok()

        tool_card = resource_mgr.get_sys_op_tool_cards("test_sys_op", operation_name="fs", tool_name="read_file")

        assert tool_card is not None
        assert tool_card.name == "read_file"

    @staticmethod
    def test_scenario1_nonexistent_tool(resource_mgr, sys_operation_card):
        """Scenario 1: Get a single tool card that doesn't exist"""
        result = resource_mgr.add_sys_operation(sys_operation_card)
        assert result.is_ok()

        tool_card = resource_mgr.get_sys_op_tool_cards("test_sys_op", operation_name="fs", tool_name="nonexistent_tool")

        assert tool_card is None

    @staticmethod
    def test_scenario2_multiple_tool_cards(resource_mgr, sys_operation_card):
        """Scenario 2: Get multiple tool cards from the same operation"""
        result = resource_mgr.add_sys_operation(sys_operation_card)
        assert result.is_ok()

        tool_cards = resource_mgr.get_sys_op_tool_cards("test_sys_op", operation_name="fs",
                                                        tool_name=["read_file", "write_file"])

        assert tool_cards is not None
        assert len(tool_cards) == 2
        tool_names = [card.name for card in tool_cards]
        assert "read_file" in tool_names
        assert "write_file" in tool_names

    @staticmethod
    def test_scenario3_all_tool_cards_from_single_operation(resource_mgr, sys_operation_card):
        """Scenario 3: Get all tool cards from a single operation"""
        result = resource_mgr.add_sys_operation(sys_operation_card)
        assert result.is_ok()

        tool_cards = resource_mgr.get_sys_op_tool_cards("test_sys_op", operation_name="fs")

        assert tool_cards is not None
        assert len(tool_cards) > 0
        for card in tool_cards:
            assert card.name is not None

    @staticmethod
    def test_scenario4_all_tool_cards_from_multiple_operations(resource_mgr, sys_operation_card):
        """Scenario 4: Get all tool cards from multiple operations"""
        result = resource_mgr.add_sys_operation(sys_operation_card)
        assert result.is_ok()

        tool_cards = resource_mgr.get_sys_op_tool_cards("test_sys_op", operation_name=["fs", "shell"])

        assert tool_cards is not None
        assert len(tool_cards) > 0

    @staticmethod
    def test_scenario5_all_tool_cards_from_all_operations(resource_mgr, sys_operation_card):
        """Scenario 5: Get all tool cards from all operations"""
        result = resource_mgr.add_sys_operation(sys_operation_card)
        assert result.is_ok()

        tool_cards = resource_mgr.get_sys_op_tool_cards("test_sys_op")

        assert tool_cards is not None
        assert len(tool_cards) > 0

    @staticmethod
    def test_nonexistent_sys_operation(resource_mgr):
        """Test getting tool cards from a non-existent sys operation"""
        tool_cards = resource_mgr.get_sys_op_tool_cards("nonexistent_sys_op")

        assert tool_cards is None

    @staticmethod
    def test_error_operation_list_with_tool_name(resource_mgr, sys_operation_card):
        """Test that error is raised when operation_name is a list and tool_name is provided"""
        result = resource_mgr.add_sys_operation(sys_operation_card)
        assert result.is_ok()

        with pytest.raises(Exception) as err:
            resource_mgr.get_sys_op_tool_cards("test_sys_op", operation_name=["fs", "shell"], tool_name="read_file")

        assert "tool_name cannot be specified when operation_name is a list" in str(err.value)


class TestResourceMgrAgentGroupRemove:
    @pytest.fixture
    def resource_mgr(self):
        return ResourceMgr()

    @pytest.mark.asyncio
    async def test_remove_agent_group_returns_ok_with_removed_card(self, resource_mgr):
        group_card = TeamCard(id="test_group", name="test_group", description="test team")

        add_result = await resource_mgr.add_agent_team(group_card, lambda: Mock())
        assert add_result.is_ok()

        remove_result = await resource_mgr.remove_agent_team(team_id=group_card.id)

        assert remove_result.is_ok()
        assert remove_result.msg() == group_card

        removed_group = await resource_mgr.get_agent_team(team_id=group_card.id)
        assert removed_group is None
