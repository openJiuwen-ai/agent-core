# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for EvolutionRail and TrajectoryRail."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, List
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from openjiuwen.agent_evolving.trajectory import (
    InMemoryTrajectoryStore,
    LLMCallDetail,
    TRAJECTORY_TRACE_AGENT_HANDLER_NAME,
    TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME,
    Trajectory,
    TrajectoryBuilder,
    TrajectoryStep,
    trajectory_execution_id,
    trajectory_from_steps,
    trajectory_session_id,
    trajectory_source,
    trajectory_steps,
)
from openjiuwen.agent_evolving.trajectory.semconv import (
    OJ_AGENT_INVOKE_TYPE,
    OJ_RL_COMPLETION_TOKEN_IDS,
    OJ_RL_LOGPROBS,
    OJ_RL_PROMPT_TOKEN_IDS,
    OJ_RL_REWARD,
    OJ_WORKFLOW_COMPONENT_TYPE,
    TRAJECTORY_INVOKE_TYPE,
)
from openjiuwen.agent_evolving.trajectory.span_codec import to_otlp_value
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.tracer import TracerHandlerRegistry
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    InvokeInputs,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.core.session.tracer.span import TraceAgentSpan
from openjiuwen.harness.rails.evolution.contracts import EvolutionSnapshot
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint
from openjiuwen.harness.rails.evolution.trajectory_rail import TrajectoryRail


@dataclass
class MockAgentCard:
    """Mock agent card for testing."""

    id: str = "test_agent"


@dataclass
class MockAgent:
    """Mock agent for testing."""

    card: MockAgentCard

    def __post_init__(self):
        self.agent_callback_manager = None


class MockMessageContext:
    """Minimal context object exposing get_messages()."""

    def __init__(self, messages: list[dict]):
        self._messages = messages

    def get_messages(self):
        return self._messages


