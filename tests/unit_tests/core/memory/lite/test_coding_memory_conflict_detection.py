"""Unit tests for Coding Memory conflict detection.

1. WriteResult/WriteMode data model
2. frontmatter helper functions
3. Snapshots and optimistic concurrency
4. End-to-end behavior covered in system tests with full dependencies
"""

import asyncio
import uuid

import pytest

from openjiuwen.core.memory.lite.conflict_types import WriteMode, WriteResult
from openjiuwen.core.memory.manage.update.mem_update_checker import MemoryStatus


class TestWriteResultConflictDetection:
    """Test WriteResult output for conflict detection core logic."""

    def test_redundant_skip_result(self):
        """Test redundant scenario returns skip mode."""
        result = WriteResult(
            success=True,
            path="/test/file.md",
            mode=WriteMode.SKIP,
            note="Content is redundant with existing memories"
        )

        d = result.to_dict()
        assert d["success"] is True
        assert d["mode"] == "skip"
        assert "redundant" in d["note"].lower()

    def test_conflict_detected_result(self):
        """Test conflict scenario returns conflict information."""
        result = WriteResult(
            success=True,
            path="/test/file.md",
            mode=WriteMode.CREATE,
            conflict_detected=True,
            conflicting_files=["old1.md", "old2.md"],
            note="Conflicts with: old1.md, old2.md. Use coding_memory_read to review."
        )

        d = result.to_dict()
        assert d["success"] is True
        assert d["mode"] == "create"
        assert d["conflict_detected"] is True
        assert d["conflicting_files"] == ["old1.md", "old2.md"]
        assert "old1.md" in d["note"]

    def test_create_mode_no_conflict(self):
        """Test create mode with no conflict."""
        result = WriteResult(
            success=True,
            path="/test/new.md",
            mode=WriteMode.CREATE
        )

        d = result.to_dict()
        assert d["success"] is True
        assert d["mode"] == "create"
        assert "conflict_detected" not in d  # Not included when no conflict

    def test_append_mode_with_self_conflict(self):
        """Test append mode with self-conflict."""
        result = WriteResult(
            success=True,
            path="/test/file.md",
            mode=WriteMode.APPEND,
            conflict_detected=True,
            conflicting_files=["file.md"],  # __self__ converted to filename
            note="Conflicts with: file.md"
        )

        d = result.to_dict()
        assert d["mode"] == "append"
        assert "file.md" in d["conflicting_files"]

    def test_append_mode_with_other_conflict(self):
        """Test append mode with conflict from other files."""
        result = WriteResult(
            success=True,
            path="/test/file.md",
            mode=WriteMode.APPEND,
            conflict_detected=True,
            conflicting_files=["other_memory.md"],
            note="Conflicts with: other_memory.md"
        )

        d = result.to_dict()
        assert d["conflicting_files"] == ["other_memory.md"]

    def test_write_error_result(self):
        """Test write error."""
        result = WriteResult(
            success=False,
            path="/test/file.md",
            mode=WriteMode.CREATE,
            error="Invalid frontmatter"
        )

        d = result.to_dict()
        assert d["success"] is False
        assert d["error"] == "Invalid frontmatter"


class TestConflictLogicScenarios:
    """Test conflict detection logic scenarios (based on WriteResult)."""

    def test_scenario_no_old_memories(self):
        """Scenario 1: No similar old memories → create directly."""
        # When _search_similar returns empty, or no LLM available
        # Result: WriteResult(success=True, mode=CREATE)
        result = WriteResult(success=True, path="/test/new.md", mode=WriteMode.CREATE)
        assert result.mode == WriteMode.CREATE
        assert not result.conflict_detected

    def test_scenario_redundant_detection(self):
        """Scenario 2: Detected redundant → skip."""
        # MemUpdateChecker returns actions not containing new memory ID
        # Result: WriteResult(success=True, mode=SKIP)
        result = WriteResult(
            success=True, path="/test/new.md", mode=WriteMode.SKIP,
            note="Content is redundant with existing memories"
        )
        assert result.mode == WriteMode.SKIP
        assert "redundant" in result.note.lower()

    def test_scenario_conflicting_detection(self):
        """Scenario 3: Detected conflict → write + return conflict info."""
        # MemUpdateChecker returns ADD new memory + DELETE old memory
        # Result: WriteResult(success=True, mode=CREATE, conflict_detected=True)
        result = WriteResult(
            success=True, path="/test/new.md", mode=WriteMode.CREATE,
            conflict_detected=True, conflicting_files=["old.md"]
        )
        assert result.mode == WriteMode.CREATE  # Still writes
        assert result.conflict_detected  # But returns conflict info

    def test_scenario_append_self_conflict(self):
        """Scenario 4: Append mode conflicts with self file content."""
        # __self__ old_body detected as conflict
        # Result: conflicting_files contains self filename
        result = WriteResult(
            success=True, path="/test/file.md", mode=WriteMode.APPEND,
            conflict_detected=True, conflicting_files=["file.md"]
        )
        assert "file.md" in result.conflicting_files


