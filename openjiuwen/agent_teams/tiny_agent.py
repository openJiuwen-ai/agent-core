# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tiny agent: an on-demand, minimal native harness for lightweight LLM tasks.

A tiny agent is the smallest useful agent in ``agent_teams``: a ``NativeHarness``
built from a bare ``DeepAgentSpec`` carrying only a system prompt + model, with
**no tools except the optional structured-output tool** (no workspace,
sys_operation, skills, subagents, or team-collaboration tools). It is meant to be
woken up at any point in a team's life to run a one-shot task (title/summary
generation) or a short multi-turn conversation, then disposed.

Two interaction modes — single-shot is NOT a special case of multi-turn, they use
different harness lifecycles deliberately:

- ``run(content, schema=...)``: stateless single-shot. A fresh harness per call
  (so concurrent calls never collide), executed via ``NativeHarness.run_once``
  (no supervisor, no steering), then disposed. Mirrors the swarmflow worker path.
- ``chat(content, schema=...)``: stateful multi-turn. One persistent harness
  started once; each turn is one ``send`` awaited back to IDLE. Mirrors the
  swarmflow avatar-session path. Call ``aclose`` (or use ``async with``) to
  dispose it.

Structured output reuses the shared ``StructuredOutputTool`` /
``StructuredOutputFinishRail`` — the harness has no native ``response_format``,
so a schema turn mounts the tool, instructs the model to finish by calling it,
and reads the captured arguments back.

Lifecycle ownership is orthogonal to the agent itself:

- **ephemeral**: the caller owns the instance and disposes it (``async with``).
  Two presets ship for the common one-shot tasks — :func:`create_title_agent` /
  :func:`create_summary_agent` and the one-call :func:`generate_title` /
  :func:`generate_summary`.
- **team-scoped**: declared in ``TeamAgentSpec.tiny_agents`` and held by
  ``TeamInfra`` (one per name, per process), disposed when the team stops. See
  ``TeamAgent.get_tiny_agent``.