class TestEvolutionRail(IsolatedAsyncioTestCase):
    """Tests for EvolutionRail base class."""

    def setUp(self):
        """Set up test fixtures."""
        self.store = InMemoryTrajectoryStore()
        self.rail = EvolutionRail(trajectory_store=self.store)

    def _create_ctx(
        self,
        event: AgentCallbackEvent,
        inputs: Any,
        agent_id: str = "test_agent",
        session: Any = None,
    ) -> AgentCallbackContext:
        """Create a mock callback context."""
        agent = MockAgent(card=MockAgentCard(id=agent_id))
        return AgentCallbackContext(
            agent=agent,
            event=event,
            inputs=inputs,
            session=session,
        )

    async def test_trajectory_trace_handlers_are_process_singletons(self):
        """Multiple rails reuse one registered trajectory handler pair."""
        other_rail = EvolutionRail(
            trajectory_store=InMemoryTrajectoryStore(),
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )

        self.assertIs(self.rail.trajectory_state_manager, other_rail.trajectory_state_manager)
        agent_handler = TracerHandlerRegistry.get_agent_handlers()[TRAJECTORY_TRACE_AGENT_HANDLER_NAME]
        workflow_handler = TracerHandlerRegistry.get_workflow_handlers()[TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME]
        self.assertIs(
            TracerHandlerRegistry.get_agent_handlers()[TRAJECTORY_TRACE_AGENT_HANDLER_NAME],
            agent_handler,
        )
        self.assertIs(
            TracerHandlerRegistry.get_workflow_handlers()[TRAJECTORY_TRACE_WORKFLOW_HANDLER_NAME],
            workflow_handler,
        )

    async def test_shared_trace_state_is_released_after_last_rail_consumer(self):
        """One rail finishing should not clear a shared trace needed by another rail."""
        trace_id = "shared-runtime-trace"
        span_manager = SimpleNamespace(_trace_id=trace_id, last_span=None)
        tracer = SimpleNamespace(tracer_agent_span_manager=span_manager)
        session = SimpleNamespace(tracer=lambda: tracer)
        other_store = InMemoryTrajectoryStore()
        other_rail = EvolutionRail(
            trajectory_store=other_store,
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )

        for rail in (self.rail, other_rail):
            await rail.before_invoke(
                self._create_ctx(
                    AgentCallbackEvent.BEFORE_INVOKE,
                    InvokeInputs(query="test query", conversation_id="conv_shared"),
                    session=session,
                )
            )

        span = TraceAgentSpan(trace_id=trace_id, invoke_id="llm-1")
        agent_handler = TracerHandlerRegistry.get_agent_handlers()[TRAJECTORY_TRACE_AGENT_HANDLER_NAME]
        await agent_handler.on_llm_start(
            span,
            inputs={"inputs": [{"role": "user", "content": "hello"}]},
            instance_info={"class_name": "test-model"},
        )
        await agent_handler.on_llm_end(
            span,
            outputs={"outputs": {"role": "assistant", "content": "hi"}},
        )

        await self.rail.after_invoke(
            self._create_ctx(
                AgentCallbackEvent.AFTER_INVOKE,
                InvokeInputs(query="test query", conversation_id="conv_shared", result={"ok": True}),
                session=session,
            )
        )

        self.assertTrue(self.rail.trajectory_state_manager.is_bound_trace(trace_id))

        await other_rail.after_invoke(
            self._create_ctx(
                AgentCallbackEvent.AFTER_INVOKE,
                InvokeInputs(query="test query", conversation_id="conv_shared", result={"ok": True}),
                session=session,
            )
        )

        self.assertEqual(len(self.store.query(session_id="conv_shared")), 1)
        self.assertEqual(len(other_store.query(session_id="conv_shared")), 1)
        self.assertFalse(self.rail.trajectory_state_manager.is_bound_trace(trace_id))

    async def test_trajectory_collection_basic(self):
        """Test basic trajectory collection through invoke lifecycle."""
        # Start invoke
        invoke_inputs = InvokeInputs(
            query="test query",
            conversation_id="conv_123",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            invoke_inputs,
        )
        await self.rail.before_invoke(ctx)

        # Model call
        model_inputs = ModelCallInputs(
            messages=[{"role": "user", "content": "hello"}],
            response={"role": "assistant", "content": "hi there"},
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            model_inputs,
        )
        await self.rail.after_model_call(ctx)

        # Tool call
        tool_inputs = ToolCallInputs(
            tool_name="read_file",
            tool_args={"file_path": "/tmp/test.txt"},
            tool_result="file contents",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_TOOL_CALL,
            tool_inputs,
        )
        await self.rail.after_tool_call(ctx)

        # End invoke
        invoke_inputs_end = InvokeInputs(
            query="test query",
            conversation_id="conv_123",
            result={"status": "done"},
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            invoke_inputs_end,
        )
        await self.rail.after_invoke(ctx)

        # Verify trajectory was saved
        trajectories = self.store.query(session_id="conv_123")
        self.assertEqual(len(trajectories), 1)

        traj = trajectories[0]
        self.assertEqual(trajectory_session_id(traj), "conv_123")
        self.assertEqual(trajectory_source(traj), "online")
        steps = trajectory_steps(traj)
        self.assertEqual(len(steps), 2)

        # Check LLM step
        llm_step = steps[0]
        self.assertEqual(llm_step.kind, "llm")
        self.assertIsNotNone(llm_step.detail)

        # Check tool step
        tool_step = steps[1]
        self.assertEqual(tool_step.kind, "tool")
        self.assertIsNotNone(tool_step.detail)

    async def test_after_invoke_clears_bound_trace_state_after_save(self):
        """Trace state is scoped to one invoke and cleared after saving."""
        trace_id = "runtime-trace-1"
        span_manager = SimpleNamespace(_trace_id=trace_id, last_span=None)
        tracer = SimpleNamespace(tracer_agent_span_manager=span_manager)
        session = SimpleNamespace(tracer=lambda: tracer)
        rail = EvolutionRail(
            trajectory_store=self.store,
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )

        await rail.before_invoke(
            self._create_ctx(
                AgentCallbackEvent.BEFORE_INVOKE,
                InvokeInputs(query="test query", conversation_id="conv_trace"),
                session=session,
            )
        )
        self.assertTrue(rail.trajectory_state_manager.is_bound_trace(trace_id))

        span = TraceAgentSpan(trace_id=trace_id, invoke_id="llm-1")
        agent_handler = TracerHandlerRegistry.get_agent_handlers()[TRAJECTORY_TRACE_AGENT_HANDLER_NAME]
        await agent_handler.on_llm_start(
            span,
            inputs={"inputs": [{"role": "user", "content": "hello"}]},
            instance_info={"class_name": "test-model"},
        )
        await agent_handler.on_llm_end(
            span,
            outputs={"outputs": {"role": "assistant", "content": "hi"}},
        )

        await rail.after_invoke(
            self._create_ctx(
                AgentCallbackEvent.AFTER_INVOKE,
                InvokeInputs(query="test query", conversation_id="conv_trace", result={"ok": True}),
                session=session,
            )
        )

        self.assertEqual(len(self.store.query(session_id="conv_trace")), 1)
        self.assertFalse(rail.trajectory_state_manager.is_bound_trace(trace_id))
        self.assertIsNone(
            rail.trajectory_state_manager.build_trajectory(
                trace_id,
                session_id="conv_trace",
                finalize=True,
            )
        )

    async def test_trace_trajectory_uses_builder_source(self):
        """Trace-backed trajectories should preserve subclass-provided source."""
        trace_id = "runtime-trace-source"
        span_manager = SimpleNamespace(_trace_id=trace_id, last_span=None)
        tracer = SimpleNamespace(tracer_agent_span_manager=span_manager)
        session = SimpleNamespace(tracer=lambda: tracer)

        class SourceRail(EvolutionRail):
            async def _on_before_invoke(self, ctx):
                if self._builder is not None:
                    self._builder.source = "custom_online"

        rail = SourceRail(
            trajectory_store=self.store,
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )

        await rail.before_invoke(
            self._create_ctx(
                AgentCallbackEvent.BEFORE_INVOKE,
                InvokeInputs(query="test query", conversation_id="conv_trace_source"),
                session=session,
            )
        )

        span = TraceAgentSpan(trace_id=trace_id, invoke_id="llm-1")
        agent_handler = TracerHandlerRegistry.get_agent_handlers()[TRAJECTORY_TRACE_AGENT_HANDLER_NAME]
        await agent_handler.on_llm_start(
            span,
            inputs={"inputs": [{"role": "user", "content": "hello"}]},
            instance_info={"class_name": "test-model"},
        )
        await agent_handler.on_llm_end(
            span,
            outputs={"outputs": {"role": "assistant", "content": "hi"}},
        )

        await rail.after_invoke(
            self._create_ctx(
                AgentCallbackEvent.AFTER_INVOKE,
                InvokeInputs(query="test query", conversation_id="conv_trace_source", result={"ok": True}),
                session=session,
            )
        )

        trajectories = self.store.query(source="custom_online")
        self.assertEqual(len(trajectories), 1)
        self.assertEqual(trajectory_source(trajectories[0]), "custom_online")
        self.assertEqual(self.store.query(source="online"), [])

    async def test_trace_trajectory_merges_builder_rl_fields(self):
        """Trace-backed trajectories should preserve builder-only RL fields."""
        trace_id = "runtime-trace-rl"
        span_manager = SimpleNamespace(_trace_id=trace_id, last_span=None)
        tracer = SimpleNamespace(tracer_agent_span_manager=span_manager)
        session = SimpleNamespace(tracer=lambda: tracer)

        class BuilderRlRail(EvolutionRail):
            async def _on_after_model_call(self, ctx):
                if self._builder is None or not self._builder.steps:
                    return
                last_step = self._builder.steps[-1]
                last_step.reward = 0.7
                last_step.prompt_token_ids = [1, 2, 3]
                last_step.completion_token_ids = [10, 11]
                last_step.logprobs = [-0.2, -0.3]
                last_step.meta["turn_id"] = 0

        rail = BuilderRlRail(
            trajectory_store=self.store,
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )

        await rail.before_invoke(
            self._create_ctx(
                AgentCallbackEvent.BEFORE_INVOKE,
                InvokeInputs(query="test query", conversation_id="conv_trace_rl"),
                session=session,
            )
        )

        span = TraceAgentSpan(trace_id=trace_id, invoke_id="llm-1")
        agent_handler = TracerHandlerRegistry.get_agent_handlers()[TRAJECTORY_TRACE_AGENT_HANDLER_NAME]
        await agent_handler.on_llm_start(
            span,
            inputs={"inputs": [{"role": "user", "content": "hello"}]},
            instance_info={"class_name": "test-model"},
        )
        await agent_handler.on_llm_end(
            span,
            outputs={"outputs": {"role": "assistant", "content": "hi"}},
        )

        await rail.after_model_call(
            self._create_ctx(
                AgentCallbackEvent.AFTER_MODEL_CALL,
                ModelCallInputs(
                    messages=[{"role": "user", "content": "hello"}],
                    response={"role": "assistant", "content": "hi"},
                ),
                session=session,
            )
        )

        await rail.after_invoke(
            self._create_ctx(
                AgentCallbackEvent.AFTER_INVOKE,
                InvokeInputs(query="test query", conversation_id="conv_trace_rl", result={"ok": True}),
                session=session,
            )
        )

        trajectories = self.store.query(session_id="conv_trace_rl")
        self.assertEqual(len(trajectories), 1)
        step = trajectory_steps(trajectories[0])[0]
        self.assertEqual(step.reward, 0.7)
        self.assertEqual(step.prompt_token_ids, [1, 2, 3])
        self.assertEqual(step.completion_token_ids, [10, 11])
        self.assertEqual(step.logprobs, [-0.2, -0.3])
        self.assertEqual(step.meta["turn_id"], 0)

    def test_otlp_span_step_kind_uses_shared_fallback_classification(self):
        """Fallback-only OTLP spans should be classified the same as trajectory projection."""
        cases = [
            (
                {"attributes": [{"key": TRAJECTORY_INVOKE_TYPE, "value": to_otlp_value("llm")}]},
                "llm",
            ),
            (
                {"attributes": [{"key": OJ_AGENT_INVOKE_TYPE, "value": to_otlp_value("plugin")}]},
                "tool",
            ),
            (
                {"attributes": [{"key": OJ_WORKFLOW_COMPONENT_TYPE, "value": to_otlp_value("Tool")}]},
                "tool",
            ),
            (
                {"name": "llm.call", "attributes": []},
                "llm",
            ),
            (
                {"name": "tool.read_file", "attributes": []},
                "tool",
            ),
        ]

        for span, expected in cases:
            with self.subTest(span=span):
                self.assertEqual(EvolutionRail._otlp_span_step_kind(span), expected)

    def test_trace_trajectory_merges_builder_fields_for_fallback_classified_span(self):
        """Builder RL fields should merge onto spans identified by invoke-type fallback attrs."""
        rail = EvolutionRail(
            trajectory_store=self.store,
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )
        rail._builder = TrajectoryBuilder(session_id="session-fallback", source="online")
        rail._builder.record_step(
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="fallback-model",
                    messages=[{"role": "user", "content": "hello"}],
                    response={"role": "assistant", "content": "hi"},
                ),
                reward=0.9,
                prompt_token_ids=[1, 2],
                completion_token_ids=[3, 4],
                logprobs=[-0.1, -0.2],
                meta={"turn_id": 1},
            )
        )
        trajectory = Trajectory(
            otlp_trace={
                "resourceSpans": [
                    {
                        "resource": {"attributes": []},
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": "0" * 32,
                                        "spanId": "1" * 16,
                                        "name": "agent.model",
                                        "attributes": [
                                            {"key": TRAJECTORY_INVOKE_TYPE, "value": to_otlp_value("llm")},
                                        ],
                                        "status": {"code": "STATUS_CODE_OK"},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )

        merged_step = trajectory_steps(rail._merge_builder_otlp_attributes(trajectory))[0]

        self.assertEqual(merged_step.reward, 0.9)
        self.assertEqual(merged_step.prompt_token_ids, [1, 2])
        self.assertEqual(merged_step.completion_token_ids, [3, 4])
        self.assertEqual(merged_step.logprobs, [-0.1, -0.2])
        self.assertEqual(merged_step.meta["turn_id"], 1)

    def test_trace_trajectory_keeps_existing_otlp_rl_fields_when_merging_builder_fields(self):
        """Trace-provided RL fields should stay authoritative over builder fallback fields."""
        rail = EvolutionRail(
            trajectory_store=self.store,
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )
        rail._builder = TrajectoryBuilder(session_id="session-existing-rl", source="online")
        rail._builder.record_step(
            TrajectoryStep(
                kind="llm",
                reward=0.9,
                prompt_token_ids=[1, 2],
                completion_token_ids=[3, 4],
                logprobs=[-0.1, -0.2],
            )
        )
        trajectory = Trajectory(
            otlp_trace={
                "resourceSpans": [
                    {
                        "resource": {"attributes": []},
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": "0" * 32,
                                        "spanId": "2" * 16,
                                        "name": "agent.model",
                                        "attributes": [
                                            {"key": TRAJECTORY_INVOKE_TYPE, "value": to_otlp_value("llm")},
                                            {"key": OJ_RL_REWARD, "value": to_otlp_value(0.4)},
                                            {"key": OJ_RL_PROMPT_TOKEN_IDS, "value": to_otlp_value([10, 20])},
                                            {"key": OJ_RL_COMPLETION_TOKEN_IDS, "value": to_otlp_value([30, 40])},
                                            {"key": OJ_RL_LOGPROBS, "value": to_otlp_value([-0.4, -0.5])},
                                        ],
                                        "status": {"code": "STATUS_CODE_OK"},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )

        merged_step = trajectory_steps(rail._merge_builder_otlp_attributes(trajectory))[0]

        self.assertEqual(merged_step.reward, 0.4)
        self.assertEqual(merged_step.prompt_token_ids, [10, 20])
        self.assertEqual(merged_step.completion_token_ids, [30, 40])
        self.assertEqual(merged_step.logprobs, [-0.4, -0.5])

    async def test_extension_points_called(self):
        """Test that extension points are called."""
        call_log: List[str] = []

        class TestEvolutionRail(EvolutionRail):
            async def _on_after_model_call(self, ctx):
                call_log.append("model")

            async def _on_after_tool_call(self, ctx):
                call_log.append("tool")

            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                call_log.append("evolution")

        rail = TestEvolutionRail(trajectory_store=self.store, async_evolution=False)

        # Start invoke
        invoke_inputs = InvokeInputs(
            query="test query",
            conversation_id="conv_456",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            invoke_inputs,
        )
        await rail.before_invoke(ctx)

        # Model call
        model_inputs = ModelCallInputs(
            messages=[{"role": "user", "content": "test"}],
            response={"role": "assistant", "content": "ok"},
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            model_inputs,
        )
        await rail.after_model_call(ctx)

        # Tool call
        tool_inputs = ToolCallInputs(
            tool_name="test_tool",
            tool_args={},
            tool_result="done",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_TOOL_CALL,
            tool_inputs,
        )
        await rail.after_tool_call(ctx)

        # End invoke
        invoke_inputs_end = InvokeInputs(
            query="test query",
            conversation_id="conv_456",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            invoke_inputs_end,
        )
        await rail.after_invoke(ctx)

        self.assertEqual(call_log, ["model", "tool", "evolution"])

    async def test_after_tool_call_trigger_respects_allow_evolution_trigger(self):
        """AFTER_TOOL_CALL should only trigger evolution when gate returns True."""
        call_log: List[str] = []

        class ConditionalEvolutionRail(EvolutionRail):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.allow_trigger = False

            async def _on_after_tool_call(self, ctx):
                call_log.append("tool")

            def _allow_evolution_trigger(self, trigger_point, ctx):
                call_log.append(f"allow:{trigger_point.value}:{self.allow_trigger}")
                return self.allow_trigger

            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                call_log.append("evolution")

        rail = ConditionalEvolutionRail(
            trajectory_store=self.store,
            evolution_trigger=EvolutionTriggerPoint.AFTER_TOOL_CALL,
            async_evolution=False,
        )

        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="test query", conversation_id="conv_conditional"),
        )
        await rail.before_invoke(ctx)

        tool_inputs = ToolCallInputs(
            tool_name="test_tool",
            tool_args={},
            tool_result="done",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_TOOL_CALL,
            tool_inputs,
        )
        await rail.after_tool_call(ctx)

        rail.allow_trigger = True
        await rail.after_tool_call(ctx)

        self.assertEqual(
            call_log,
            [
                "tool",
                "allow:after_tool_call:False",
                "tool",
                "allow:after_tool_call:True",
                "evolution",
            ],
        )

    async def test_async_snapshot_uses_typed_contract_legacy_shape(self):
        messages = [{"role": "user", "content": "hello"}]
        trajectory = trajectory_from_steps(
            execution_id="exec-1",
            session_id="session-1",
            steps=[TrajectoryStep(kind="llm", detail=LLMCallDetail(model="m", messages=messages))],
            source="online",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            InvokeInputs(query="test", conversation_id="session-1"),
        )
        ctx.context = MockMessageContext([{"role": "user", "content": "ignored"}])

        snapshot = await self.rail._snapshot_for_evolution(trajectory, ctx)
        typed_snapshot = EvolutionSnapshot.from_legacy_dict(snapshot)

        self.assertIsInstance(typed_snapshot.trajectory, Trajectory)
        self.assertIs(typed_snapshot.trajectory, trajectory)
        self.assertEqual(
            trajectory_execution_id(typed_snapshot.trajectory),
            trajectory_execution_id(trajectory),
        )
        self.assertTrue(hasattr(typed_snapshot.trajectory, "otlp_trace"))
        self.assertEqual(typed_snapshot.messages, messages)
        self.assertIsNone(typed_snapshot.skill_name)

    async def test_approval_event_compat_wrapper_drains_host_buffer(self):
        self.rail._emit_background_outcome_event({"status": "failed", "message": "background evolution failed"})

        events = await self.rail.drain_pending_approval_events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["evolution_meta"]["event_kind"], "outcome")
        self.assertEqual(events[0].payload["evolution_meta"]["rail_kind"], "base")
        self.assertEqual(events[0].payload["evolution_meta"]["status"], "failed")

    async def test_background_outcome_event_includes_optional_structured_fields(self):
        self.rail._emit_background_outcome_event(
            {
                "status": "no_evolution_no_records",
                "message": "no applied updates for skill=skill-a",
                "rail_kind": "regular",
                "skill_name": "skill-a",
                "stage": "completed",
                "source": "experience_updater",
            }
        )

        events = await self.rail.drain_pending_host_events()

        meta = events[0].payload["evolution_meta"]
        self.assertEqual(meta["event_kind"], "outcome")
        self.assertEqual(meta["status"], "no_evolution_no_records")
        self.assertEqual(meta["rail_kind"], "regular")
        self.assertEqual(meta["skill_name"], "skill-a")
        self.assertEqual(meta["stage"], "completed")
        self.assertEqual(meta["source"], "experience_updater")

    async def test_after_tool_call_trigger_defaults_to_compatible_behavior(self):
        """Default gate should preserve unconditional AFTER_TOOL_CALL triggering."""
        evolution_calls: List[str] = []

        class DefaultAfterToolRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                evolution_calls.append("evolution")

        rail = DefaultAfterToolRail(
            trajectory_store=self.store,
            evolution_trigger=EvolutionTriggerPoint.AFTER_TOOL_CALL,
            async_evolution=False,
        )

        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="test query", conversation_id="conv_default_tool"),
        )
        await rail.before_invoke(ctx)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_TOOL_CALL,
            ToolCallInputs(tool_name="test_tool", tool_args={}, tool_result="done"),
        )
        await rail.after_tool_call(ctx)

        self.assertEqual(evolution_calls, ["evolution"])

    async def test_after_tool_call_extension_point_runs_without_builder(self):
        """Tool-call hooks should run even when trajectory recording is unavailable."""
        call_log: List[str] = []

        class HookOnlyRail(EvolutionRail):
            async def _on_after_tool_call(self, ctx):
                call_log.append(ctx.inputs.tool_name)

        rail = HookOnlyRail(trajectory_store=self.store, async_evolution=False)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_TOOL_CALL,
            ToolCallInputs(tool_name="test_tool", tool_args={}, tool_result="done"),
        )
        await rail.after_tool_call(ctx)

        self.assertEqual(call_log, ["test_tool"])
        self.assertIsNone(rail._build_trajectory())

    async def test_online_tool_step_records_tool_call_id_and_parent_llm_ref(self):
        rail = EvolutionRail(async_evolution=False)
        rail._builder = TrajectoryBuilder(session_id="session-1", source="online")
        agent = SimpleNamespace(card=SimpleNamespace(id="agent-1"), config=SimpleNamespace(model="m"))

        await rail.after_model_call(
            AgentCallbackContext(
                agent=agent,
                inputs=ModelCallInputs(
                    messages=[{"role": "user", "content": "read file"}],
                    response={"role": "assistant", "content": "", "tool_calls": [{"id": "call_read_1"}]},
                ),
            )
        )
        await rail.after_tool_call(
            AgentCallbackContext(
                agent=agent,
                inputs=ToolCallInputs(
                    tool_name="read_file",
                    tool_args={"file_path": "a.txt"},
                    tool_result={"success": True},
                    tool_call=ToolCall(id="call_read_1", type="function", name="read_file", arguments="{}"),
                ),
            )
        )

        trajectory = rail._build_trajectory()
        steps = trajectory_steps(trajectory)
        assert steps[1].detail.tool_call_id == "call_read_1"
        assert steps[1].meta["parent_llm_call"] == "llm_0001"

    async def test_online_tool_step_uses_matching_tool_call_id_for_parent_ref(self):
        rail = EvolutionRail(async_evolution=False)
        rail._builder = TrajectoryBuilder(session_id="session-1", source="online")
        agent = SimpleNamespace(card=SimpleNamespace(id="agent-1"), config=SimpleNamespace(model="m"))

        await rail.after_model_call(
            AgentCallbackContext(
                agent=agent,
                inputs=ModelCallInputs(
                    messages=[{"role": "user", "content": "read file"}],
                    response={"role": "assistant", "content": "", "tool_calls": [{"id": "call_read_1"}]},
                ),
            )
        )
        await rail.after_model_call(
            AgentCallbackContext(
                agent=agent,
                inputs=ModelCallInputs(
                    messages=[{"role": "user", "content": "unrelated followup"}],
                    response={"role": "assistant", "content": "no tool here"},
                ),
            )
        )
        await rail.after_tool_call(
            AgentCallbackContext(
                agent=agent,
                inputs=ToolCallInputs(
                    tool_name="read_file",
                    tool_args={"file_path": "a.txt"},
                    tool_result={"success": True},
                    tool_call=ToolCall(id="call_read_1", type="function", name="read_file", arguments="{}"),
                ),
            )
        )

        trajectory = rail._build_trajectory()
        steps = trajectory_steps(trajectory)
        assert steps[2].detail.tool_call_id == "call_read_1"
        assert steps[2].meta["parent_llm_call"] == "llm_0001"

    async def test_after_evolution_triggered_hook_runs_after_trigger(self):
        """Subclasses can observe successful trigger scheduling after evolution starts."""
        call_log: List[str] = []

        class HookedEvolutionRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                call_log.append("evolution")

            async def _on_after_evolution_triggered(self, trajectory, ctx):
                call_log.append(f"after:{trajectory_session_id(trajectory)}")

        rail = HookedEvolutionRail(trajectory_store=self.store, async_evolution=False)

        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="test query", conversation_id="conv_hook"),
        )
        await rail.before_invoke(ctx)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            ModelCallInputs(
                messages=[{"role": "user", "content": "test"}],
                response={"role": "assistant", "content": "ok"},
            ),
        )
        await rail.after_model_call(ctx)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            InvokeInputs(query="test query", conversation_id="conv_hook"),
        )
        await rail.after_invoke(ctx)

        self.assertEqual(call_log, ["evolution", "after:conv_hook"])

    async def test_lifecycle_debug_logs_builder_create_reuse_and_missing_builder(self):
        """Lifecycle debug logs should expose builder session decisions."""
        debug_messages: list[str] = []

        with patch(
            "openjiuwen.harness.rails.evolution.evolution_rail.logger.debug",
            side_effect=lambda msg, *args, **kwargs: debug_messages.append(msg % args),
        ):
            await self.rail.before_invoke(
                self._create_ctx(
                    AgentCallbackEvent.BEFORE_INVOKE,
                    InvokeInputs(query="first", conversation_id="session-logs"),
                )
            )
            await self.rail.before_invoke(
                self._create_ctx(
                    AgentCallbackEvent.BEFORE_INVOKE,
                    InvokeInputs(query="second", conversation_id="session-logs"),
                )
            )
            self.rail._reset_trajectory_builder()
            await self.rail.after_tool_call(
                self._create_ctx(
                    AgentCallbackEvent.AFTER_TOOL_CALL,
                    ToolCallInputs(tool_name="view_task", tool_args={}, tool_result="completed"),
                )
            )

        joined = "\n".join(debug_messages)
        self.assertIn("[EvolutionRail] created trajectory builder session_id=session-logs", joined)
        self.assertIn("[EvolutionRail] reusing trajectory builder session_id=session-logs", joined)
        self.assertIn("[EvolutionRail] after_tool_call trajectory recording skipped because builder is empty", joined)

    async def test_no_op_without_builder(self):
        """Test that hooks are no-op when builder is not initialized."""
        # Call after_model_call without starting invoke
        model_inputs = ModelCallInputs(
            messages=[{"role": "user", "content": "test"}],
            response={"role": "assistant", "content": "ok"},
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            model_inputs,
        )
        # Should not raise
        await self.rail.after_model_call(ctx)

        # Verify no trajectory saved
        trajectories = self.store.query()
        self.assertEqual(len(trajectories), 0)


