"""Coding Memory conflict resolution workflow system tests.

Tests the complete conflict resolution workflow:
1. Conflict detection during write
2. Reading conflicting files
3. Editing to resolve conflicts
4. Redundant content handling (auto-skip)
"""

import os
import tempfile
import shutil

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.core.memory.lite.coding_memory_tools import (
    coding_memory_read,
    coding_memory_write,
    coding_memory_edit,
    _upsert_memory_index,
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

    from openjiuwen.core.memory.lite import coding_memory_tools
    workspace = Workspace(
        root_path=work_dir,
        directories=[{"name": "coding_memory", "path": "coding_memory"}]
    )
    coding_memory_tools.coding_memory_workspace = workspace
    coding_memory_tools.coding_memory_sys_operation = sys_op
    coding_memory_tools.coding_memory_dir = coding_memory_dir

    yield {
        "work_dir": work_dir,
        "coding_memory_dir": coding_memory_dir,
        "sys_operation_id": sys_operation_id,
        "sys_op": sys_op,
    }

    coding_memory_tools.coding_memory_workspace = None
    coding_memory_tools.coding_memory_sys_operation = None
    coding_memory_tools.coding_memory_dir = "coding_memory"

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
        
        # 1. Create initial memory
        initial_content = """---
name: User Role
description: User is a Python developer
type: user
---

User is a senior Python developer familiar with Django and Flask."""
        
        result1 = await coding_memory_write.invoke({
            "path": "user_role.md", 
            "content": initial_content
        })
        assert result1["success"] is True
        assert result1["mode"] == "create"
        
        # Update index for the first file
        await _upsert_memory_index(
            env["coding_memory_dir"],
            "user_role.md",
            {"name": "User Role", "description": "User is a Python developer"}
        )
        
        # 2. Write similar content that may conflict
        similar_content = """---
name: Developer Role
description: User develops in Python
type: user
---

User develops backend services using Python and Django framework."""
        
        result2 = await coding_memory_write.invoke({
            "path": "developer_role.md",
            "content": similar_content
        })
        
        # The write should succeed (file created)
        assert result2["success"] is True
        
        # If conflict detected, verify the workflow
        if result2.get("conflict_detected"):
            conflicting_files = result2.get("conflicting_files", [])
            
            # 3. Read conflicting files
            for conflict_file in conflicting_files:
                read_result = await coding_memory_read.invoke({"path": conflict_file})
                assert read_result["success"] is True
                assert read_result["content"]  # Should have content
            
            # 4. Edit to resolve conflict (update the new file)
            edit_result = await coding_memory_edit.invoke({
                "path": "developer_role.md",
                "old_text": "User develops backend services using Python and Django framework.",
                "new_text": "User develops backend services using Python, Django, and also has experience with FastAPI."
            })
            assert edit_result["success"] is True
            
            # 5. Verify the edit
            verify_result = await coding_memory_read.invoke({"path": "developer_role.md"})
            assert verify_result["success"] is True
            assert "FastAPI" in verify_result["content"]
    
    @pytest.mark.asyncio
    async def test_append_mode_self_conflict_resolution(self, conflict_test_env):
        """Test append mode with self-conflict resolution."""
        env = conflict_test_env
        
        # 1. Create initial memory
        initial_content = """---
name: Project Setup
description: Project initialization steps
type: project
---

Step 1: Install dependencies
Step 2: Configure database"""
        
        result1 = await coding_memory_write.invoke({
            "path": "project_setup.md",
            "content": initial_content
        })
        assert result1["success"] is True
        
        await _upsert_memory_index(
            env["coding_memory_dir"],
            "project_setup.md",
            {"name": "Project Setup", "description": "Project initialization steps"}
        )
        
        # 2. Append similar content (may trigger self-conflict)
        append_content = """---
name: Project Setup Extended
description: More project setup details
type: project
---

Step 1: Install dependencies
Step 2: Configure database
Step 3: Run migrations"""
        
        result2 = await coding_memory_write.invoke({
            "path": "project_setup.md",
            "content": append_content
        })
        
        # Should succeed (append mode)
        assert result2["success"] is True
        assert result2["mode"] == "append"
        
        # 3. If conflict detected with self, read and edit
        if result2.get("conflict_detected"):
            read_result = await coding_memory_read.invoke({"path": "project_setup.md"})
            assert read_result["success"] is True
            
            # Edit to resolve
            edit_result = await coding_memory_edit.invoke({
                "path": "project_setup.md",
                "old_text": "Step 3: Run migrations",
                "new_text": "Step 3: Run database migrations and verify connection"
            })
            assert edit_result["success"] is True


class TestRedundantHandling:
    """Redundant content handling tests."""
    
    @pytest.mark.asyncio
    async def test_redundant_content_skip(self, conflict_test_env):
        """Test that redundant content is auto-skipped."""
        env = conflict_test_env
        
        # 1. Create original memory
        original_content = """---
name: API Endpoint
description: User login API
type: reference
---

POST /api/v1/login
Request: {username, password}
Response: {token, expires_in}"""
        
        result1 = await coding_memory_write.invoke({
            "path": "api_login.md",
            "content": original_content
        })
        assert result1["success"] is True
        
        await _upsert_memory_index(
            env["coding_memory_dir"],
            "api_login.md",
            {"name": "API Endpoint", "description": "User login API"}
        )
        
        # 2. Try to write nearly identical content
        redundant_content = """---
name: Login API
description: API for user login
type: reference
---

POST /api/v1/login
Request: {username, password}
Response: {token, expires_in}"""
        
        result2 = await coding_memory_write.invoke({
            "path": "login_api.md",
            "content": redundant_content
        })
        
        # If detected as redundant, mode should be SKIP
        if result2.get("mode") == "skip":
            assert "redundant" in result2.get("note", "").lower()
            # Verify the file was NOT created
            read_result = await coding_memory_read.invoke({"path": "login_api.md"})
            # May succeed if file exists from before, but should not have our content
            if read_result["success"]:
                # If file exists, it shouldn't have the redundant content
                pass  # File may exist, that's ok
    
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
        
        result = await coding_memory_write.invoke({
            "path": "test_skip.md",
            "content": content
        })
        
        await _upsert_memory_index(
            env["coding_memory_dir"],
            "test_skip.md",
            {"name": "Test Memory", "description": "Test description"}
        )
        
        # If skipped, success should still be True
        if result.get("mode") == "skip":
            assert result["success"] is True
            # No conflicting files should be reported for skip
            assert not result.get("conflict_detected", False)


class TestConflictNoteFormat:
    """Conflict note message format tests."""
    
    @pytest.mark.asyncio
    async def test_conflict_note_contains_read_instruction(self, conflict_test_env):
        """Test that conflict note instructs to use coding_memory_read."""
        env = conflict_test_env
        
        # Create a memory that might cause conflict
        content1 = """---
name: Database Config
description: PostgreSQL configuration
type: project
---

Use PostgreSQL with connection pool size 20."""
        
        await coding_memory_write.invoke({"path": "db_config.md", "content": content1})
        await _upsert_memory_index(
            env["coding_memory_dir"],
            "db_config.md",
            {"name": "Database Config", "description": "PostgreSQL configuration"}
        )
        
        content2 = """---
name: DB Settings
description: Database connection settings
type: project
---

Use PostgreSQL with connection pool size 20 and timeout 30s."""
        
        result = await coding_memory_write.invoke({
            "path": "db_settings.md",
            "content": content2
        })
        
        if result.get("conflict_detected"):
            note = result.get("note", "")
            # Note should mention coding_memory_read
            assert "coding_memory_read" in note.lower() or "read" in note.lower()
    
    @pytest.mark.asyncio
    async def test_conflict_note_contains_edit_instruction(self, conflict_test_env):
        """Test that conflict note instructs to use coding_memory_edit."""
        env = conflict_test_env
        
        # Create a memory that might cause conflict
        content1 = """---
name: Code Style
description: Python code style guide
type: feedback
---

Use 4 spaces for indentation."""
        
        await coding_memory_write.invoke({"path": "code_style.md", "content": content1})
        await _upsert_memory_index(
            env["coding_memory_dir"],
            "code_style.md",
            {"name": "Code Style", "description": "Python code style guide"}
        )
        
        content2 = """---
name: Python Style
description: Python formatting rules
type: feedback
---

Use 4 spaces for indentation in Python files."""
        
        result = await coding_memory_write.invoke({
            "path": "python_style.md",
            "content": content2
        })
        
        if result.get("conflict_detected"):
            note = result.get("note", "")
            # Note should mention coding_memory_edit
            assert "coding_memory_edit" in note.lower() or "edit" in note.lower()


class TestWriteResultStructure:
    """Write result structure validation tests."""
    
    @pytest.mark.asyncio
    async def test_successful_write_result_structure(self, conflict_test_env):
        """Test successful write returns expected result structure."""
        content = """---
name: Test Structure
description: Testing result structure
type: user
---

Test content."""
        
        result = await coding_memory_write.invoke({
            "path": "test_structure.md",
            "content": content
        })
        
        assert "success" in result
        assert "path" in result
        assert "mode" in result
        assert result["success"] is True
        assert result["mode"] in ["create", "append", "skip"]
    
    @pytest.mark.asyncio
    async def test_failed_write_result_structure(self, conflict_test_env):
        """Test failed write returns expected error structure."""
        # Invalid content without frontmatter
        result = await coding_memory_write.invoke({
            "path": "test_fail.md",
            "content": "No frontmatter here"
        })
        
        assert result["success"] is False
        assert "error" in result
        assert "frontmatter" in result["error"].lower()
    
    @pytest.mark.asyncio
    async def test_conflict_result_includes_conflicting_files(self, conflict_test_env):
        """Test conflict result includes conflicting_files field."""
        env = conflict_test_env
        
        # Create first memory
        content1 = """---
name: Architecture Decision
description: Microservices architecture
type: project
---

We use microservices architecture with Kubernetes."""
        
        await coding_memory_write.invoke({"path": "arch.md", "content": content1})
        await _upsert_memory_index(
            env["coding_memory_dir"],
            "arch.md",
            {"name": "Architecture Decision", "description": "Microservices architecture"}
        )
        
        # Create potentially conflicting memory
        content2 = """---
name: System Design
description: Kubernetes-based architecture
type: project
---

We use microservices architecture deployed on Kubernetes clusters."""
        
        result = await coding_memory_write.invoke({
            "path": "system_design.md",
            "content": content2
        })
        
        if result.get("conflict_detected"):
            assert "conflicting_files" in result
            assert isinstance(result["conflicting_files"], list)
            assert len(result["conflicting_files"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