"""
from __future__ import annotations

import asyncio
import itertools
from typing import TYPE_CHECKING, Any, Callable

from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.agent_teams.tools.structured_output_tool import (
    StructuredOutputFinishRail,
    StructuredOutputTool,
)
from openjiuwen.agent_teams.workflow.engine.schema import coerce, resolve_schema
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec, TeamModelConfig

# ---------------------------------------------------------------------------
# Preset definitions (title / summary) — system prompt by language, schema is
# language-agnostic. Kept as module constants (minimal); migrate to ``locales``
# if these grow.
# ---------------------------------------------------------------------------

_TITLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"title": {"type": "string", "description": "A concise title."}},
    "required": ["title"],
}
_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string", "description": "A concise summary."}},
    "required": ["summary"],
}
_TITLE_PROMPT: dict[str, str] = {
    "cn": (
        "你是一个标题生成助手。根据用户提供的内容生成一个简洁、准确、概括性强的标题。"
        "只输出标题本身，不要解释、不要加引号、不要添加额外内容。"
    ),
    "en": (
        "You are a title-generation assistant. Produce one concise, accurate, "
        "descriptive title for the user's content. Output only the title itself — "
        "no explanation, no quotes, no extra text."
    ),
}
_SUMMARY_PROMPT: dict[str, str] = {
    "cn": (
        "你是一个摘要生成助手。根据用户提供的内容生成一段简洁、忠实、抓住要点的摘要。"
        "只输出摘要本身，不要解释、不要复述全文。"
    ),
    "en": (
        "You are a summarization assistant. Produce one concise, faithful summary "
        "that captures the key points of the user's content. Output only the "
        "summary itself — no explanation, no restating the whole text."
    ),
}


def _normalize_language(language: str) -> str:
    """Clamp an arbitrary language hint to a supported translator language."""
    return language if language in ("cn", "en") else "cn"


def _output_text(result: Any) -> str:
    """Extract the final free-text output from a ``run_once`` / round result."""
    if isinstance(result, dict):
        return str(result.get("output", ""))
    return str(result or "")


class TinyAgent:
    """A minimal native-harness agent: system prompt + model + optional schema.

    Wraps one ``DeepAgentSpec`` template. ``run`` builds a fresh harness per call;
    ``chat`` lazily starts a single persistent harness reused across turns. The
    instance is cheap to construct; the cost is paid when a harness is built.
    """

    def __init__(
        self,
        spec: "DeepAgentSpec",
        *,
        default_schema: Any = None,
        language: str = "cn",
    ) -> None:
        """Initialize a tiny agent from a resolved minimal spec.

        Args:
            spec: The minimal ``DeepAgentSpec`` (system prompt + model, no tools).
            default_schema: Optional schema (dict / pydantic model) used by
                ``run`` / ``chat`` when the call passes no explicit ``schema``.
            language: Prompt language for the structured-output tool i18n.
        """
        self._spec = spec
        self._default_schema = default_schema
        self._language = _normalize_language(language)
        self._t: Translator = make_translator(self._language)
        # Per-call card-id suffix so concurrent run() harnesses never share an
        # owner id (the ability manager qualifies tool ids per owner).
        self._run_seq = itertools.count()

        # Persistent chat harness (lazy). One turn at a time via the lock; the
        # IDLE callback resolves the in-flight turn's future.
        self._chat_harness: NativeHarness | None = None
        self._chat_lock = asyncio.Lock()
        self._turn_future: asyncio.Future | None = None
        self._last_finished: dict | None = None
        self._failed: bool = False

    # ------------------------------------------------------------------
    # Single-shot
    # ------------------------------------------------------------------

    async def run(self, content: str, *, schema: Any = None) -> str | Any:
        """Run one stateless single-shot task and return its result.

        Args:
            content: The task input.
            schema: Optional per-call schema (dict / pydantic model); falls back
                to the agent's ``default_schema``. When effective schema is None,
                the plain free-text output is returned; otherwise the structured
                result (a dict, or a model instance for a pydantic schema).

        Returns:
            The free-text output (no schema) or the coerced structured result.
        """
        json_schema, model = resolve_schema(schema if schema is not None else self._default_schema)
        spec = self._spec_for_run(json_schema)
        harness = NativeHarness(spec)
        if json_schema is not None:
            harness.add_rail(StructuredOutputFinishRail())
        prompt = content
        capture: StructuredOutputTool | None = None
        if json_schema is not None:
            # The submit tool was placed on the spec; the harness owns its
            # lifecycle. Keep a reference to read ``captured`` after the run.
            capture = next(t for t in spec.tools if isinstance(t, StructuredOutputTool))
            prompt = f"{content}\n\n{self._t('structured_output', key='reminder')}"
        try:
            result = await harness.run_once(prompt)
        finally:
            try:
                await harness.dispose()
            except Exception:
                team_logger.debug("[tiny_agent] run harness dispose failed", exc_info=True)
        if json_schema is None:
            return _output_text(result)
        if capture is None or not (capture.called and capture.captured is not None):
            raise_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg="tiny agent did not submit a structured result via structured_output",
            )
        return coerce(capture.captured, json_schema, model)

    def _spec_for_run(self, json_schema: dict | None) -> "DeepAgentSpec":
        """Derive a per-call spec: unique card id, plus the schema tool if any.

        ``model_copy(update=...)`` is used (not the ``DeepAgentSpec`` constructor)
        so a concrete ``StructuredOutputTool`` instance rides on ``tools`` without
        tripping field validation — mirroring the swarmflow worker path.
        """
        base_card = self._spec.card
        run_card = AgentCard(
            id=f"{base_card.id}-{next(self._run_seq)}",
            name=base_card.name,
            description=base_card.description,
        )
        update: dict[str, Any] = {"card": run_card}
        if json_schema is not None:
            update["tools"] = [StructuredOutputTool(json_schema, self._t)]
        return self._spec.model_copy(update=update)

    # ------------------------------------------------------------------
    # Multi-turn
    # ------------------------------------------------------------------

    async def chat(self, content: str, *, schema: Any = None) -> str | Any:
        """Advance one turn on the persistent conversation harness.

        The first call starts the harness; subsequent calls reuse it so context
        persists across turns. Turns are serialized (one at a time).

        Args:
            content: This turn's message.
            schema: Optional per-call schema (dict / pydantic model); falls back
                to the agent's ``default_schema``.

        Returns:
            The free-text reply (no schema) or the coerced structured result.
        """
        json_schema, model = resolve_schema(schema if schema is not None else self._default_schema)
        async with self._chat_lock:
            await self._ensure_chat_started()
            harness = self._chat_harness  # non-None after _ensure_chat_started
            capture: StructuredOutputTool | None = None
            turn_prompt = content
            if json_schema is not None:
                capture = StructuredOutputTool(json_schema, self._t)
                harness.ability_manager.add_ability(capture.card, capture)
                turn_prompt = f"{content}\n\n{self._t('structured_output', key='reminder')}"
            try:
                result = await self._drive_turn(turn_prompt)
            finally:
                if capture is not None:
                    try:
                        harness.ability_manager.remove_ability("structured_output")
                    except Exception:
                        team_logger.debug("[tiny_agent] structured_output detach failed", exc_info=True)
            if self._failed:
                raise_error(StatusCode.AGENT_TEAM_EXECUTION_ERROR, error_msg="tiny agent chat round failed")
            if json_schema is None:
                return _output_text(result)
            if capture is None or not (capture.called and capture.captured is not None):
                raise_error(
                    StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                    error_msg="tiny agent chat turn did not submit a structured result via structured_output",
                )
            return coerce(capture.captured, json_schema, model)

    async def _ensure_chat_started(self) -> None:
        """Build + start the persistent chat harness once (idempotent)."""
        if self._chat_harness is not None:
            return
        harness = NativeHarness(self._spec)
        harness.add_rail(StructuredOutputFinishRail())
        await harness.start()
        await harness.subscribe(on_state=self._on_state, on_round=self._on_round)
        self._chat_harness = harness

    async def _drive_turn(self, prompt: str) -> dict | None:
        """Send one turn and await the harness settling back to IDLE."""
        loop = asyncio.get_running_loop()
        self._last_finished = None
        self._failed = False
        fut: asyncio.Future = loop.create_future()
        self._turn_future = fut
        await self._chat_harness.send(prompt, immediate=False)
        return await fut

    async def _on_state(self, *, new: Any) -> None:
        """Resolve the in-flight turn when the harness settles to IDLE."""
        if new is not HarnessState.IDLE:
            return
        fut = self._turn_future
        if fut is not None and not fut.done():
            self._turn_future = None
            fut.set_result(self._last_finished)

    async def _on_round(self, *, kind: str, result: Any) -> None:
        """Cache each round's outcome (last finished wins per turn)."""
        if kind == "finished":
            self._last_finished = result
        elif kind == "failed":
            self._failed = True

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Dispose the persistent chat harness, if one was started (idempotent)."""
        if self._chat_harness is not None:
            try:
                await self._chat_harness.dispose()
            except Exception:
                team_logger.debug("[tiny_agent] chat harness dispose failed", exc_info=True)
            self._chat_harness = None

    async def __aenter__(self) -> "TinyAgent":
        """Enter an async context; the agent is disposed on exit."""
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Dispose the agent on context exit."""
        await self.aclose()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_tiny_agent(
    *,
    system_prompt: str,
    model_name: str,
    model_resolver: Callable[[str], "TeamModelConfig | None"],
    default_schema: Any = None,
    name: str = "tiny",
    language: str = "cn",
    max_iterations: int = 6,
) -> TinyAgent:
    """Create a tiny agent from a system prompt + a resolvable model name.

    Args:
        system_prompt: The agent's system prompt.
        model_name: Model name resolved to a full config via ``model_resolver``.
        model_resolver: Maps ``model_name`` to a ``TeamModelConfig`` (typically a
            closure over ``resolve_member_model(team_spec, ...)``); returning
            None is a hard error here (no base spec to fall back on).
        default_schema: Optional default output schema (dict / pydantic model).
        name: Logical name; becomes the agent card name/id base.
        language: Prompt language for the structured-output tool i18n.
        max_iterations: ReAct iteration ceiling for the underlying harness.

    Returns:
        A ready-to-use :class:`TinyAgent`.
    """
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

    model = model_resolver(model_name)
    if model is None:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason=f"tiny agent model_name '{model_name}' cannot be resolved against the team model pool",
        )
    spec = DeepAgentSpec(
        card=AgentCard(id=name, name=name, description="tiny agent"),
        system_prompt=system_prompt,
        model=model,
        tools=None,
        auto_create_workspace=False,
        max_iterations=max_iterations,
        language=_normalize_language(language),
    )
    return TinyAgent(spec, default_schema=default_schema, language=language)


