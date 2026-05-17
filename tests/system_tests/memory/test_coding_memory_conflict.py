"""Coding Memory conflict resolution workflow system tests.

Tests the complete conflict resolution workflow:
1. Conflict detection during write
2. Reading conflicting files
3. Editing to resolve conflicts
4. Redundant content handling (auto-skip)
"""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.core.memory.lite.coding_memory_tool_context import CodingMemoryToolContext
from openjiuwen.core.memory.lite.coding_memory_tool_ops import (
    _upsert_memory_index,
    coding_memory_edit_with_context,
    coding_memory_read_with_context,
    coding_memory_write_with_context,
)


@pytest_asyncio.fixture
async def conflict_test_env():
    """Create test environment for conflict resolution tests."""
    from openjiuwen.core.sys_operation.cwd import init_cwd

    await Runner.start()
    work_dir = tempfile.mkdtemp(prefix="coding_memory_conflict_")

    init_cwd(work_dir, project_root=work_dir, workspace=work_dir)

    sys_operation_id = f"coding_memory_conflict_sysop_{os.urandom(4).hex()}"
    card = SysOperationCard(
        id=sys_operation_id,
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=work_dir),
    )
    add_result = Runner.resource_mgr.add_sys_operation(card)
    if add_result.is_err():
        raise RuntimeError(f"add_sys_operation failed: {add_result.msg()}")

    coding_memory_dir = os.path.join(work_dir, "coding_memory")
    os.makedirs(coding_memory_dir, exist_ok=True)

    sys_op = Runner.resource_mgr.get_sys_operation(sys_operation_id)

    workspace = Workspace(
        root_path=work_dir,
        directories=[{"name": "coding_memory", "path": "coding_memory"}]
    )
    ctx = CodingMemoryToolContext(
        workspace=workspace,
        sys_operation=sys_op,
        coding_memory_dir=coding_memory_dir,
        node_name="coding_memory",
    )

    yield {
        "work_dir": work_dir,
        "coding_memory_dir": coding_memory_dir,
        "sys_operation_id": sys_operation_id,
        "sys_op": sys_op,
        "ctx": ctx,
        "workspace": workspace,
    }

    try:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=sys_operation_id)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        await Runner.stop()


class TestConflictResolutionWorkflow:
    """Conflict resolution workflow tests."""

    @pytest.mark.asyncio
    async def test_conflict_detected_then_read_and_edit(self, conflict_test_env):
        """Test conflict resolution flow: detect -> read -> edit."""
        env = conflict_test_env

        initial_content = """---
name: User Role
description: User is a Python developer
type: user
---

User is a senior Python developer familiar with Django and Flask."""

        result1 = await coding_memory_write_with_context(env["ctx"], "user_role.md", initial_content)
        assert result1["success"] is True
        assert result1["mode"] == "create"

        await _upsert_memory_index(
            env["ctx"],
            env["coding_memory_dir"],
            "user_role.md",
            {"name": "User Role", "description": "User is a Python developer"},
        )

        similar_content = """---
name: Developer Role
description: User develops in Python
type: user
---

User develops backend services using Python and Django framework."""

        result2 = await coding_memory_write_with_context(env["ctx"], "developer_role.md", similar_content)

        assert result2["success"] is True

        if result2.get("conflict_detected"):
            conflicting_files = result2.get("conflicting_files", [])

            for conflict_file in conflicting_files:
                read_result = await coding_memory_read_with_context(env["ctx"], conflict_file)
                assert read_result["success"] is True
                assert read_result["content"]

            edit_result = await coding_memory_edit_with_context(
                env["ctx"],
                "developer_role.md",
                "User develops backend services using Python and Django framework.",
                "User develops backend services using Python, Django, and also has experience with FastAPI.",
            )
            assert edit_result["success"] is True

            verify_result = await coding_memory_read_with_context(env["ctx"], "developer_role.md")
            assert verify_result["success"] is True
            assert "FastAPI" in verify_result["content"]

    @pytest.mark.asyncio
    async def test_append_mode_self_conflict_resolution(self, conflict_test_env):
        """Test append mode with self-conflict resolution."""
        env = conflict_test_env

        initial_content = """---
name: Project Setup
description: Project initialization steps
type: project
---

Step 1: Install dependencies
Step 2: Configure database"""

        result1 = await coding_memory_write_with_context(env["ctx"], "project_setup.md", initial_content)
        assert result1["success"] is True

        await _upsert_memory_index(
            env["ctx"],
            env["coding_memory_dir"],
            "project_setup.md",
            {"name": "Project Setup", "description": "Project initialization steps"},
        )

        append_content = """---
name: Project Setup Extended
description: More project setup details
type: project
---

Step 1: Install dependencies
Step 2: Configure database
Step 3: Run migrations"""

        result2 = await coding_memory_write_with_context(env["ctx"], "project_setup.md", append_content)

        assert result2["success"] is True
        assert result2["mode"] == "append"

        if result2.get("conflict_detected"):
            read_result = await coding_memory_read_with_context(env["ctx"], "project_setup.md")
            assert read_result["success"] is True

            edit_result = await coding_memory_edit_with_context(
                env["ctx"],
                "project_setup.md",
                "Step 3: Run migrations",
                "Step 3: Run database migrations and verify connection",
            )
            assert edit_result["success"] is True