class TestEvolutionRailAccumulation(IsolatedAsyncioTestCase):
    """Tests for multi-round trajectory accumulation."""

    def setUp(self):
        """Set up test fixtures."""
        self.store = InMemoryTrajectoryStore()

    def _create_ctx(
        self,
        event: AgentCallbackEvent,
        inputs: Any,
        agent_id: str = "test_agent",
    ) -> AgentCallbackContext:
        """Create a mock callback context."""
        agent = MockAgent(card=MockAgentCard(id=agent_id))
        return AgentCallbackContext(
            agent=agent,
            event=event,
            inputs=inputs,
        )

    async def test_multi_round_accumulation(self):
        """Test that the base rail keeps builder across same-session rounds."""
        evolution_calls: List[Trajectory] = []

        class AccumulatingRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                evolution_calls.append(trajectory)

        rail = AccumulatingRail(
            trajectory_store=self.store,
            evolution_trigger=EvolutionTriggerPoint.NONE,
        )

        # Round 1: start, model call, end
        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="q1", conversation_id="conv_multi"),
        )
        await rail.before_invoke(ctx)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            ModelCallInputs(
                messages=[{"role": "user", "content": "q1"}],
                response={"role": "assistant", "content": "a1"},
            ),
        )
        await rail.after_model_call(ctx)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            InvokeInputs(query="q1", conversation_id="conv_multi"),
        )
        await rail.after_invoke(ctx)

        # Builder should NOT be reset in accumulate mode
        self.assertIsNotNone(rail._builder)
        # No evolution triggered yet (defer mode)
        self.assertEqual(len(evolution_calls), 0)
        # Trajectory is saved in after_invoke even when evolution deferred
        self.assertEqual(len(self.store.query()), 1)

        # Round 2: another model call
        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="q2", conversation_id="conv_multi"),
        )
        await rail.before_invoke(ctx)

        # Builder should be the same instance (accumulating)
        self.assertEqual(len(rail._builder.steps), 1)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            ModelCallInputs(
                messages=[{"role": "user", "content": "q2"}],
                response={"role": "assistant", "content": "a2"},
            ),
        )
        await rail.after_model_call(ctx)

        # Now trigger evolution manually via helper methods
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_TOOL_CALL,
            InvokeInputs(query="q2", conversation_id="conv_multi"),
        )
        trajectory = rail._build_trajectory()
        self.assertIsNotNone(trajectory)
        rail._save_trajectory(trajectory)
        await rail.run_evolution(trajectory, ctx)

        self.assertEqual(len(trajectory_steps(trajectory)), 2)
        self.assertEqual(len(evolution_calls), 1)
        self.assertEqual(len(self.store.query()), 2)  # Round 1 + manual save

    async def test_same_session_default_keeps_builder_after_invoke(self):
        """Test that default mode keeps builder after each invoke."""
        evolution_calls: List[Trajectory] = []

        class PerRoundRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                evolution_calls.append(trajectory)

        rail = PerRoundRail(trajectory_store=self.store, async_evolution=False)

        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="q1", conversation_id="conv_single"),
        )
        await rail.before_invoke(ctx)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            ModelCallInputs(
                messages=[{"role": "user", "content": "q1"}],
                response={"role": "assistant", "content": "a1"},
            ),
        )
        await rail.after_model_call(ctx)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            InvokeInputs(query="q1", conversation_id="conv_single"),
        )
        await rail.after_invoke(ctx)

        self.assertIsNotNone(rail._builder)
        self.assertEqual(len(evolution_calls), 1)
        self.assertEqual(len(self.store.query()), 1)

    async def test_new_session_replaces_builder(self):
        """A new conversation starts a fresh trajectory builder."""
        rail = EvolutionRail(trajectory_store=self.store)

        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="q1", conversation_id="conv_a"),
        )
        await rail.before_invoke(ctx)
        first_builder = rail._builder

        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="q2", conversation_id="conv_b"),
        )
        await rail.before_invoke(ctx)

        self.assertIsNot(rail._builder, first_builder)
        self.assertEqual(rail._builder.session_id, "conv_b")

    async def test_runtime_session_id_takes_precedence_over_conversation_id(self):
        """Trajectory accumulation uses the actual runtime session id when available."""
        rail = EvolutionRail(trajectory_store=self.store)
        session = create_agent_session(session_id="runtime-session")

        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="q1", conversation_id="input-session"),
        )
        ctx.session = session
        await rail.before_invoke(ctx)

        self.assertEqual(rail._builder.session_id, "runtime-session")