# ---------------------------------------------------------------------------
# Presets (ephemeral one-shot): title / summary
# ---------------------------------------------------------------------------


def create_title_agent(
    *,
    model_name: str,
    model_resolver: Callable[[str], "TeamModelConfig | None"],
    language: str = "cn",
) -> TinyAgent:
    """Create a tiny agent preconfigured to generate a title (structured output)."""
    lang = _normalize_language(language)
    return create_tiny_agent(
        system_prompt=_TITLE_PROMPT[lang],
        model_name=model_name,
        model_resolver=model_resolver,
        default_schema=_TITLE_SCHEMA,
        name="tiny-title",
        language=lang,
    )


def create_summary_agent(
    *,
    model_name: str,
    model_resolver: Callable[[str], "TeamModelConfig | None"],
    language: str = "cn",
) -> TinyAgent:
    """Create a tiny agent preconfigured to generate a summary (structured output)."""
    lang = _normalize_language(language)
    return create_tiny_agent(
        system_prompt=_SUMMARY_PROMPT[lang],
        model_name=model_name,
        model_resolver=model_resolver,
        default_schema=_SUMMARY_SCHEMA,
        name="tiny-summary",
        language=lang,
    )


async def generate_title(
    content: str,
    *,
    model_name: str,
    model_resolver: Callable[[str], "TeamModelConfig | None"],
    language: str = "cn",
) -> str:
    """One-call helper: generate a title for ``content`` and return it as text."""
    async with create_title_agent(model_name=model_name, model_resolver=model_resolver, language=language) as agent:
        result = await agent.run(content)
        return str(result.get("title", "")) if isinstance(result, dict) else str(result)


async def generate_summary(
    content: str,
    *,
    model_name: str,
    model_resolver: Callable[[str], "TeamModelConfig | None"],
    language: str = "cn",
) -> str:
    """One-call helper: generate a summary for ``content`` and return it as text."""
    async with create_summary_agent(model_name=model_name, model_resolver=model_resolver, language=language) as agent:
        result = await agent.run(content)
        return str(result.get("summary", "")) if isinstance(result, dict) else str(result)


__all__ = [
    "TinyAgent",
    "create_tiny_agent",
    "create_title_agent",
    "create_summary_agent",
    "generate_title",
    "generate_summary",
]
