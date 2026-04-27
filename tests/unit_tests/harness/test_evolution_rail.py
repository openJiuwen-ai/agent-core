# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for EvolutionRail and TrajectoryRail."""

from __future__ import annotations

import asyncio
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
from openjiuwen.harness.rails.evolution_rail import EvolutionRail, EvolutionTriggerPoint
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

        self.assertEqual(len(trajectory.steps), 2)
        self.assertEqual(len(evolution_calls), 1)
        self.assertEqual(len(self.store.query()), 2)  # Round 1 + manual save

    async def test_per_round_mode_resets_builder(self):
        """Test that default mode resets builder after each invoke."""
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
        self.assertEqual(traj.session_id, "conv_custom")
        self.assertEqual(len(traj.steps), 1)


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
        ctx = self._create_ctx(
            AgentCallbackEvent.AFTER_INVOKE,
            InvokeInputs(query="test", conversation_id="conv_snap"),
        )
        result = await rail._snapshot_for_evolution(
            Trajectory(execution_id="test", steps=[], session_id="test", source="online"), ctx
        )
        self.assertIsNotNone(result)
        self.assertIn("trajectory", result)

    async def test_safe_run_evolution_catches_exceptions(self):
        """_safe_run_evolution catches and logs exceptions."""
        class FailingRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                raise RuntimeError("evolution failed")

        rail = FailingRail(trajectory_store=self.store)
        # Should not raise
        await rail._safe_run_evolution({"trajectory": Trajectory(execution_id="test", steps=[], session_id="test", source="online")})

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
            {"trajectory": Trajectory(execution_id="test", steps=[], session_id="test", source="online")}
        )

        self.assertFalse(rail.completed)
        self.assertEqual(
            rail.drain_evolution_outcomes(),
            [{"status": "timed_out", "message": "background evolution timed out after 0.01s"}],
        )

    async def test_safe_run_evolution_records_failure_outcome(self):
        """_safe_run_evolution should preserve failed outcome for downstream watchers."""

        class FailingRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                raise RuntimeError("evolution failed")

        rail = FailingRail(trajectory_store=self.store)
        await rail._safe_run_evolution(
            {"trajectory": Trajectory(execution_id="test", steps=[], session_id="test", source="online")}
        )

        self.assertEqual(
            rail.drain_evolution_outcomes(),
            [{"status": "failed", "message": "evolution failed"}],
        )

    async def test_safe_run_evolution_does_not_buffer_completed_outcomes(self):
        """Successful background evolution should not retain drainable outcome state."""

        class SuccessfulRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                return None

        rail = SuccessfulRail(trajectory_store=self.store)
        await rail._safe_run_evolution(
            {"trajectory": Trajectory(execution_id="test", steps=[], session_id="test", source="online")}
        )

        self.assertEqual(rail.drain_evolution_outcomes(), [])

    async def test_safe_run_evolution_limits_buffered_failure_outcomes(self):
        """Failure outcome buffer should remain bounded in long-lived processes."""

        class FailingRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                raise RuntimeError(f"failed-{trajectory.execution_id}")

        rail = FailingRail(trajectory_store=self.store)
        outcome_count = rail._MAX_PENDING_EVOLUTION_OUTCOMES + 5

        for index in range(outcome_count):
            await rail._safe_run_evolution(
                {
                    "trajectory": Trajectory(
                        execution_id=str(index),
                        steps=[],
                        session_id="test",
                        source="online",
                    )
                }
            )

        outcomes = rail.drain_evolution_outcomes()
        self.assertEqual(len(outcomes), rail._MAX_PENDING_EVOLUTION_OUTCOMES)
        self.assertEqual(outcomes[0]["message"], "failed-5")
        self.assertEqual(outcomes[-1]["message"], f"failed-{outcome_count - 1}")

    async def test_drain_pending_approval_events_default(self):
        """Base class drain returns empty list by default."""
        rail = EvolutionRail(trajectory_store=self.store)
        events = await rail.drain_pending_approval_events()
        self.assertEqual(events, [])

    async def test_drain_waits_for_background_tasks(self):
        """drain(wait=True) waits for background tasks before returning."""
        from openjiuwen.core.session.stream import OutputSchema

        class EmitRail(EvolutionRail):
            async def run_evolution(self, trajectory, ctx=None, *, snapshot=None):
                event = OutputSchema(type="test", index=0, payload={})
                self._pending_approval_events.append(event)

            def _collect_pending_approval_events(self) -> list[OutputSchema]:
                events = list(self._pending_approval_events)
                self._pending_approval_events.clear()
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