class TestTrajectoryRail(IsolatedAsyncioTestCase):
    """Tests for TrajectoryRail."""

    def setUp(self):
        """Set up test fixtures."""
        self.store = InMemoryTrajectoryStore()
        self.rail = TrajectoryRail(trajectory_store=self.store)

    def _create_ctx(
        self,
        event: AgentCallbackEvent,
        inputs: Any,
        agent_id: str = "test_agent",
    ) -> AgentCallbackContext:
        """Create a mock callback context."""
        agent = MockAgent(card=MockAgentCard(id=agent_id))
        return AgentCallbackContext(
            agent=agent,
            event=event,
            inputs=inputs,
        )

    async def test_trajectory_rail_collects_only(self):
        """Test that TrajectoryRail only collects trajectories, no evolution."""
        # Start invoke
        invoke_inputs = InvokeInputs(
            query="test query",
            conversation_id="conv_789",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            invoke_inputs,
        )
        await self.rail.before_invoke(ctx)

        # Model call
        model_inputs = ModelCallInputs(
            messages=[{"role": "user", "content": "test"}],
            response={"role": "assistant", "content": "ok"},
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            model_inputs,
        )
        await self.rail.after_model_call(ctx)

        # End invoke
        invoke_inputs_end = InvokeInputs(
            query="test query",
            conversation_id="conv_789",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            invoke_inputs_end,
        )
        await self.rail.after_invoke(ctx)

        # Verify trajectory was saved
        trajectories = self.store.query(session_id="conv_789")
        self.assertEqual(len(trajectories), 1)
        self.assertEqual(trajectory_session_id(trajectories[0]), "conv_789")

    def test_priority(self):
        """Test that TrajectoryRail has expected priority."""
        self.assertEqual(self.rail.priority, 10)

    def test_inherits_evolution_rail(self):
        """Test that TrajectoryRail inherits EvolutionRail."""
        self.assertIsInstance(self.rail, EvolutionRail)


