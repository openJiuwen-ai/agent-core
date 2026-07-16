# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for EvolutionRail and TrajectoryRail."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List
from unittest import IsolatedAsyncioTestCase

from openjiuwen.agent_evolving.trajectory import (
    InMemoryTrajectoryStore,
    LLMCallDetail,
    Trajectory,
    TrajectoryBuilder,
    TrajectoryStep,
    to_legacy_trajectory,
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
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    ModelCallInputs,
    ToolCallInputs,
    InvokeInputs,
)
from openjiuwen.harness.rails.evolution_rail import EvolutionRail
from openjiuwen.harness.rails.trajectory_rail import TrajectoryRail


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
    ) -> AgentCallbackContext:
        """Create a mock callback context."""
        agent = MockAgent(card=MockAgentCard(id=agent_id))
        return AgentCallbackContext(
            agent=agent,
            event=event,
            inputs=inputs,
        )

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

        traj = to_legacy_trajectory(trajectories[0])
        self.assertEqual(traj.session_id, "conv_123")
        self.assertEqual(traj.source, "online")
        self.assertEqual(len(traj.steps), 2)

        # Check LLM step
        llm_step = traj.steps[0]
        self.assertEqual(llm_step.kind, "llm")
        self.assertIsNotNone(llm_step.detail)

        # Check tool step
        tool_step = traj.steps[1]
        self.assertEqual(tool_step.kind, "tool")
        self.assertIsNotNone(tool_step.detail)

    async def test_extension_points_called(self):
        """Test that extension points are called."""
        call_log: List[str] = []

        class TestEvolutionRail(EvolutionRail):
            async def _on_after_model_call(self, ctx):
                call_log.append("model")

            async def _on_after_tool_call(self, ctx):
                call_log.append("tool")

            async def run_evolution(self, trajectory, ctx):
                call_log.append("evolution")

        rail = TestEvolutionRail(trajectory_store=self.store)

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
        self.assertEqual(to_legacy_trajectory(trajectories[0]).session_id, "conv_789")

    def test_priority(self):
        """Test that TrajectoryRail has expected priority."""
        self.assertEqual(self.rail.priority, 10)

    def test_inherits_evolution_rail(self):
        """Test that TrajectoryRail inherits EvolutionRail."""
        self.assertIsInstance(self.rail, EvolutionRail)


class TestEvolutionRailTraceIdFromCtx(IsolatedAsyncioTestCase):
    """Tests for EvolutionRail._trace_id_from_ctx public SpanManager API."""

    def test_uses_span_manager_trace_id_property(self):
        from openjiuwen.core.session.tracer.span import SpanManager
        from openjiuwen.core.session.tracer.tracer import Tracer

        class _Session:
            def __init__(self, tracer: Tracer):
                self._tracer = tracer

            def tracer(self):
                return self._tracer

        tracer = Tracer()
        ctx = AgentCallbackContext(
            agent=MockAgent(card=MockAgentCard(id="agent")),
            event=AgentCallbackEvent.BEFORE_INVOKE,
            inputs=InvokeInputs(query="q", conversation_id="c"),
            session=_Session(tracer),
        )

        trace_id = EvolutionRail._trace_id_from_ctx(ctx)

        self.assertIsInstance(tracer.tracer_agent_span_manager, SpanManager)
        self.assertEqual(trace_id, tracer.tracer_agent_span_manager.trace_id)

    def test_returns_none_without_span_manager(self):
        ctx = AgentCallbackContext(
            agent=MockAgent(card=MockAgentCard(id="agent")),
            event=AgentCallbackEvent.BEFORE_INVOKE,
            inputs=InvokeInputs(query="q", conversation_id="c"),
        )
        self.assertIsNone(EvolutionRail._trace_id_from_ctx(ctx))


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
                super().__init__(trajectory_store=store)
                self.call_log = call_log

            async def run_evolution(self, trajectory, ctx):
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
        received = self.evolution_calls[0]
        self.assertIsInstance(received, Trajectory)
        self.assertIsNotNone(received.otlp_trace)
        traj = to_legacy_trajectory(received)
        self.assertEqual(traj.session_id, "conv_custom")
        self.assertEqual(len(traj.steps), 1)


class TestEvolutionRailOtlpMerge(IsolatedAsyncioTestCase):
    """Tests for OTLP span attribute merge in EvolutionRail."""

    def setUp(self):
        self.store = InMemoryTrajectoryStore()
        self.rail = EvolutionRail(trajectory_store=self.store)

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
        rail = EvolutionRail(trajectory_store=self.store)
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

        merged_step = to_legacy_trajectory(rail._merge_builder_otlp_attributes(trajectory)).steps[0]

        self.assertEqual(merged_step.reward, 0.9)
        self.assertEqual(merged_step.prompt_token_ids, [1, 2])
        self.assertEqual(merged_step.completion_token_ids, [3, 4])
        self.assertEqual(merged_step.logprobs, [-0.1, -0.2])
        self.assertEqual(merged_step.meta["turn_id"], 1)

    def test_trace_trajectory_keeps_existing_otlp_rl_fields_when_merging_builder_fields(self):
        """Trace-provided RL fields should stay authoritative over builder fallback fields."""
        rail = EvolutionRail(trajectory_store=self.store)
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

        merged_step = to_legacy_trajectory(rail._merge_builder_otlp_attributes(trajectory)).steps[0]

        self.assertEqual(merged_step.reward, 0.4)
        self.assertEqual(merged_step.prompt_token_ids, [10, 20])
        self.assertEqual(merged_step.completion_token_ids, [30, 40])
        self.assertEqual(merged_step.logprobs, [-0.4, -0.5])
