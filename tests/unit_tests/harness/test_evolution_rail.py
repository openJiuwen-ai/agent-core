# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for EvolutionRail and TrajectoryRail."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List
from unittest import IsolatedAsyncioTestCase

from openjiuwen.agent_evolving.trajectory import (
    InMemoryTrajectoryStore,
    Trajectory,
)
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

        traj = trajectories[0]
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

    async def test_should_accumulate_trajectory_default(self):
        """Test that _should_accumulate_trajectory defaults to False."""
        self.assertFalse(self.rail._should_accumulate_trajectory())


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
        """Test that accumulate mode keeps builder across rounds."""
        evolution_calls: List[Trajectory] = []

        class AccumulatingRail(EvolutionRail):
            def _should_accumulate_trajectory(self) -> bool:
                return True

            def _should_trigger_evolution_after_invoke(self) -> bool:
                # Defer evolution trigger, like TeamSkillRail
                return False

            async def run_evolution(self, trajectory, ctx):
                evolution_calls.append(trajectory)

        rail = AccumulatingRail(trajectory_store=self.store)

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

        self.assertEqual(len(trajectory.steps), 2)
        self.assertEqual(len(evolution_calls), 1)
        self.assertEqual(len(self.store.query()), 2)  # Round 1 + manual save

    async def test_per_round_mode_resets_builder(self):
        """Test that default mode resets builder after each invoke."""
        evolution_calls: List[Trajectory] = []

        class PerRoundRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx):
                evolution_calls.append(trajectory)

        rail = PerRoundRail(trajectory_store=self.store)

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

        self.assertIsNone(rail._builder)
        self.assertEqual(len(evolution_calls), 1)
        self.assertEqual(len(self.store.query()), 1)


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
        self.assertEqual(trajectories[0].session_id, "conv_789")

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
        traj = self.evolution_calls[0]
        self.assertEqual(traj.session_id, "conv_custom")
        self.assertEqual(len(traj.steps), 1)