class TestEvolutionRailCustomEvolution(IsolatedAsyncioTestCase):
    """Tests for custom evolution implementations."""

    def setUp(self):
        """Set up test fixtures."""
        self.store = InMemoryTrajectoryStore()
        self.evolution_calls: List[Trajectory] = []

    def _create_ctx(
        self,
        event: AgentCallbackEvent,
        inputs: Any,
        agent_id: str = "test_agent",
    ) -> AgentCallbackContext:
        """Create a mock callback context."""
        agent = MockAgent(card=MockAgentCard(id=agent_id))
        return AgentCallbackContext(
            agent=agent,
            event=event,
            inputs=inputs,
        )

    async def test_custom_evolution_receives_trajectory(self):
        """Test that custom evolution receives the collected trajectory."""

        class CustomEvolutionRail(EvolutionRail):
            def __init__(self, store, call_log):
                super().__init__(trajectory_store=store, async_evolution=False)
                self.call_log = call_log

            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                self.call_log.append(trajectory)

        rail = CustomEvolutionRail(self.store, self.evolution_calls)

        # Start invoke
        invoke_inputs = InvokeInputs(
            query="test query",
            conversation_id="conv_custom",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            invoke_inputs,
        )
        await rail.before_invoke(ctx)

        # Model call
        model_inputs = ModelCallInputs(
            messages=[{"role": "user", "content": "evolve me"}],
            response={"role": "assistant", "content": "done"},
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_MODEL_CALL,
            model_inputs,
        )
        await rail.after_model_call(ctx)

        # End invoke
        invoke_inputs_end = InvokeInputs(
            query="test query",
            conversation_id="conv_custom",
        )
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            invoke_inputs_end,
        )
        await rail.after_invoke(ctx)

        # Verify evolution was called with trajectory
        self.assertEqual(len(self.evolution_calls), 1)
        traj = self.evolution_calls[0]
        self.assertEqual(trajectory_session_id(traj), "conv_custom")
        self.assertEqual(len(trajectory_steps(traj)), 1)


