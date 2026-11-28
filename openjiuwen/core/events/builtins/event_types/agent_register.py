# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``agent.register`` event and its real DeepAgent CORE handler."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, MatchPattern
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.harness.schema.config import SubAgentConfig

AGENT_REGISTER = "agent.register"


@dataclass(frozen=True, slots=True)
class AgentRegisterInput:
    """Declaration for one sub-agent type to bind to the current DeepAgent."""

    # Keep the legacy projection first so existing positional construction
    # remains valid.  New callers should prefer ``subagent_config``.
    agent_card: AgentCard | None = None
    prompt: str | None = None
    tools: tuple[str, ...] | None = None
    disallowed_tools: tuple[str, ...] | None = None
    model: str | None = None
    effort: str | int | None = None
    permission_mode: str | None = None
    mcp_servers: tuple[str | Mapping[str, object], ...] | None = None
    hooks: Mapping[str, object] | None = None
    max_turns: int | None = None
    auto_compact_window: int | None = None
    skills: tuple[str, ...] | None = None
    initial_prompt: str | None = None
    memory: Literal["user", "project", "local"] | None = None
    background: Literal[True] | None = None
    omit_claude_md: Literal[True] | None = None
    isolation: Literal["worktree", "remote"] | None = None
    subagent_config: "SubAgentConfig | None" = None

    @classmethod
    def from_subagent_config(cls, config: "SubAgentConfig") -> "AgentRegisterInput":
        """Build an event input from the canonical subagent declaration."""
        _validate_subagent_config(config)
        return cls(
            agent_card=config.agent_card,
            prompt=config.system_prompt,
            subagent_config=config,
        )


@dataclass(frozen=True, slots=True)
class AgentRegisterTriggerContext:
    """Internal CORE context used during DeepAgent initialization.

    Startup registration has a live ``DeepAgent`` but may not have a user
    session yet.  The context is accepted only by the CORE handler; public
    ``ops.agent.register`` continues to require a ``SessionContext``.
    """

    agent: "DeepAgent"


@dataclass(frozen=True, slots=True)
class AgentRegisterValue:
    """Successful agent registration result."""

    agent: str
    agent_card: AgentCard

    def __post_init__(self) -> None:
        if not isinstance(self.agent_card, AgentCard):
            raise TypeError("agent_card must be AgentCard")
        if not isinstance(self.agent, str) or not self.agent or self.agent != self.agent_card.name:
            raise ValueError("agent must match agent_card.name")


@dataclass(frozen=True, slots=True)
class AgentRegisterOutput:
    """CORE result; exactly one of ``value`` and ``deny`` must be present."""

    value: AgentRegisterValue | None = None
    deny: str | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.deny is None):
            raise ValueError("agent.register output requires exactly one of value or deny")
        if self.value is not None and not isinstance(self.value, AgentRegisterValue):
            raise TypeError("value must be AgentRegisterValue")
        if self.deny is not None and not isinstance(self.deny, str):
            raise TypeError("deny must be a string")

    def validate(self) -> None:
        """Validate the output after a handler has possibly rewritten it."""
        self.__post_init__()


def _validate_agent_card(card: object) -> AgentCard:
    if not isinstance(card, AgentCard):
        raise TypeError("agent.register requires AgentRegisterInput with AgentCard")
    if not isinstance(card.id, str) or not card.id:
        raise ValueError("agent card id must be a nonempty string")
    if not isinstance(card.name, str) or not card.name:
        raise ValueError("agent card name must be a nonempty string")
    return card


def _validate_subagent_config(config: object) -> "SubAgentConfig":
    from openjiuwen.harness.schema.config import SubAgentConfig

    if not isinstance(config, SubAgentConfig):
        raise TypeError("subagent_config must be SubAgentConfig")
    _validate_agent_card(config.agent_card)
    if not isinstance(config.system_prompt, str):
        raise TypeError("subagent_config.system_prompt must be a string")
    return config


