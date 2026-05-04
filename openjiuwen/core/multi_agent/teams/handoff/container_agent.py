# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""ContainerAgent -- internal per-agent wrapper created by HandoffTeam."""
from __future__ import annotations
from typing import List
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import multi_agent_logger as logger
from openjiuwen.core.multi_agent.team_runtime.communicable_agent import CommunicableAgent
from openjiuwen.core.multi_agent.teams.handoff.interrupt import extract_interrupt_signal, flush_team_session
from openjiuwen.core.multi_agent.teams.handoff.handoff_orchestrator import HANDOFF_HISTORY_KEY
from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import extract_handoff_signal
from openjiuwen.core.multi_agent.teams.handoff.handoff_tool import HandoffTool
from openjiuwen.core.multi_agent.teams.handoff.handoff_request import HandoffRequest
from openjiuwen.core.single_agent.base import BaseAgent

_CONTEXT_HISTORY_KEY = "__handoff_ctx_history__"
_DEFAULT_CONTEXT_ID = "default_context_id"


class ContainerAgent(CommunicableAgent, BaseAgent):
    def __init__(self, target_card, target_provider, allowed_targets, coordinator_lookup=None):
        super().__init__(card=target_card)
        self._target_provider = target_provider
        self._allowed_targets = allowed_targets
        self._target_instance = None
        self._tools_injected = False
        self._coordinator_lookup = coordinator_lookup

    def _get_target_agent(self):
        if self._target_instance is None:
            self._target_instance = self._target_provider()
        return self._target_instance

    def _inject_tools_once(self, target_agent):
        if self._tools_injected:
            return
        self._tools_injected = True
        ability_mgr = getattr(target_agent, "ability_manager", None)
        if ability_mgr is None:
            logger.debug(f"[{self.__class__.__name__}:{self.card.id}] "
                         f"{target_agent.card.id!r} has no ability_manager, skipping")
            return
        from openjiuwen.core.runner import Runner
        for target_id in sorted(self._allowed_targets):
            card = self._runtime.get_agent_card(target_id) if self._runtime else None
            description = card.description if card else ""
            tool = HandoffTool(target_id=target_id, target_description=description)
            ability_mgr.add(tool.card)
            # _tools_injected flag guarantees this block runs exactly once per
            # ContainerAgent instance, so duplicate-registration checks are unnecessary.
            Runner.resource_mgr.add_tool(tool, tag=target_agent.card.id)
            logger.debug(f"[{self.__class__.__name__}:"
                         f"{self.card.id}] injected '{tool.card.name}' -> '{target_agent.card.id}'")

    def _build_agent_input(self, inputs):
        msg = inputs.input_message
        if not inputs.history:
            return msg
        if isinstance(msg, dict):
            return {**msg, "handoff_history": inputs.history}
        return {"query": msg, "handoff_history": inputs.history}

    @staticmethod
    def _strip_handoff_messages(messages: List) -> List:
        from openjiuwen.core.foundation.llm import AssistantMessage

        cleaned = []
        for msg in messages:
            role = getattr(msg, "role", "")
            if role == "tool":
                continue
            if isinstance(msg, AssistantMessage):
                tcs = getattr(msg, "tool_calls", None) or []
                if tcs:
                    continue
            cleaned.append(msg)
        return cleaned

    def _save_context_to_team_session(
        self, agent_session, team_session
    ) -> None:
        if agent_session is None or team_session is None:
            return
        ctx_state = agent_session.get_state("context")
        if not ctx_state or not isinstance(ctx_state, dict):
            return
        new_messages = ctx_state.get(_DEFAULT_CONTEXT_ID, {}).get("messages", [])
        if not new_messages:
            return
        cleaned = self._strip_handoff_messages(new_messages)
        if not cleaned:
            return
        existing: List = team_session.get_state(_CONTEXT_HISTORY_KEY) or []

        def _msg_key(m):
            return (
                getattr(m, "role", ""),
                str(getattr(m, "content", "")),
                str(getattr(m, "tool_calls", "")),
                getattr(m, "tool_call_id", ""),
            )
        existing_keys = {_msg_key(m) for m in existing}
        to_append = [m for m in cleaned if _msg_key(m) not in existing_keys]
        if to_append:
            team_session.update_state({_CONTEXT_HISTORY_KEY: existing + to_append})
            logger.debug(
                f"[{self.__class__.__name__}:{self.card.id}] saved {len(to_append)} messages "
                f"to team context history (total={len(existing) + len(to_append)})"
            )

    def _inject_context_history(
        self, agent_session, team_session
    ) -> None:
        if agent_session is None or team_session is None:
            return
        history_messages = team_session.get_state(_CONTEXT_HISTORY_KEY)
        if not history_messages:
            return
        agent_session.update_state({
            "context": {
                _DEFAULT_CONTEXT_ID: {
                    "messages": list(history_messages),
                    "offload_messages": {},
                }
            }
        })
        logger.debug(
            f"[{self.__class__.__name__}:{self.card.id}] injected {len(history_messages)} "
            f"history messages into agent_session for {self.card.id!r}"
        )

    def configure(self, config):
        return self

    async def _invoke_target_with_stream(self, target_agent, agent_input, team_session):
        """Invoke the target agent and relay its result to *team_session*.

        Uses ``invoke()`` instead of ``stream()`` to avoid the timeout risk that arises
        when ``write_stream`` calls block inside an async iteration loop.  The relay
        still reaches listeners via ``team_session.write_stream`` for both single-dict
        and multi-item (list) results, so callers that stream the team session see
        output without functional change.
        """
        agent_session = team_session.create_agent_session(card=target_agent.card)
        self._inject_context_history(agent_session, team_session)
        result = await target_agent.invoke(inputs=agent_input, session=agent_session)
        if isinstance(result, dict):
            await team_session.write_stream(result)
        elif isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    await team_session.write_stream(item)
        await self._save_agent_context(target_agent, agent_session)
        self._save_context_to_team_session(agent_session, team_session)
        signal = extract_handoff_signal(result, agent_session)
        return result, signal

    async def _save_agent_context(self, target_agent, agent_session):
        """Persist agent context to session state before reading it for handoff detection."""
        try:
            context_engine = getattr(target_agent, "context_engine", None)
            if context_engine is not None:
                await context_engine.save_contexts(agent_session)
        except Exception as exc:
            logger.warning(
                f"[{self.__class__.__name__}:{self.card.id}] "
                f"failed to save agent context for {target_agent.card.id!r}: {exc}",
                exc_info=False,
            )

    async def stream(self, inputs, session=None, **kwargs):
        result = await self.invoke(inputs=inputs, session=session)
        yield result

    async def invoke(self, inputs, session=None):
        if not isinstance(inputs, HandoffRequest):
            return {}
        session_id = inputs.session_id
        coordinator = self._coordinator_lookup(session_id) if self._coordinator_lookup is not None else None
        if coordinator is None:
            if not session_id:
                error_msg = "ContainerAgent invoked without a HandoffTeam session (session_id is empty)"
            else:
                error_msg = (
                    f"coordinator not found for session_id={session_id!r}; "
                    "session may have already ended or never been registered"
                )
            logger.error(
                f"[{self.__class__.__name__}:{self.card.id}] {error_msg}",
                exc_info=False,
            )
            raise build_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg=error_msg,
            )
        history = list(inputs.history)
        team_session = inputs.session
        interrupt_signal = None
        signal = None
        try:
            target_agent = self._get_target_agent()
            self._inject_tools_once(target_agent)
            agent_input = self._build_agent_input(inputs)
            provider_type = type(self._target_provider).__name__
            logger.info(
                f"[{self.__class__.__name__}:{self.card.id}] invoking "
                f"session_id={session_id!r} resolved_agent={target_agent.card.id!r} "
                f"provider_type={provider_type} hop={len(history)} "
                f"streaming={team_session is not None}"
            )
            if team_session is not None:
                result, signal = await self._invoke_target_with_stream(target_agent, agent_input, team_session)
            else:
                from openjiuwen.core.session.agent import create_agent_session as _create_agent_session
                agent_session = _create_agent_session(
                    session_id=session_id or None,
                    card=target_agent.card,
                )
                result = await target_agent.invoke(inputs=agent_input, session=agent_session)
                signal = extract_handoff_signal(result, agent_session)
            history.append({"agent": target_agent.card.id, "output": result})
            interrupt_signal = extract_interrupt_signal(result=result)
        except Exception as exc:
            interrupt_signal = extract_interrupt_signal(exc=exc)
            if interrupt_signal is None:
                error_msg = f"agent execution error in {self.card.id!r}: {exc}"
                logger.error(
                    f"[{self.__class__.__name__}:{self.card.id}] {error_msg}",
                    exc_info=True,
                )
                structured_exc = build_error(
                    StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                    error_msg=error_msg,
                )
                await coordinator.error(structured_exc)
                return {}
        if interrupt_signal is not None:
            await self._handle_team_interrupt(signal=interrupt_signal,
                                              coordinator=coordinator,
                                              history=history,
                                              inputs=inputs)
            return {}
        if signal is None:
            logger.info(
                f"[{self.__class__.__name__}:{self.card.id}] completing session_id={session_id!r} "
                f"resolved_agent={target_agent.card.id!r}"
            )
            await coordinator.complete(result)
        else:
            allowed = await coordinator.request_handoff(target_id=signal.target, reason=signal.reason)
            if allowed:
                logger.info(
                    f"[{self.__class__.__name__}:{self.card.id}] handoff approved "
                    f"session_id={session_id!r} from={target_agent.card.id!r} to={signal.target!r}"
                )
                next_input = signal.message or inputs.input_message
                await self.publish(
                    message=HandoffRequest(
                        input_message=next_input,
                        history=history,
                        session=inputs.session,
                    ),
                    topic_id=f"container_{signal.target}",
                    session_id=session_id,
                )
            else:
                logger.info(
                    f"[{self.__class__.__name__}:{self.card.id}] handoff blocked "
                    f"session_id={session_id!r} target={signal.target!r} completing with current result"
                )
                await coordinator.complete(result)
        return {}

    async def _handle_team_interrupt(self, signal, coordinator, history, inputs):
        if inputs.session is not None:
            coordinator.save_to_session(inputs.session)
            inputs.session.update_state({HANDOFF_HISTORY_KEY: history})
            await flush_team_session(inputs.session)
        await coordinator.complete(signal.result)
