"""The server-selected optimizer reaches every model-backed paper module."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.manager import ManagerRuntime
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.agent import (
    CodeImplementationAgent,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.agent import (
    _variant_env,
)
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
        optimization_instruction=None, artifact_path=str(tmp_path), model=model,
    )
    orchestrator.config = {"manager": {"modules": {"reflection": True}}}
    seed = SimpleNamespace(run_id="model-r1", topic="test", research_paths=[], objective="test", constraints=[])
    assert await orchestrator._run_manager(seed) == "done"
    instance = captured[0]
    assert instance.manager._injected_model is model
    for name in ("experiment_design", "code_implementation", "reflection", "reporting"):
        assert instance.registry.get(name).agent._injected_model is model
    assert instance.registry.get("topic_survey").agent._model is model
    assert instance.registry.get("code_implementation").artifact_path == str(tmp_path)
    assert instance.registry.get("experiment_execution").artifact_path == str(tmp_path)


def test_variant_env_pins_uploaded_paper(tmp_path, monkeypatch):
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    paper_file = paper_dir / "main.tex"
    paper_file.write_text("paper", encoding="utf-8")
    task_spec = paper_dir / "task_spec.json"
    task_spec.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PAPER_FILE", "stale-paper.tex")
    monkeypatch.setenv("SUPPLIED_PAPER_DIR", "stale-paper-dir")
    monkeypatch.setenv("ARTIFACT_PATH", "stale-artifact")
    monkeypatch.setenv("TASK_SPEC_FILE", "stale-task-spec.json")

    directory_env = _variant_env(str(paper_dir))
    assert directory_env["ARTIFACT_PATH"] == str(paper_dir.resolve())
    assert directory_env["SUPPLIED_PAPER_DIR"] == str(paper_dir.resolve())
    assert directory_env["PAPER_FILE"] == str(paper_file.resolve())
    assert directory_env["TASK_SPEC_FILE"] == str(task_spec.resolve())

    file_env = _variant_env(str(paper_file))
    assert file_env["ARTIFACT_PATH"] == str(paper_file.resolve())
    assert file_env["PAPER_FILE"] == str(paper_file.resolve())
    assert "SUPPLIED_PAPER_DIR" not in file_env
    assert file_env["TASK_SPEC_FILE"] == str(task_spec.resolve())

    case_root = tmp_path / "case"
    nested_paper = case_root / "paper"
    nested_paper.mkdir(parents=True)
    nested_main = nested_paper / "main.tex"
    nested_main.write_text("paper", encoding="utf-8")
    nested_spec = nested_paper / "task_spec.json"
    nested_spec.write_text("{}", encoding="utf-8")
    nested_env = _variant_env(str(case_root))
    assert nested_env["PAPER_FILE"] == str(nested_main.resolve())
    assert nested_env["TASK_SPEC_FILE"] == str(nested_spec.resolve())

    no_spec_dir = tmp_path / "no-spec"
    no_spec_dir.mkdir()
    assert "TASK_SPEC_FILE" not in _variant_env(str(no_spec_dir))
    for name in ("ARTIFACT_PATH", "SUPPLIED_PAPER_DIR", "PAPER_FILE", "TASK_SPEC_FILE"):
        assert name not in _variant_env()


def test_code_agent_stages_file_artifact_without_losing_its_extension(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"paper")
    workspace = tmp_path / "agent_workspace"

    staged = CodeImplementationAgent._stage_artifact_input(str(source), workspace)

    assert staged == workspace / "artifact_path"
    assert (staged / "paper.pdf").read_bytes() == b"paper"


def test_code_agent_stages_directory_artifact_contents(tmp_path):
    source = tmp_path / "paper"
    source.mkdir()
    (source / "paper_facts.json").write_text("{}", encoding="utf-8")
    workspace = tmp_path / "agent_workspace"

    staged = CodeImplementationAgent._stage_artifact_input(str(source), workspace)

    assert staged == workspace / "artifact_path"
    assert (staged / "paper_facts.json").read_text(encoding="utf-8") == "{}"


@pytest.mark.asyncio
async def test_orchestrator_bridges_model_to_legacy_environment(tmp_path, monkeypatch):
    model = SimpleNamespace(
        model_client_config=SimpleNamespace(
            client_provider=SimpleNamespace(value="DeepSeek"),
            api_base="https://model.test/v1",
            api_key="test-key",
            timeout=17,
        ),
        model_config=SimpleNamespace(model_name="deepseek-v4-flash"),
    )
    monkeypatch.setenv("API_BASE", "https://example.com/compatible-mode/v1")
    monkeypatch.setenv("MODEL_NAME", "your-model-name")
    monkeypatch.setattr(module, "set_project_root", lambda path: None)
    monkeypatch.setattr(module, "load_project_dotenv", lambda: None)
    captured = {}

    class Runtime:
        def __init__(self, config, **kwargs):
            captured["config"] = config
            del kwargs

        async def arun(self, **kwargs):
            del kwargs
            captured["environment"] = {
                key: os.environ.get(key)
                for key in ("API_KEY", "API_BASE", "MODEL_PROVIDER", "MODEL_NAME", "MODEL_TIMEOUT")
            }
            return "done"

    monkeypatch.setattr(module, "ManagerRuntime", Runtime)
    orchestrator = module.PaperTreeOrchestrator(
        task_id="model-env",
        run_dir=str(tmp_path),
        max_iterations=1,
        optimization_instruction=None,
        artifact_path=None,
        model=model,
    )
    orchestrator.config = {"openjiuwen": {"model": "placeholder"}}
    seed = SimpleNamespace(
        run_id="model-env-r1",
        topic="test",
        research_paths=[],
        objective="test",
        constraints=[],
    )

    assert await orchestrator._run_manager(seed) == "done"
    assert captured["config"]["openjiuwen"] == {
        "model": "deepseek-v4-flash",
        "provider": "DeepSeek",
        "base_url": "https://model.test/v1",
        "timeout": 17,
    }
    assert captured["environment"] == {
        "API_KEY": "test-key",
        "API_BASE": "https://model.test/v1",
        "MODEL_PROVIDER": "DeepSeek",
        "MODEL_NAME": "deepseek-v4-flash",
        "MODEL_TIMEOUT": "17",
    }
    assert os.environ["API_BASE"] == "https://example.com/compatible-mode/v1"
    assert os.environ["MODEL_NAME"] == "your-model-name"


def test_reflection_build_uses_injected_model_without_credentials(tmp_path, monkeypatch):
    import openjiuwen.core.foundation.llm as llm
    import openjiuwen.harness as harness

    model = object()
    def unexpected(*args, **kwargs):
        pytest.fail("injected model must bypass config/environment resolution")
    monkeypatch.setattr(llm, "init_model", unexpected)
    monkeypatch.setattr(harness, "create_deep_agent", lambda supplied, **kwargs: supplied)
    assert ReflectionAgent({}, model=model)._build_reflection_agent(tmp_path) is model
