# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from a2a.types import AgentCard as A2AAgentCard

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.extensions.a2a.a2a_agentcard_adapter import A2AAgentCardAdapter


class TestA2AAgentCardAdapter:
    def test_to_a2a_agent_card_should_map_basic_fields_and_default_modes(self):
        card = AgentCard(
            id="1234567890abcdef1234567890abcdef",
            name="示例智能体",
            description="这是一个示例智能体",
            input_params={"age": 25},
            output_params={"greeting": "hello"},
        )

        result = A2AAgentCardAdapter.to_a2a_agent_card(card)

        assert result.name == "示例智能体"
        assert result.description.startswith("这是一个示例智能体")
        assert "[input_params]" in result.description
        assert "[output_params]" in result.description
        assert list(result.default_input_modes) == ["text/plain", "application/json"]
        assert list(result.default_output_modes) == ["text/plain", "application/json"]
        # id has no mapping in A2A AgentCard.
        assert "1234567890abcdef1234567890abcdef" not in result.description

    def test_to_a2a_agent_card_should_fill_supported_interfaces_from_config(self):
        card = AgentCard(name="demo", description="desc")

        result = A2AAgentCardAdapter.to_a2a_agent_card(
            card,
            supported_interfaces=[
                {
                    "url": "https://rest.example.com/v1",
                    "protocol_binding": "HTTP+JSON",
                    "protocol_version": "1.0",
                    "tenant": "testtenant",
                }
            ],
        )

        assert len(result.supported_interfaces) == 1
        interface = result.supported_interfaces[0]
        assert interface.url == "https://rest.example.com/v1"
        assert interface.protocol_binding == "HTTP+JSON"
        assert interface.protocol_version == "1.0"
        assert interface.tenant == "testtenant"

    def test_to_a2a_agent_card_should_fallback_to_single_interface_args(self):
        card = AgentCard(name="demo", description="desc")

        result = A2AAgentCardAdapter.to_a2a_agent_card(
            card,
            interface_url="https://grpc.example.com/a2a",
            protocol_binding="GRPC",
            protocol_version="1.0",
            tenant="tenant-a",
        )

        assert len(result.supported_interfaces) == 1
        interface = result.supported_interfaces[0]
        assert interface.url == "https://grpc.example.com/a2a"
        assert interface.protocol_binding == "GRPC"
        assert interface.protocol_version == "1.0"
        assert interface.tenant == "tenant-a"

    def test_from_a2a_agent_card_should_map_name_and_description(self):
        a2a_card = A2AAgentCard(
            name="Recipe Agent",
            description="Agent that helps users with recipes and cooking.",
        )

        result = A2AAgentCardAdapter.from_a2a_agent_card(a2a_card)

        assert result.id
        assert result.name == "Recipe Agent"
        assert result.description == "Agent that helps users with recipes and cooking."