class TestEvolutionRailAsyncMode(IsolatedAsyncioTestCase):
    """Tests for async_evolution mode in EvolutionRail."""

    def setUp(self):
        self.store = InMemoryTrajectoryStore()

    def _create_ctx(
        self,
        event: AgentCallbackEvent,
        inputs: Any,
        agent_id: str = "test_agent",
    ) -> AgentCallbackContext:
        agent = MockAgent(card=MockAgentCard(id=agent_id))
        return AgentCallbackContext(
            agent=agent,
            event=event,
            inputs=inputs,
        )

    async def test_sync_evolution_mode_passes_active_ctx(self):
        """When async_evolution=False, run_evolution receives active ctx."""
        received_args: List[tuple] = []

        class SyncRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                received_args.append((trajectory, ctx, snapshot))

        rail = SyncRail(trajectory_store=self.store, async_evolution=False)

        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="test", conversation_id="conv_sync"),
        )
        await rail.before_invoke(ctx)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            InvokeInputs(query="test", conversation_id="conv_sync"),
        )
        await rail.after_invoke(ctx)

        self.assertEqual(len(received_args), 1)
        _traj, ctx_arg, snapshot_arg = received_args[0]
        self.assertIsNotNone(ctx_arg)
        self.assertIsNone(snapshot_arg)

    async def test_async_evolution_mode_passes_none_ctx_and_snapshot(self):
        """When async_evolution=True, run_evolution receives ctx=None with snapshot."""
        received_args: List[tuple] = []

        class AsyncRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                received_args.append((trajectory, ctx, snapshot))

        rail = AsyncRail(trajectory_store=self.store, async_evolution=True)

        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="test", conversation_id="conv_async"),
        )
        await rail.before_invoke(ctx)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            InvokeInputs(query="test", conversation_id="conv_async"),
        )
        await rail.after_invoke(ctx)

        # Wait for background task to complete
        for task in rail._bg_tasks:
            await task.wait()

        self.assertEqual(len(received_args), 1)
        _traj, ctx_arg, snapshot_arg = received_args[0]
        self.assertIsNone(ctx_arg)
        self.assertIsNotNone(snapshot_arg)
        self.assertIn("trajectory", snapshot_arg)

    async def test_snapshot_for_evolution_default_returns_trajectory(self):
        """Base class _snapshot_for_evolution returns dict with trajectory."""
        rail = EvolutionRail(trajectory_store=self.store)
        ctx = AgentCallbackContext(
            agent=MockAgent(card=MockAgentCard(id="test_agent")),
            event=AgentCallbackEvent.AFTER_INVOKE,
            inputs=InvokeInputs(query="test", conversation_id="conv_snap"),
            context=MockMessageContext(messages=[{"role": "user", "content": "improve the workflow"}]),
        )
        result = await rail._snapshot_for_evolution(
            trajectory_from_steps(
                execution_id="test",
                steps=[],
                session_id="test",
                source="online",
            ),
            ctx,
        )
        self.assertIsNotNone(result)
        self.assertIn("trajectory", result)
        self.assertIsInstance(result["trajectory"], Trajectory)
        self.assertEqual(result["messages"], [])

    async def test_safe_run_evolution_catches_exceptions(self):
        """_safe_run_evolution catches and logs exceptions."""

        class FailingRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                raise RuntimeError("evolution failed")

        rail = FailingRail(trajectory_store=self.store)
        # Should not raise
        await rail._safe_run_evolution(
            {
                "trajectory": trajectory_from_steps(
                    execution_id="test",
                    steps=[],
                    session_id="test",
                    source="online",
                )
            }
        )

    async def test_safe_run_evolution_respects_total_timeout_hook(self):
        """_safe_run_evolution should stop background evolution when total timeout is exceeded."""

        class SlowRail(EvolutionRail):
            def __init__(self, trajectory_store):
                super().__init__(trajectory_store=trajectory_store)
                self.completed = False

            def _get_evolution_total_timeout_secs(self) -> float | None:
                return 0.01

            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                await asyncio.sleep(0.05)
                self.completed = True

        rail = SlowRail(trajectory_store=self.store)
        await rail._safe_run_evolution(
            {
                "trajectory": trajectory_from_steps(
                    execution_id="test",
                    steps=[],
                    session_id="test",
                    source="online",
                )
            }
        )

        self.assertFalse(rail.completed)
        events = await rail.drain_pending_approval_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["evolution_meta"]["event_kind"], "outcome")
        self.assertEqual(events[0].payload["evolution_meta"]["status"], "timed_out")
        self.assertIn("background evolution timed out after 0.01s", events[0].payload["content"])

    async def test_safe_run_evolution_records_failure_outcome(self):
        """_safe_run_evolution should preserve failed outcome for downstream watchers."""

        class FailingRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                raise RuntimeError("evolution failed")

        rail = FailingRail(trajectory_store=self.store)
        await rail._safe_run_evolution(
            {
                "trajectory": trajectory_from_steps(
                    execution_id="test",
                    steps=[],
                    session_id="test",
                    source="online",
                )
            }
        )

        events = await rail.drain_pending_approval_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["evolution_meta"]["event_kind"], "outcome")
        self.assertEqual(events[0].payload["evolution_meta"]["status"], "failed")
        self.assertIn("evolution failed", events[0].payload["content"])

    async def test_safe_run_evolution_does_not_buffer_completed_outcomes(self):
        """Successful background evolution should not retain drainable outcome state."""

        class SuccessfulRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                return None

        rail = SuccessfulRail(trajectory_store=self.store)
        await rail._safe_run_evolution(
            {
                "trajectory": trajectory_from_steps(
                    execution_id="test",
                    steps=[],
                    session_id="test",
                    source="online",
                )
            }
        )

        self.assertEqual(await rail.drain_pending_approval_events(), [])

    async def test_safe_run_evolution_emits_failure_outcomes_to_host_events(self):
        """Failure outcomes should use the shared host event buffer."""

        class FailingRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                raise RuntimeError(f"failed-{trajectory_execution_id(trajectory)}")

        rail = FailingRail(trajectory_store=self.store)

        for index in range(3):
            await rail._safe_run_evolution(
                {
                    "trajectory": trajectory_from_steps(
                        execution_id=str(index),
                        steps=[],
                        session_id="test",
                        source="online",
                    )
                }
            )

        events = await rail.drain_pending_approval_events()
        self.assertEqual(len(events), 3)
        self.assertEqual(
            [event.payload["evolution_meta"]["status"] for event in events],
            ["failed", "failed", "failed"],
        )
        self.assertIn("failed-0", events[0].payload["content"])
        self.assertIn("failed-2", events[-1].payload["content"])

    async def test_drain_pending_approval_events_default(self):
        """Base class drain returns empty list by default."""
        rail = EvolutionRail(trajectory_store=self.store)
        events = await rail.drain_pending_approval_events()
        self.assertEqual(events, [])

    async def test_collect_pending_approval_events_forwards_to_host_events(self):
        """Protected approval collector name remains a host-event wrapper."""
        from openjiuwen.core.session.stream import OutputSchema

        rail = EvolutionRail(trajectory_store=self.store)
        event = OutputSchema(type="test", index=0, payload={})
        rail.emit_host_event(event)

        self.assertEqual(rail._collect_pending_approval_events(), [event])
        self.assertEqual(await rail.drain_pending_approval_events(), [])

    async def test_drain_waits_for_background_tasks(self):
        """drain(wait=True) waits for background tasks before returning."""
        from openjiuwen.core.session.stream import OutputSchema

        class EmitRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                event = OutputSchema(type="test", index=0, payload={})
                self._pending_host_events.append(event)

            def _collect_pending_host_events(self) -> list[OutputSchema]:
                events = list(self._pending_host_events)
                self._pending_host_events.clear()
                return events

        rail = EmitRail(trajectory_store=self.store, async_evolution=True)

        ctx = self._create_ctx(
            AgentCallbackEvent.BEFORE_INVOKE,
            InvokeInputs(query="test", conversation_id="conv_drain"),
        )
        await rail.before_invoke(ctx)

        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            InvokeInputs(query="test", conversation_id="conv_drain"),
        )
        await rail.after_invoke(ctx)

        # drain with wait should get the event
        events = await rail.drain_pending_approval_events(wait=True, timeout=5.0)
        self.assertGreaterEqual(len(events), 1)

    async def test_cleanup_background_tasks(self):
        """cleanup_background_tasks clears the task set."""
        rail = EvolutionRail(trajectory_store=self.store)
        rail._bg_tasks = set()
        await rail.cleanup_background_tasks()
        self.assertEqual(len(rail._bg_tasks), 0)
