# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import re
import uuid

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, SysOperation, SysOperationCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.prompts.tools import _REGISTRY, register_tool_provider
from openjiuwen.harness.prompts.tools.todo import TodoCreateMetadataProvider
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.tools.todo import TodoCreateTool, TodoListTool
from openjiuwen.harness.workspace.workspace import Workspace


@pytest.fixture
def todo_env(tmp_path):
    operation = SysOperation(SysOperationCard(
        id=f"todo-persistence-{uuid.uuid4().hex}",
        work_config=LocalWorkConfig(sandbox_root=[str(tmp_path)]),
    ))
    session = Session(session_id="status-validation")
    return operation, session, tmp_path


def build_todo_items(*statuses):
    result = []
    for index, status in enumerate(statuses):
        item = {"id": f"task{index}", "content": f"Task {index}", "activeForm": f"Doing task {index}",
                "description": f"Verify result {index}"}
        if status != "omitted":
            item["status"] = status
        result.append(item)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("statuses, expected", [
    (("omitted", "omitted"), ["in_progress", "pending"]),
    (("completed", "omitted"), ["completed", "pending"]),
    (("omitted", "in_progress"), ["pending", "in_progress"]),
    (("completed", "in_progress", "pending"), ["completed", "in_progress", "pending"]),
    (("completed", "cancelled", "in_progress", "pending"), ["completed", "cancelled", "in_progress", "pending"]),
    (("completed", "completed"), ["completed", "completed"]),
    (("cancelled", "cancelled"), ["cancelled", "cancelled"]),
    (("completed", "cancelled"), ["completed", "cancelled"]),
    (("pending", "pending"), ["pending", "pending"]),
])
async def test_create_status_contract_and_receipt(todo_env, required, statuses, expected):
    operation, session, root = todo_env
    tool = TodoCreateTool(operation, str(root), require_explicit_status=required)
    path = root / session.get_session_id() / "todo.json"
    if required and "omitted" in statuses:
        with pytest.raises(Exception, match="Missing status.*task.*Allowed:.*Example task:.*No changes were written"):
            await tool.invoke({"tasks": build_todo_items(*statuses)}, session=session)
        assert not path.exists()
        return
    expected_by_id = {f"task{i}": status for i, status in enumerate(expected)}
    # Saved lists are stably ordered: completed/cancelled, in_progress, pending
    rank = {"completed": 0, "cancelled": 0, "in_progress": 1, "pending": 2}
    expected_order = sorted(expected_by_id, key=lambda task_id: rank[expected_by_id[task_id]])
    for _ in range(2):
        result = await tool.invoke({"tasks": build_todo_items(*statuses)}, session=session)
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert {item["id"]: item["status"] for item in saved} == expected_by_id
        assert [item["id"] for item in saved] == expected_order
        message = result["message"]
        assert re.findall(r"task_id: (\S+) , content: Task \d+", message) == expected_order
        if "in_progress" in expected:
            current = expected.index("in_progress")
            assert message.endswith(f"Recorded in progress: 'Task {current}' (task_id: task{current}). Next step: start this task now.")
        elif "pending" in expected:
            assert message.endswith("Choose a pending task and set it to in_progress before starting.")
        else:
            assert message.endswith("All items are closed (completed or cancelled). Report actual results.")
        listed = await TodoListTool(operation, str(root)).invoke({}, session=session)
        fields = ("id", "content", "status", "depends_on")
        assert listed["tasks"] == [{key: item[key] for key in fields} for item in saved]


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("invalid", ["bad-status", "", None, "duplicate", "two-active", "late-invalid"])
@pytest.mark.parametrize("existing", [False, True])
async def test_invalid_create_never_writes(todo_env, required, invalid, existing):
    operation, session, root = todo_env
    tool = TodoCreateTool(operation, str(root), require_explicit_status=required)
    path = root / session.get_session_id() / "todo.json"
    if existing:
        await tool.invoke({"tasks": build_todo_items("completed", "pending")}, session=session)
    original = path.read_bytes() if existing else None
    data = build_todo_items("completed", "pending")
    if invalid == "duplicate":
        data[1]["id"] = data[0]["id"]
    elif invalid == "two-active":
        for item in data:
            item["status"] = "in_progress"
    elif invalid == "late-invalid":
        del data[1]["description"]
    else:
        data[1]["status"] = invalid
    with pytest.raises(BaseError) as error:
        await tool.invoke({"tasks": data}, session=session)
    assert error.value.status is StatusCode.TOOL_TODOS_INVOKE_FAILED
    assert isinstance(error.value.__cause__, BaseError)
    assert error.value.__cause__.status is StatusCode.TOOL_TODOS_VALIDATION_INVALID
    assert (path.read_bytes() if path.exists() else None) == original


@pytest.mark.asyncio
async def test_missing_status_preserves_existing_and_reports_all_ids(todo_env):
    operation, session, root = todo_env
    tool = TodoCreateTool(operation, str(root), require_explicit_status=True)
    await tool.invoke({"tasks": build_todo_items("completed", "pending")}, session=session)
    path = root / session.get_session_id() / "todo.json"
    original = path.read_bytes()
    with pytest.raises(Exception, match="Missing status for: task0, task1"):
        await tool.invoke({"tasks": build_todo_items("omitted", "omitted")}, session=session)
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_missing_id_keeps_explicit_status(todo_env):
    operation, session, root = todo_env
    tool = TodoCreateTool(operation, str(root), require_explicit_status=True)
    data = build_todo_items("completed")
    del data[0]["id"]
    await tool.invoke({"tasks": data}, session=session)
    [saved] = (await TodoListTool(operation, str(root)).invoke({}, session=session))["tasks"]
    assert str(uuid.UUID(saved["id"])) == saved["id"]
    assert saved["status"] == "completed"


