# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from typing import Any, Dict, Iterable, Optional

from a2a.types import AgentCard as A2AAgentCard
from a2a.types import AgentInterface

from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class A2AAgentCardAdapter:
    """Adapter between openjiuwen AgentCard and A2A AgentCard."""

    DEFAULT_INPUT_MODES = ["text/plain", "application/json"]
    DEFAULT_OUTPUT_MODES = ["text/plain", "application/json"]

    @classmethod
    def to_a2a_agent_card(
            cls,
            agent_card: AgentCard,
            *,
            interface_url: Optional[str] = None,
            protocol_binding: str = "HTTP+JSON",
            protocol_version: str = "1.0",
            tenant: Optional[str] = None,
            supported_interfaces: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Optional[A2AAgentCard]:
        # Keep only minimal validation: input must be OJW AgentCard
        if not isinstance(agent_card, AgentCard):
            return None

        description = cls._build_description(
            base_description=agent_card.description,
            input_params=agent_card.input_params,
            output_params=agent_card.output_params,
        )

        card = A2AAgentCard(
            name=agent_card.name or "",
            description=description,
            default_input_modes=cls.DEFAULT_INPUT_MODES,
            default_output_modes=cls.DEFAULT_OUTPUT_MODES,
        )

        interfaces = cls._build_interfaces(
            interface_url=interface_url,
            protocol_binding=protocol_binding,
            protocol_version=protocol_version,
            tenant=tenant,
            supported_interfaces=supported_interfaces,
        )
        if interfaces:
            card.supported_interfaces.extend(interfaces)
        return card

    @staticmethod
    def from_a2a_agent_card(a2a_agent_card: A2AAgentCard) -> AgentCard:
        return AgentCard(
            name=a2a_agent_card.name,
            description=a2a_agent_card.description,
            # openjiuwen AgentCard requires id; A2A has no id so keep generated default.
        )

    @staticmethod
    def _serialize_param_payload(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            payload = value
        else:
            # Keep compatibility with BaseModel type-style declarations.
            model_json_schema = getattr(value, "model_json_schema", None)
            if callable(model_json_schema):
                payload = model_json_schema()
            elif isinstance(value, type):
                payload = {"type": value.__name__}
            else:
                payload = {"value": str(value)}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @classmethod
    def _build_description(
            cls,
            *,
            base_description: str,
            input_params: Any,
            output_params: Any,
    ) -> str:
        sections = [base_description.strip() if base_description else ""]
        input_text = cls._serialize_param_payload(input_params)
        output_text = cls._serialize_param_payload(output_params)
        if input_text:
            sections.append(f"[input_params] {input_text}")
        if output_text:
            sections.append(f"[output_params] {output_text}")
        return "\n".join(part for part in sections if part).strip()

    @staticmethod
    def _build_interfaces(
            *,
            interface_url: Optional[str],
            protocol_binding: str,
            protocol_version: str,
            tenant: Optional[str],
            supported_interfaces: Optional[Iterable[Dict[str, Any]]],
    ) -> list[AgentInterface]:
        result: list[AgentInterface] = []
        if supported_interfaces:
            for item in supported_interfaces:
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                binding = item.get("protocol_binding")
                version = item.get("protocol_version")
                if not url or not binding or not version:
                    continue
                interface = AgentInterface(
                    url=url,
                    protocol_binding=str(binding),
                    protocol_version=str(version),
                )
                if item.get("tenant"):
                    interface.tenant = str(item["tenant"])
                result.append(interface)
            if result:
                return result

        if interface_url:
            interface = AgentInterface(
                url=interface_url,
                protocol_binding=protocol_binding,
                protocol_version=protocol_version,
            )
            if tenant:
                interface.tenant = tenant
            result.append(interface)
        return result