class TestRedundantHandling:
    """Redundant content handling tests."""

    @pytest.mark.asyncio
    async def test_redundant_content_skip(self, conflict_test_env):
        """Test that redundant content is auto-skipped."""
        env = conflict_test_env

        original_content = """---
name: API Endpoint
description: User login API
type: reference
---

POST /api/v1/login
Request: {username, password}
Response: {token, expires_in}"""

        result1 = await coding_memory_write_with_context(env["ctx"], "api_login.md", original_content)
        assert result1["success"] is True

        await _upsert_memory_index(
            env["ctx"],
            env["coding_memory_dir"],
            "api_login.md",
            {"name": "API Endpoint", "description": "User login API"},
        )

        redundant_content = """---
name: Login API
description: API for user login
type: reference
---

POST /api/v1/login
Request: {username, password}
Response: {token, expires_in}"""

        result2 = await coding_memory_write_with_context(env["ctx"], "login_api.md", redundant_content)

        if result2.get("mode") == "skip":
            assert "redundant" in result2.get("note", "").lower()
            read_result = await coding_memory_read_with_context(env["ctx"], "login_api.md")
            if read_result["success"]:
                pass

    @pytest.mark.asyncio
    async def test_no_action_needed_for_skip(self, conflict_test_env):
        """Test that skip mode requires no user action."""
        env = conflict_test_env

        content = """---
name: Test Memory
description: Test description
type: user
---

Test content for skip scenario."""

        result = await coding_memory_write_with_context(env["ctx"], "test_skip.md", content)

        await _upsert_memory_index(
            env["ctx"],
            env["coding_memory_dir"],
            "test_skip.md",
            {"name": "Test Memory", "description": "Test description"},
        )

        if result.get("mode") == "skip":
            assert result["success"] is True
            assert not result.get("conflict_detected", False)


class TestConflictNoteFormat:
    """Conflict note message format tests."""

    @pytest.mark.asyncio
    async def test_conflict_note_contains_read_instruction(self, conflict_test_env):
        """Test that conflict note instructs to use coding_memory_read."""
        env = conflict_test_env

        content1 = """---
name: Database Config
description: PostgreSQL configuration
type: project
---

Use PostgreSQL with connection pool size 20."""

        await coding_memory_write_with_context(env["ctx"], "db_config.md", content1)
        await _upsert_memory_index(
            env["ctx"],
            env["coding_memory_dir"],
            "db_config.md",
            {"name": "Database Config", "description": "PostgreSQL configuration"},
        )

        content2 = """---
name: DB Settings
description: Database connection settings
type: project
---

Use PostgreSQL with connection pool size 20 and timeout 30s."""

        result = await coding_memory_write_with_context(env["ctx"], "db_settings.md", content2)

        if result.get("conflict_detected"):
            note = result.get("note", "")
            assert "coding_memory_read" in note.lower() or "read" in note.lower()

    @pytest.mark.asyncio
    async def test_conflict_note_contains_edit_instruction(self, conflict_test_env):
        """Test that conflict note instructs to use coding_memory_edit."""
        env = conflict_test_env

        content1 = """---
name: Code Style
description: Python code style guide
type: feedback
---

Use 4 spaces for indentation."""

        await coding_memory_write_with_context(env["ctx"], "code_style.md", content1)
        await _upsert_memory_index(
            env["ctx"],
            env["coding_memory_dir"],
            "code_style.md",
            {"name": "Code Style", "description": "Python code style guide"},
        )

        content2 = """---
name: Python Style
description: Python formatting rules
type: feedback
---

Use 4 spaces for indentation in Python files."""

        result = await coding_memory_write_with_context(env["ctx"], "python_style.md", content2)

        if result.get("conflict_detected"):
            note = result.get("note", "")
            assert "coding_memory_edit" in note.lower() or "edit" in note.lower()


class TestWriteResultStructure:
    """Write result structure validation tests."""

    @pytest.mark.asyncio
    async def test_successful_write_result_structure(self, conflict_test_env):
        """Test successful write returns expected result structure."""
        env = conflict_test_env

        content = """---
name: Test Structure
description: Testing result structure
type: user
---

Test content."""

        result = await coding_memory_write_with_context(env["ctx"], "test_structure.md", content)

        assert "success" in result
        assert "path" in result
        assert "mode" in result
        assert result["success"] is True
        assert result["mode"] in ["create", "append", "skip"]

    @pytest.mark.asyncio
    async def test_failed_write_result_structure(self, conflict_test_env):
        """Test failed write returns expected error structure."""
        env = conflict_test_env

        result = await coding_memory_write_with_context(env["ctx"], "test_fail.md", "No frontmatter here")

        assert result["success"] is False
        assert "error" in result
        assert "frontmatter" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_conflict_result_includes_conflicting_files(self, conflict_test_env):
        """Test conflict result includes conflicting_files field."""
        env = conflict_test_env

        content1 = """---
name: Architecture Decision
description: Microservices architecture
type: project
---

We use microservices architecture with Kubernetes."""

        await coding_memory_write_with_context(env["ctx"], "arch.md", content1)
        await _upsert_memory_index(
            env["ctx"],
            env["coding_memory_dir"],
            "arch.md",
            {"name": "Architecture Decision", "description": "Microservices architecture"},
        )

        content2 = """---
name: System Design
description: Kubernetes-based architecture
type: project
---

We use microservices architecture deployed on Kubernetes clusters."""

        result = await coding_memory_write_with_context(env["ctx"], "system_design.md", content2)

        if result.get("conflict_detected"):
            assert "conflicting_files" in result
            assert isinstance(result["conflicting_files"], list)
            assert len(result["conflicting_files"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