@pytest.mark.asyncio
async def test_missing_status_identifies_long_ids(todo_env):
    operation, session, root = todo_env
    tool = TodoCreateTool(operation, str(root), require_explicit_status=True)
    entries = build_todo_items("omitted", "omitted")
    for entry in entries:
        entry["id"] = "stage-" * 16 + entry["id"]
    with pytest.raises(BaseError) as exc:
        await tool.invoke({"tasks": entries}, session=session)
    assert all(entry["id"] in str(exc.value) for entry in entries)
    assert not (root / session.get_session_id() / "todo.json").exists()


@pytest.mark.asyncio
async def test_list_empty_and_missing_file(todo_env):
    operation, session, root = todo_env
    tool = TodoListTool(operation, str(root))
    with pytest.raises(Exception, match="Todo file not found"):
        await tool.invoke({}, session=session)
    await tool.save_todos(session.get_session_id(), [])
    assert await tool.invoke({}, session=session) == {"tasks": []}


@pytest.mark.asyncio
async def test_real_io_failure_is_not_success(todo_env):
    operation, session, root = todo_env
    tool = TodoCreateTool(operation, str(root), require_explicit_status=True)
    path = root / session.get_session_id() / "todo.json"
    path.mkdir(parents=True)
    with pytest.raises(BaseError) as error:
        await tool.invoke({"tasks": build_todo_items("completed")}, session=session)
    assert error.value.status is StatusCode.TOOL_TODOS_INVOKE_FAILED
    assert isinstance(error.value.__cause__, BaseError)
    assert error.value.__cause__.status is StatusCode.TOOL_TODOS_SAVE_FAILED
    assert path.is_dir()


@pytest.mark.parametrize("language", ["cn", "en"])
def test_schema_instances_and_subagent_registration(todo_env, language):
    operation, _, root = todo_env
    public = TodoCreateTool(operation, str(root), language=language)
    required = TodoCreateTool(operation, str(root), language=language, require_explicit_status=True)
    for tool, expected in [(public, False), (required, True), (public, False)]:
        item = tool.card.input_params["properties"]["tasks"]["items"]
        assert ("status" in item["required"]) is expected
        assert item["properties"]["status"]["enum"] == ["pending", "in_progress", "completed", "cancelled"]
    assert ("automatically" if language == "en" else "自动") not in required.card.description
    rail = TaskPlanningRail(require_explicit_status=True)
    for current in [rail, rail.clone_for_subagent()]:
        agent = DeepAgent(AgentCard(id=f"todo-{uuid.uuid4().hex}", name="todo", description="status contract"))
        agent.configure(DeepAgentConfig(workspace=Workspace(root_path=str(root)), sys_operation=operation))
        current.init(agent)
        create = next(tool for tool in current.tools if isinstance(tool, TodoCreateTool))
        assert create.require_explicit_status
        assert "status" in create.card.input_params["properties"]["tasks"]["items"]["required"]
        current.init(agent)
        with pytest.raises(BaseError, match="configuration differs") as error:
            other = TaskPlanningRail(require_explicit_status=False)
            other.init(agent)
        assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
        assert error.value.code == StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR.code
        current.uninit(agent)


def test_todo_create_preserves_registered_metadata(todo_env):
    class WorkspaceTodoMetadata(TodoCreateMetadataProvider):
        def get_description(self, language="cn"):
            instruction = {"cn": "按工作区约定填写任务名称。", "en": "Use workspace naming conventions."}
            return super().get_description(language) + instruction[language]

        def get_input_params(self, language="cn"):
            schema = super().get_input_params(language)
            schema["properties"]["tasks"]["minItems"] = 1
            return schema

    operation, _, root = todo_env
    provider = WorkspaceTodoMetadata()
    original = _REGISTRY[provider.get_name()]
    register_tool_provider(provider)
    try:
        for language in ("cn", "en"):
            card = TodoCreateTool(operation, str(root), language=language).card
            assert card.description == provider.get_description(language)
            assert card.input_params == provider.get_input_params(language)
            strict = TodoCreateTool(operation, str(root), language=language, require_explicit_status=True).card
            assert strict.description.endswith({"cn": "按工作区约定填写任务名称。", "en": "Use workspace naming conventions."}[language])
            assert strict.input_params["properties"]["tasks"]["minItems"] == 1
            assert "status" in strict.input_params["properties"]["tasks"]["items"]["required"]
            assert "status" not in card.input_params["properties"]["tasks"]["items"]["required"]
    finally:
        register_tool_provider(original)


def test_reuse_checks_the_executing_agents_instance(todo_env):
    operation, _, root = todo_env
    card = AgentCard(id=uuid.uuid4().hex, name="todo", description="shared agent id")
    first, second = DeepAgent(card), DeepAgent(card.model_copy())
    for agent in (first, second):
        agent.configure(DeepAgentConfig(workspace=Workspace(root_path=str(root)), sys_operation=operation))
    loose, strict = TaskPlanningRail(), TaskPlanningRail(require_explicit_status=True)
    loose.init(first)
    strict.init(second)
    try:
        with pytest.raises(BaseError, match="configuration differs"):
            TaskPlanningRail(require_explicit_status=True).init(first)
        loose.init(first)
        assert not next(tool for tool in loose.tools if isinstance(tool, TodoCreateTool)).require_explicit_status
    finally:
        strict.uninit(second)
        loose.uninit(first)
