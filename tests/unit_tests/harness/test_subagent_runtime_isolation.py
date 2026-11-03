"""Runtime resources are private to each spawned subagent; cards stay stable."""
import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail


def parent_agent(tmp_path):
    model = Model(model_client_config=ModelClientConfig(client_provider="OpenAI", api_key="test", api_base="http://test.invalid/v1"),
                  model_config=ModelRequestConfig(model="test"))
    return create_deep_agent(model, workspace=str(tmp_path), add_general_purpose_agent=True,
                             auto_create_workspace=False, enable_read_image_multimodal=False,
                             rails=[SysOperationRail(), TaskPlanningRail()])


@pytest.mark.asyncio
async def test_siblings_keep_tools_and_rail_bindings_after_peer_cleanup(tmp_path):
    parent = parent_agent(tmp_path)
    await parent._ensure_initialized()
    first = parent.create_subagent("general-purpose", "child-A")
    second = parent.create_subagent("general-purpose", "child-B")
    await first._ensure_initialized()
    await second._ensure_initialized()
    parent_fs = parent.find_rails_by_type((SysOperationRail,))[0]
    first_fs = first.find_rails_by_type((SysOperationRail,))[0]
    second_fs = second.find_rails_by_type((SysOperationRail,))[0]
    try:
        assert first_fs.workspace is first.deep_config.workspace
        assert second_fs.workspace is second.deep_config.workspace
        assert parent_fs.workspace is parent.deep_config.workspace
        assert first.deep_config.sys_operation is not second.deep_config.sys_operation
        assert first.ability_manager.get("write_file").id != second.ability_manager.get("write_file").id
        assert first.card.id == second.card.id  # persistence identity is unchanged
        first_fs.uninit(first)
        from openjiuwen.core.runner import Runner
        peer_card = second.ability_manager.get("write_file")
        assert Runner.resource_mgr.get_tool(peer_card.id) is not None
        parent_todo = parent.find_rails_by_type((TaskPlanningRail,))[0]
        first_todo = first.find_rails_by_type((TaskPlanningRail,))[0]
        second_todo = second.find_rails_by_type((TaskPlanningRail,))[0]
        first_todo.uninit(first)
        assert parent_todo.system_prompt_builder is parent.system_prompt_builder
        assert second_todo.system_prompt_builder is second.system_prompt_builder
        assert Runner.resource_mgr.get_tool(second.ability_manager.get("todo_list").id) is not None
    finally:
        for agent in (first, second, parent):
            agent.ability_manager.teardown_tools()


def test_same_subsession_rebuild_uses_same_runtime_owner(tmp_path):
    parent = parent_agent(tmp_path)
    first = parent.create_subagent("general-purpose", "approval-session")
    resumed = parent.create_subagent("general-purpose", "approval-session")
    sibling = parent.create_subagent("general-purpose", "other-session")
    assert first.deep_config.tool_owner_id
    assert first.deep_config.tool_owner_id == resumed.deep_config.tool_owner_id
    assert first.deep_config.tool_owner_id != sibling.deep_config.tool_owner_id
    assert first.deep_config.sys_operation is resumed.deep_config.sys_operation

@pytest.mark.asyncio
async def test_terminal_cleanup_releases_only_this_child_runtime(tmp_path):
    from openjiuwen.core.runner import Runner
    parent = parent_agent(tmp_path)
    first = parent.create_subagent("general-purpose", "cleanup-A")
    second = parent.create_subagent("general-purpose", "cleanup-B")
    await first._ensure_initialized()
    await second._ensure_initialized()
    second_op = second.deep_config.sys_operation
    first_tool_id = first.ability_manager.get("write_file").id
    second_tool_id = second.ability_manager.get("write_file").id
    await first.cleanup_task_resources()
    assert Runner.resource_mgr.get_tool(first_tool_id) is None
    assert Runner.resource_mgr.get_tool(second_tool_id) is not None
    assert Runner.resource_mgr.get_sys_operation("general-purpose_cleanup-A") is None
    assert Runner.resource_mgr.get_sys_operation("general-purpose_cleanup-B") is second_op
    await second.cleanup_task_resources()


@pytest.mark.asyncio
async def test_independent_verifiers_override_shared_owner_and_clean_separately(tmp_path):
    from openjiuwen.harness.subagents.verification_agent import build_verification_agent_config
    parent = parent_agent(tmp_path)
    spec = build_verification_agent_config(model=parent.deep_config.model)
    spec.factory_kwargs = {"tool_owner_id": "shared-owner"}
    parent.deep_config.subagents = [spec]
    first = parent.create_subagent("verification_agent", "verify-A", isolated=True)
    second = parent.create_subagent("verification_agent", "verify-B", isolated=True)
    assert first is not second
    assert first.deep_config.tool_owner_id == "verify-A"
    assert second.deep_config.tool_owner_id == "verify-B"
    assert spec.factory_kwargs == {"tool_owner_id": "shared-owner"}
    await first._ensure_initialized()
    await second._ensure_initialized()
    from openjiuwen.core.runner import Runner
    peer_tool = second.ability_manager.get("read_file")
    await first.cleanup_task_resources()
    assert Runner.resource_mgr.get_tool(peer_tool.id) is not None
    await second.cleanup_task_resources()


@pytest.mark.asyncio
async def test_independent_browsers_own_profiles_ports_and_driver_shutdown(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from openjiuwen.harness.subagents.browser_agent import build_browser_agent_config
    from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail
    from openjiuwen.harness.tools.browser_move.playwright_runtime.service import BrowserService
    monkeypatch.setenv("BROWSER_PROFILE_STORE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("PLAYWRIGHT_RUNTIME_MCP_CWD", str(tmp_path))
    monkeypatch.setenv("BROWSER_MANAGED_ARGS",
                       "--headless=new --user-data-dir=C:/shared --remote-debugging-port=9333")
    parent = parent_agent(tmp_path)
    spec = build_browser_agent_config(parent.deep_config.model)
    parent.deep_config.subagents = [spec]
    first = parent.create_subagent("browser_agent", "browser-A", isolated=True)
    second = parent.create_subagent("browser_agent", "browser-B", isolated=True)
    a = first.find_rails_by_type((BrowserRuntimeRail,))[0]._runtime.service
    b = second.find_rails_by_type((BrowserRuntimeRail,))[0]._runtime.service
    assert a.lifecycle_identity != b.lifecycle_identity
    ports = iter([41001, 41002])
    monkeypatch.setattr(BrowserService, "_allocate_free_port", staticmethod(lambda: next(ports)))
    pa, pb = a._build_managed_profile(), b._build_managed_profile()
    assert pa.user_data_dir != pb.user_data_dir and pa.debug_port != pb.debug_port
    assert pa.extra_args == pb.extra_args == ["--headless=new"]
    stopped = []
    a._managed_driver = SimpleNamespace(stop=lambda: stopped.append("A"))
    b._managed_driver = SimpleNamespace(stop=lambda: stopped.append("B"))
    await first.prepare_task_resources()
    await second.prepare_task_resources()
    await first.cleanup_task_resources()
    assert stopped == ["A"] and b._managed_driver is not None
    await second.cleanup_task_resources()
    assert stopped == ["A", "B"]