def _copy_mutable(value: object) -> object:
    """Copy declaration containers while preserving runtime object handles."""
    if isinstance(value, Mapping):
        return {key: _copy_mutable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_mutable(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_mutable(item) for item in value)
    if isinstance(value, set):
        return {_copy_mutable(item) for item in value}
    return value


def _snapshot_subagent_config(config: "SubAgentConfig") -> "SubAgentConfig":
    """Snapshot mutable declaration state without cloning runtime handles."""
    from dataclasses import replace as replace_dataclass

    config = _validate_subagent_config(config)
    return replace_dataclass(
        config,
        agent_card=config.agent_card.model_copy(deep=True),
        tools=None if config.tools is None else list(config.tools),
        mcps=None if config.mcps is None else list(config.mcps),
        rails=None if config.rails is None else list(config.rails),
        skills=None if config.skills is None else list(config.skills),
        factory_kwargs=_copy_mutable(config.factory_kwargs),
    )


def prepare_agent_register_input(data: AgentRegisterInput) -> AgentRegisterInput:
    """Validate and snapshot an agent registration request."""
    if not isinstance(data, AgentRegisterInput):
        raise TypeError("agent.register requires AgentRegisterInput")

    config = data.subagent_config
    if config is not None:
        config = _snapshot_subagent_config(config)
        if data.agent_card is not None:
            card = _validate_agent_card(data.agent_card)
            if card != config.agent_card:
                raise ValueError("agent_card must match subagent_config.agent_card")
        if data.prompt is not None and data.prompt != config.system_prompt:
            raise ValueError("prompt must match subagent_config.system_prompt")
        card = config.agent_card
        prompt = config.system_prompt
    else:
        card = _validate_agent_card(data.agent_card)
        if not isinstance(data.prompt, str) or not data.prompt:
            raise ValueError("prompt must be a nonempty string")
        from openjiuwen.harness.schema.config import SubAgentConfig

        config = SubAgentConfig(agent_card=card, system_prompt=data.prompt)
        config = _snapshot_subagent_config(config)
        card = config.agent_card
        prompt = config.system_prompt

    for name in ("tools", "disallowed_tools", "skills"):
        value = getattr(data, name)
        if value is not None and (not isinstance(value, tuple) or any(not isinstance(item, str) for item in value)):
            raise TypeError(f"{name} must be a tuple of strings")
    for name in ("background", "omit_claude_md"):
        if getattr(data, name) is not None and getattr(data, name) is not True:
            raise ValueError(f"{name} must be True or None")
    if data.memory is not None and data.memory not in ("user", "project", "local"):
        raise ValueError("invalid memory")
    if data.isolation is not None and data.isolation not in ("worktree", "remote"):
        raise ValueError("invalid isolation")
    for name in ("model", "permission_mode", "initial_prompt"):
        value = getattr(data, name)
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
    if data.effort is not None and (isinstance(data.effort, bool) or not isinstance(data.effort, (str, int))):
        raise TypeError("effort must be a string or integer")
    for name in ("max_turns", "auto_compact_window"):
        value = getattr(data, name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be a positive integer")
        if value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if data.hooks is not None and not isinstance(data.hooks, Mapping):
        raise TypeError("hooks must be a mapping")
    if data.mcp_servers is not None and (
        not isinstance(data.mcp_servers, tuple)
        or any(not isinstance(item, (str, Mapping)) for item in data.mcp_servers)
    ):
        raise TypeError("mcp_servers must be a tuple of names or mappings")

    return replace(
        data,
        agent_card=card,
        prompt=prompt,
        subagent_config=config,
        hooks=None if data.hooks is None else copy.deepcopy(dict(data.hooks)),
        mcp_servers=None if data.mcp_servers is None else copy.deepcopy(data.mcp_servers),
    )


def _unsupported_fields(data: AgentRegisterInput) -> tuple[str, ...]:
    """List declaration fields not representable by the current SubAgentConfig."""
    unsupported: list[str] = []
    if data.tools:
        unsupported.append("tools")
    if data.disallowed_tools:
        unsupported.append("disallowed_tools")
    for name in (
        "model",
        "effort",
        "permission_mode",
        "mcp_servers",
        "hooks",
        "max_turns",
        "auto_compact_window",
        "skills",
        "initial_prompt",
        "memory",
        "background",
        "omit_claude_md",
        "isolation",
    ):
        value = getattr(data, name)
        if value not in (None, (), {}):
            unsupported.append(name)
    return tuple(unsupported)


def _require_session_context(ops: EventHandlerOps | None):
    if ops is None or ops.session_context is None or not ops.session_context.session_id:
        raise RuntimeError("agent.register requires a session")
    return ops.session_context


def _resolve_agent(ops: EventHandlerOps | None) -> "DeepAgent":
    """Resolve the live agent from internal startup or public session context."""
    trigger_context = getattr(ops, "trigger_context", None)

    from openjiuwen.harness.deep_agent import DeepAgent

    if isinstance(trigger_context, AgentRegisterTriggerContext):
        if not isinstance(trigger_context.agent, DeepAgent):
            raise RuntimeError("agent.register trigger context requires a bound DeepAgent")
        return trigger_context.agent

    session = _require_session_context(ops)
    from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime

    agent = get_agent_runtime().get(session.session_id)
    if agent is None:
        raise RuntimeError("session agent is not bound or has been released")
    if not isinstance(agent, DeepAgent):
        raise RuntimeError("agent.register requires a bound DeepAgent")
    return agent


def match_pattern_func(value: object, pattern: MatchPattern) -> bool:
    """Match valid agent registration requests with an empty pattern."""
    return isinstance(value, AgentRegisterInput) and not pattern


async def core_handler(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> AgentRegisterOutput:
    """Bind a real ``SubAgentConfig`` through the live ``DeepAgent`` binder."""
    del event_next
    agent = _resolve_agent(ops)
    data = prepare_agent_register_input(event.event_input)

    unsupported = _unsupported_fields(data)
    if unsupported:
        return AgentRegisterOutput(
            deny=("agent.register fields are not supported by the current DeepAgent runtime: " + ", ".join(unsupported))
        )

    if data.subagent_config is None:
        raise RuntimeError("agent.register input did not produce a SubAgentConfig")
    await agent.load_subagent_config(data.subagent_config)
    return AgentRegisterOutput(
        value=AgentRegisterValue(agent=data.agent_card.name, agent_card=data.agent_card),
    )


def _is_valid_output(value: object) -> bool:
    """Return whether a value satisfies the complete agent output contract."""
    if not isinstance(value, AgentRegisterOutput):
        return False
    try:
        value.validate()
    except (TypeError, ValueError):
        return False
    return True


def register(bus: ExtensionEventBus) -> None:
    """Register ``agent.register`` and its CORE handler on ``bus``."""
    bus.register_event_type(
        AGENT_REGISTER,
        match_pattern_func,
        core_handler,
        input_validator=lambda value: isinstance(value, AgentRegisterInput),
        output_validator=_is_valid_output,
    )
