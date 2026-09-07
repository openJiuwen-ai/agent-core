"""The server-selected optimizer reaches every model-backed paper module."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.manager import ManagerRuntime
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.agent import ReflectionAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider import orchestrator as module


@pytest.mark.asyncio
async def test_orchestrator_passes_optimizer_to_all_modules(tmp_path, monkeypatch):
    model = object()
    monkeypatch.setenv("API_BASE", "https://example.com")
    monkeypatch.setenv("MODEL_NAME", "your-model-name")
    monkeypatch.setattr(module, "set_project_root", lambda path: None)
    monkeypatch.setattr(module, "load_project_dotenv", lambda: None)
    captured = []

    def runtime(config, **kwargs):
        instance = ManagerRuntime(config, **kwargs)
        captured.append(instance)
        instance.arun = AsyncMock(return_value="done")
        return instance

    monkeypatch.setattr(module, "ManagerRuntime", runtime)
    orchestrator = module.PaperTreeOrchestrator(
        task_id="model", run_dir=str(tmp_path), max_iterations=1,
        optimization_instruction=None, artifact_path=None, model=model,
    )
    orchestrator.config = {"manager": {"modules": {"reflection": True}}}
    seed = SimpleNamespace(run_id="model-r1", topic="test", research_paths=[], objective="test", constraints=[])
    assert await orchestrator._run_manager(seed) == "done"
    instance = captured[0]
    assert instance.manager._injected_model is model
    for name in ("experiment_design", "code_implementation", "reflection", "reporting"):
        assert instance.registry.get(name).agent._injected_model is model
    assert instance.registry.get("topic_survey").agent._model is model


def test_reflection_build_uses_injected_model_without_credentials(tmp_path, monkeypatch):
    import openjiuwen.core.foundation.llm as llm
    import openjiuwen.harness as harness

    model = object()
    def unexpected(*args, **kwargs):
        pytest.fail("injected model must bypass config/environment resolution")
    monkeypatch.setattr(llm, "init_model", unexpected)
    monkeypatch.setattr(harness, "create_deep_agent", lambda supplied, **kwargs: supplied)
    assert ReflectionAgent({}, model=model)._build_reflection_agent(tmp_path) is model