class TestMemoryStatusEnum:
    """Test MemoryStatus enum (used for conflict judgment)."""

    def test_memory_status_values(self):
        """Test MemoryStatus enum values."""
        assert MemoryStatus.ADD.value == "add"
        assert MemoryStatus.DELETE.value == "delete"


class TestOptimisticConcurrency:
    """Test optimistic concurrency mechanism (_snapshot_memory_files and snapshot validation logic)."""

    def test_frozenset_snapshot_equality(self):
        """Snapshots use frozenset, same file set snapshots should be equal."""
        s1 = frozenset(["a.md", "b.md"])
        s2 = frozenset(["b.md", "a.md"])
        assert s1 == s2  # Order independent

    def test_frozenset_snapshot_inequality_on_new_file(self):
        """New file causes snapshot inequality."""
        s_before = frozenset(["a.md", "b.md"])
        s_after = frozenset(["a.md", "b.md", "c.md"])
        assert s_before != s_after

    def test_frozenset_snapshot_inequality_on_removed_file(self):
        """Removed file causes snapshot inequality."""
        s_before = frozenset(["a.md", "b.md", "c.md"])
        s_after = frozenset(["a.md", "b.md"])
        assert s_before != s_after

    def test_basename_in_snapshot_detects_existing_file(self):
        """Check file existence via basename in snapshot."""
        snapshot = frozenset(["existing.md", "other.md"])
        assert "existing.md" in snapshot
        assert "new_file.md" not in snapshot

    def test_max_conflict_retries_value(self):
        """Validate max conflict retries constant."""
        from openjiuwen.core.memory.lite.coding_memory_tools import _MAX_CONFLICT_RETRIES
        assert _MAX_CONFLICT_RETRIES >= 1
        assert _MAX_CONFLICT_RETRIES <= 5  # Should not be too large to avoid infinite loops

    @pytest.mark.asyncio
    async def test_concurrent_snapshot_reads(self):
        """Multiple coroutines reading snapshots concurrently should not block each other (no lock)."""
        from openjiuwen.core.memory.lite import coding_memory_tools

        # Without sys_operation, snapshot should return empty set
        original_sys_op = coding_memory_tools.coding_memory_sys_operation
        try:
            coding_memory_tools.coding_memory_sys_operation = None

            results = await asyncio.gather(
                coding_memory_tools._snapshot_memory_files("coding_memory"),
                coding_memory_tools._snapshot_memory_files("coding_memory"),
                coding_memory_tools._snapshot_memory_files("coding_memory"),
            )
            assert all(r == frozenset() for r in results)
        finally:
            coding_memory_tools.coding_memory_sys_operation = original_sys_op


class TestLockMechanismBasic:
    """Test lock mechanism basic logic (without importing coding_memory_tools)."""

    def test_asyncio_lock_basic(self):
        """Test asyncio.Lock basic behavior."""
        lock = asyncio.Lock()
        assert not lock.locked()

    @pytest.mark.asyncio
    async def test_asyncio_lock_acquire_release(self):
        """Test lock acquire and release."""
        lock = asyncio.Lock()
        async with lock:
            assert lock.locked()
        assert not lock.locked()

    def test_lock_dict_pattern(self):
        """Test lock dictionary pattern."""
        locks = {}
        path = "/test/file.md"

        # Simulate _get_file_lock logic
        if path not in locks:
            locks[path] = asyncio.Lock()

        assert path in locks
        assert isinstance(locks[path], asyncio.Lock)

        # Getting again returns the same lock
        same_lock = locks[path]
        assert locks[path] is same_lock


