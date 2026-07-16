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
    LEGACY_STEP_META,
    OJ_AGENT_INVOKE_TYPE,
    OJ_RL_COMPLETION_TOKEN_IDS,
    OJ_RL_LOGPROBS,
    OJ_RL_PROMPT_TOKEN_IDS,
    OJ_RL_REWARD,
    OJ_WORKFLOW_COMPONENT_TYPE,
    TRAJECTORY_INVOKE_TYPE,
)
from openjiuwen.agent_evolving.trajectory.span_codec import otlp_value_to_python, to_otlp_value
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

    @staticmethod
    def _otlp_trace_with_spans(*spans: dict) -> dict:
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [{"spans": list(spans)}],
                }
            ],
        }

    def test_merge_matches_multiple_spans_in_llm_tool_llm_order(self):
        """Multi-span traces should align builder steps by kind order (llm-tool-llm)."""
        rail = EvolutionRail(trajectory_store=self.store)
        rail._builder = TrajectoryBuilder(session_id="session-multi", source="online")
        rail._builder.record_step(TrajectoryStep(kind="llm", reward=0.1, meta={"turn": 1}))
        rail._builder.record_step(
            TrajectoryStep(
                kind="tool",
                detail={"tool_name": "read_file"},
                meta={"tool_call_id": "call-1"},
            )
        )
        rail._builder.record_step(TrajectoryStep(kind="llm", reward=0.8, meta={"turn": 2}))

        trajectory = Trajectory(
            otlp_trace=self._otlp_trace_with_spans(
                {
                    "traceId": "0" * 32,
                    "spanId": "1" * 16,
                    "name": "agent.model",
                    "attributes": [{"key": TRAJECTORY_INVOKE_TYPE, "value": to_otlp_value("llm")}],
                    "status": {"code": "STATUS_CODE_OK"},
                },
                {
                    "traceId": "0" * 32,
                    "spanId": "2" * 16,
                    "name": "tool.read_file",
                    "attributes": [{"key": TRAJECTORY_INVOKE_TYPE, "value": to_otlp_value("plugin")}],
                    "status": {"code": "STATUS_CODE_OK"},
                },
                {
                    "traceId": "0" * 32,
                    "spanId": "3" * 16,
                    "name": "agent.model",
                    "attributes": [{"key": TRAJECTORY_INVOKE_TYPE, "value": to_otlp_value("llm")}],
                    "status": {"code": "STATUS_CODE_OK"},
                },
            )
        )

        merged = to_legacy_trajectory(rail._merge_builder_otlp_attributes(trajectory))
        self.assertEqual([step.kind for step in merged.steps], ["llm", "tool", "llm"])
        self.assertEqual(merged.steps[0].reward, 0.1)
        self.assertEqual(merged.steps[0].meta["turn"], 1)
        self.assertEqual(merged.steps[1].meta["tool_call_id"], "call-1")
        self.assertEqual(merged.steps[2].reward, 0.8)
        self.assertEqual(merged.steps[2].meta["turn"], 2)

    def test_merge_noop_when_otlp_trace_is_empty_dict(self):
        """Empty otlp_trace dict should skip merge and keep the trajectory unchanged."""
        rail = EvolutionRail(trajectory_store=self.store)
        rail._builder = TrajectoryBuilder(session_id="session-empty-otlp", source="online")
        rail._builder.record_step(TrajectoryStep(kind="llm", reward=0.5))
        trajectory = Trajectory(otlp_trace={})

        merged = rail._merge_builder_otlp_attributes(trajectory)
        self.assertIs(merged, trajectory)
        self.assertEqual(merged.otlp_trace, {})

    def test_set_otlp_span_attr_overwrite_true_replaces_existing_value(self):
        """overwrite=True (LEGACY_STEP_META path) should replace an existing attribute."""
        attributes = [{"key": LEGACY_STEP_META, "value": to_otlp_value({"old": True})}]
        EvolutionRail._set_otlp_span_attr(
            attributes,
            LEGACY_STEP_META,
            {"new": True, "turn_id": 2},
            overwrite=True,
        )
        self.assertEqual(len(attributes), 1)
        self.assertEqual(attributes[0]["key"], LEGACY_STEP_META)
        self.assertEqual(
            otlp_value_to_python(attributes[0]["value"]),
            {"new": True, "turn_id": 2},
        )

    def test_merge_noop_when_builder_has_no_steps(self):
        """Merge should be a no-op when the builder exists but has no steps."""
        rail = EvolutionRail(trajectory_store=self.store)
        rail._builder = TrajectoryBuilder(session_id="session-empty-builder", source="online")
        trajectory = Trajectory(
            otlp_trace=self._otlp_trace_with_spans(
                {
                    "traceId": "0" * 32,
                    "spanId": "4" * 16,
                    "name": "agent.model",
                    "attributes": [
                        {"key": TRAJECTORY_INVOKE_TYPE, "value": to_otlp_value("llm")},
                        {"key": OJ_RL_REWARD, "value": to_otlp_value(0.3)},
                    ],
                    "status": {"code": "STATUS_CODE_OK"},
                }
            )
        )

        merged = rail._merge_builder_otlp_attributes(trajectory)
        self.assertIs(merged, trajectory)
        legacy = to_legacy_trajectory(merged)
        self.assertEqual(len(legacy.steps), 1)
        self.assertEqual(legacy.steps[0].reward, 0.3)

    def test_build_trajectory_falls_back_to_builder_when_otlp_trace_is_none(self):
        """When trace trajectory has no otlp_trace, fall back to builder-only projection."""
        rail = EvolutionRail(trajectory_store=self.store)
        rail._builder = TrajectoryBuilder(session_id="session-fallback-build", source="online")
        rail._builder.record_step(
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="fallback-model",
                    messages=[{"role": "user", "content": "hi"}],
                    response={"role": "assistant", "content": "ok"},
                ),
                reward=0.7,
            )
        )

        original_build = rail._build_trace_trajectory
        rail._build_trace_trajectory = lambda ctx, finalize=False: Trajectory(otlp_trace=None)
        try:
            ctx = AgentCallbackContext(
                agent=MockAgent(card=MockAgentCard(id="test_agent")),
                event=AgentCallbackEvent.AFTER_INVOKE,
                inputs=InvokeInputs(query="q", conversation_id="session-fallback-build"),
            )
            built = rail._build_trajectory(ctx, finalize=True)
        finally:
            rail._build_trace_trajectory = original_build

        self.assertIsNotNone(built)
        legacy = to_legacy_trajectory(built)
        self.assertEqual(len(legacy.steps), 1)
        self.assertEqual(legacy.steps[0].kind, "llm")
        self.assertEqual(legacy.steps[0].reward, 0.7)

    async def test_after_invoke_releases_trace_even_when_builder_is_none(self):
        """after_invoke must still release the bound trace when builder was cleared."""
        rail = EvolutionRail(trajectory_store=self.store)
        rail._active_trace_id = "trace-bound"
        rail._builder = None
        released: list[tuple[str, str]] = []

        def _release(trace_id, *, consumer_id):
            released.append((trace_id, consumer_id))

        rail.trajectory_state_manager.release_trace = _release
        ctx = AgentCallbackContext(
            agent=MockAgent(card=MockAgentCard(id="test_agent")),
            event=AgentCallbackEvent.AFTER_INVOKE,
            inputs=InvokeInputs(query="q", conversation_id="conv-release"),
        )
        await rail.after_invoke(ctx)

        self.assertEqual(released, [("trace-bound", rail.trajectory_consumer_id)])
        self.assertIsNone(rail._active_trace_id)
        self.assertIsNone(rail._builder)

    async def test_after_invoke_prefers_active_trace_id_over_ctx(self):
        """Release must use the before_invoke-bound _active_trace_id when ctx differs."""
        rail = EvolutionRail(trajectory_store=self.store)
        rail._active_trace_id = "trace-A"
        rail._builder = TrajectoryBuilder(session_id="conv-priority", source="online")
        released: list[str] = []

        def _release(trace_id, *, consumer_id):
            released.append(trace_id)

        rail.trajectory_state_manager.release_trace = _release
        rail._trace_id_from_ctx = lambda ctx: "trace-B"  # type: ignore[method-assign]
        ctx = AgentCallbackContext(
            agent=MockAgent(card=MockAgentCard(id="test_agent")),
            event=AgentCallbackEvent.AFTER_INVOKE,
            inputs=InvokeInputs(query="q", conversation_id="conv-priority"),
        )
        await rail.after_invoke(ctx)

        self.assertEqual(released, ["trace-A"])
        self.assertIsNone(rail._active_trace_id)
