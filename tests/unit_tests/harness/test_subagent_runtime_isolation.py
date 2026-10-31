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
