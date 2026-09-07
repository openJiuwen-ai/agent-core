import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline import manager as manager_module
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import OriginalTask, SubtaskContract


@pytest.mark.asyncio
@pytest.mark.parametrize("module,mode", [
    ("topic_survey", "run"), ("experiment_design", "create"),
    ("code_implementation", "run"), ("experiment_execution", "run"),
    ("reflection", "run"), ("reporting", "run"),
])
async def test_stage_is_observable_before_module_finishes(tmp_path, monkeypatch, module, mode):
    stages = []

    async def on_stage(stage):
        stages.append(stage)

    async def ainvoke(*args, **kwargs):
        assert stages == [module]
        raise asyncio.CancelledError

    runtime = object.__new__(manager_module.ManagerRuntime)
    runtime.on_stage = on_stage
    runtime.registry = SimpleNamespace(get=lambda name: SimpleNamespace(ainvoke=ainvoke))
    monkeypatch.setattr(manager_module, "module_attempt_dir", lambda *args: tmp_path)
    monkeypatch.setattr(manager_module, "to_project_relative", lambda path: str(path))
    monkeypatch.setattr(manager_module, "append_event", lambda *args: None)
    state = manager_module.build_initial_state(OriginalTask(topic="test"), config={})
    contract = SubtaskContract(module=module, mode=mode, goal="test", acceptance_criteria=["test"])
    with pytest.raises(asyncio.CancelledError):
        await runtime._execute_contract(state, contract, 1)
    assert stages == [module]