class TestMemoryIndexLockMechanism:
    """Test MEMORY.md index lock mechanism."""

    @pytest.mark.asyncio
    async def test_memory_index_lock_exists(self):
        """Verify _memory_index_lock exists as an independent asyncio.Lock."""
        from openjiuwen.core.memory.lite import coding_memory_tools

        assert hasattr(coding_memory_tools, "_memory_index_lock")
        assert isinstance(coding_memory_tools._memory_index_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_memory_index_lock_separate_from_file_locks(self):
        """Verify index lock is separate from file locks dictionary."""
        from openjiuwen.core.memory.lite import coding_memory_tools

        # Index lock should not appear in file locks dictionary
        assert "_memory_index_lock" not in coding_memory_tools._file_locks
        # Index lock is a different object from file locks init lock
        assert coding_memory_tools._memory_index_lock is not coding_memory_tools._file_locks_init_lock

    @pytest.mark.asyncio
    async def test_memory_index_lock_concurrent_protection(self):
        """Simulate concurrent writes to MEMORY.md: index lock should serialize operations."""
        from openjiuwen.core.memory.lite import coding_memory_tools

        order = []

        async def simulate_index_update(tag: str):
            async with coding_memory_tools._memory_index_lock:
                order.append(f"{tag}_start")
                await asyncio.sleep(0.01)
                order.append(f"{tag}_end")

        # Two coroutines concurrently attempt to update index
        await asyncio.gather(
            simulate_index_update("A"),
            simulate_index_update("B"),
        )

        # Operations should not interleave (start1/end1/start2/end2 or start2/end2/start1/end1)
        # i.e., A_start, B_start, A_end, B_end such interleaving is not allowed
        a_indices = [i for i, x in enumerate(order) if x.startswith("A")]
        b_indices = [i for i, x in enumerate(order) if x.startswith("B")]

        # A's start and end should be adjacent (not interleaved with B)
        a_range = set(range(a_indices[0], a_indices[1] + 1))
        b_range = set(range(b_indices[0], b_indices[1] + 1))
        assert a_range.isdisjoint(b_range), f"Operations interleaved: {order}"


class TestEditLockProtection:
    """Test coding_memory_edit file lock protection logic."""

    @pytest.mark.asyncio
    async def test_edit_uses_same_file_lock_as_write(self):
        """Verify edit and write use the same file-level lock for the same file."""
        from openjiuwen.core.memory.lite import coding_memory_tools

        path = "/coding_memory/test_file.md"
        lock1 = await coding_memory_tools._get_file_lock(path)
        lock2 = await coding_memory_tools._get_file_lock(path)

        # Same path should return the same lock object
        assert lock1 is lock2

    @pytest.mark.asyncio
    async def test_different_files_use_different_locks(self):
        """Verify different files use different lock objects."""
        from openjiuwen.core.memory.lite import coding_memory_tools

        lock_a = await coding_memory_tools._get_file_lock("/coding_memory/a.md")
        lock_b = await coding_memory_tools._get_file_lock("/coding_memory/b.md")

        assert lock_a is not lock_b

    @pytest.mark.asyncio
    async def test_concurrent_edit_and_write_same_file_serialized(self):
        """Simulate concurrent edit and write on the same file: file lock should serialize operations."""
        from openjiuwen.core.memory.lite import coding_memory_tools

        path = "/coding_memory/same_file.md"
        lock = await coding_memory_tools._get_file_lock(path)
        order = []

        async def simulate_write(tag: str):
            async with lock:
                order.append(f"{tag}_start")
                await asyncio.sleep(0.01)
                order.append(f"{tag}_end")

        await asyncio.gather(
            simulate_write("write"),
            simulate_write("edit"),
        )

        # Operations should not interleave
        w_indices = [i for i, x in enumerate(order) if x.startswith("write")]
        e_indices = [i for i, x in enumerate(order) if x.startswith("edit")]

        w_range = set(range(w_indices[0], w_indices[1] + 1))
        e_range = set(range(e_indices[0], e_indices[1] + 1))
        assert w_range.isdisjoint(e_range), f"Operations interleaved: {order}"


class TestConflictNoteFormat:
    """Test conflict note message format."""

    def test_note_format_single_conflict(self):
        """Test note format for single conflict."""
        result = WriteResult(
            success=True, path="/test/new.md", mode=WriteMode.CREATE,
            conflict_detected=True, conflicting_files=["old.md"],
            note="Conflicts with: old.md. Use coding_memory_read to review, then coding_memory_edit to update."
        )
        assert "old.md" in result.note
        assert "coding_memory_read" in result.note
        assert "coding_memory_edit" in result.note

    def test_note_format_multiple_conflicts(self):
        """Test note format for multiple conflicts."""
        files = ["old1.md", "old2.md", "old3.md"]
        result = WriteResult(
            success=True, path="/test/new.md", mode=WriteMode.CREATE,
            conflict_detected=True, conflicting_files=files,
            note=f"Conflicts with: {', '.join(files)}. Use coding_memory_read to review."
        )
        assert "old1.md" in result.note
        assert "old2.md" in result.note
        assert "old3.md" in result.note


class TestFileLockRegistry:
    """_get_file_lock keeps one asyncio.Lock per path for the process lifetime (no unsafe eviction)."""

    @pytest.mark.asyncio
    async def test_same_path_returns_same_lock_instance(self):
        """Repeated _get_file_lock for one path returns the same Lock object."""
        from openjiuwen.core.memory.lite import coding_memory_tools

        path = f"/coding_memory/same_{uuid.uuid4().hex}.md"
        a = await coding_memory_tools._get_file_lock(path)
        b = await coding_memory_tools._get_file_lock(path)
        assert a is b
        assert path in coding_memory_tools._file_locks

    @pytest.mark.asyncio
    async def test_concurrent_get_file_lock_same_instance(self):
        """Concurrent getters for one path all receive the same Lock."""
        from openjiuwen.core.memory.lite import coding_memory_tools

        path = f"/coding_memory/gather_{uuid.uuid4().hex}.md"

        async def get_lock():
            return await coding_memory_tools._get_file_lock(path)

        locks = await asyncio.gather(*[get_lock() for _ in range(32)])
        assert all(l is locks[0] for l in locks)

    @pytest.mark.asyncio
    async def test_lock_entry_retained_after_async_with(self):
        """Registry does not remove locks after the critical section (by design)."""
        from openjiuwen.core.memory.lite import coding_memory_tools

        path = f"/coding_memory/retain_{uuid.uuid4().hex}.md"
        lock = await coding_memory_tools._get_file_lock(path)
        async with lock:
            pass
        assert path in coding_memory_tools._file_locks
        assert coding_memory_tools._file_locks[path] is lock


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
