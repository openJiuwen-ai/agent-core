"""The server-selected optimizer reaches every model-backed paper module."""

import asyncio
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
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.provider import (
    PaperArtifactProviderImpl,
)


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
    seed = SimpleNamespace(
        run_id="model-r1",
        topic="test",
        research_paths=["input/paper_context.md"],
        objective="test",
        constraints=[],
        initial_prompt="uploaded baseline context",
        task_mode="modify_paper",
    )
    assert await orchestrator._run_manager(seed) == "done"
    instance = captured[0]
    assert instance.manager._injected_model is model
    for name in ("experiment_design", "code_implementation", "reflection", "reporting"):
        assert instance.registry.get(name).agent._injected_model is model
    assert instance.registry.get("topic_survey").agent._model is model
    assert instance.registry.get("code_implementation").artifact_path == str(tmp_path)
    assert instance.registry.get("experiment_execution").artifact_path == str(tmp_path)
    call = instance.arun.await_args.kwargs
    assert call["research_paths"] == ["input/paper_context.md"]
    assert call["initial_prompt"] == "uploaded baseline context"
    assert call["task_mode"] == "modify_paper"


@pytest.mark.asyncio
async def test_orchestrator_stages_uploaded_paper_and_builds_task_context(tmp_path):
    source = tmp_path / "upload" / "paper.pdf"
    source.parent.mkdir()
    source.write_bytes(b"uploaded paper")
    run_dir = tmp_path / "task"
    orchestrator = module.PaperTreeOrchestrator(
        task_id="uploaded-paper",
        run_dir=str(run_dir),
        max_iterations=0,
        optimization_instruction="Improve the evaluation.",
        artifact_path=str(source),
    )

    await orchestrator.start()
    assert orchestrator._task is not None  # noqa: SLF001
    await orchestrator._task  # noqa: SLF001

    snapshot = run_dir / "input" / "paper" / "paper.pdf"
    context = run_dir / "input" / "paper_context.md"
    state = orchestrator.storage.load_task_state()
    assert state is not None
    assert state.artifact_path == str(snapshot)
    assert snapshot.read_bytes() == b"uploaded paper"
    assert context.is_file()
    assert orchestrator.initial_prompt.startswith("TASK MODE: modify_paper")
    assert orchestrator.initial_research_paths == [
        "input/paper_context.md",
        "input/paper/paper.pdf",
    ]
    assert "input/paper/paper.pdf" in context.read_text(encoding="utf-8")

    seed = module.build_node_seed(
        task_id="uploaded-paper",
        round_index=1,
        optimization_instruction="Improve the evaluation.",
        retry_reason=None,
        parent_run_id=None,
        initial_research_paths=orchestrator.initial_research_paths,
        initial_prompt=orchestrator.initial_prompt,
        task_mode="modify_paper",
    )
    assert seed.task_mode == "modify_paper"
    assert seed.initial_prompt == orchestrator.initial_prompt
    assert seed.research_paths == orchestrator.initial_research_paths


@pytest.mark.asyncio
async def test_orchestrator_pause_cancels_inflight_manager_and_persists_paused(
    tmp_path, monkeypatch
):
    started = asyncio.Event()
    events = []

    class Runtime:
        def __init__(self, config, **kwargs):
            del config, kwargs

        async def arun(self, **kwargs):
            del kwargs
            started.set()
            await asyncio.sleep(60)

    monkeypatch.setattr(module, "ManagerRuntime", Runtime)
    monkeypatch.setattr(module, "set_project_root", lambda path: None)
    monkeypatch.setattr(module, "load_project_dotenv", lambda: None)

    async def on_event(event):
        events.append(event)

    orchestrator = module.PaperTreeOrchestrator(
        task_id="pause-paper",
        run_dir=str(tmp_path),
        max_iterations=1,
        optimization_instruction="improve the paper",
        artifact_path=None,
        on_event=on_event,
    )
    await orchestrator.start()
    await started.wait()

    paused = await orchestrator.pause()

    assert paused.status == "paused"
    persisted = orchestrator.storage.load_task_state()
    assert persisted is not None
    assert persisted.status == "paused"
    assert orchestrator._task is not None  # noqa: SLF001 - lifecycle assertion
    assert orchestrator._task.cancelled()  # noqa: SLF001 - lifecycle assertion
    assert [event.status for event in events if isinstance(event, module.EventStatus)] == [
        "running",
        "paused",
    ]


def test_paper_provider_supports_pause_without_advertising_resume():
    provider = PaperArtifactProviderImpl()

    assert provider.supports_pause is True
    assert getattr(provider, "supports_resume", False) is False


@pytest.mark.asyncio
async def test_paper_provider_pause_delegates_to_live_orchestrator():
    provider = PaperArtifactProviderImpl()
    orchestrator = SimpleNamespace(
        pause=AsyncMock(return_value=SimpleNamespace(status="paused", best_node_id="node-1"))
    )
    provider._orchestrators["pause-provider"] = orchestrator  # noqa: SLF001 - provider seam test

    result = await provider.pause("pause-provider")

    assert result.status == "paused"
    assert result.final_node_id == "node-1"
    orchestrator.pause.assert_awaited_once_with(on_event=None)


@pytest.mark.parametrize("web_proxy", [None, "http://proxy.example.test:7890"])
def test_orchestrator_uses_global_search_scope_with_or_without_proxy(
    tmp_path, monkeypatch, web_proxy
):
    monkeypatch.setattr(module, "set_project_root", lambda path: None)
    orchestrator = module.PaperTreeOrchestrator(
        task_id=f"scope-{bool(web_proxy)}",
        run_dir=str(tmp_path),
        max_iterations=1,
        optimization_instruction=None,
        artifact_path=None,
        web_proxy=web_proxy,
    )

    assert orchestrator.config["topic_survey"]["search_scope"] == "global"
    assert orchestrator.config["topic_survey"]["web_proxy"] == web_proxy


@pytest.mark.parametrize("web_proxy", [None, "http://proxy.example.test:7890"])
def test_orchestrator_uses_global_search_scope_with_or_without_proxy(
    tmp_path, monkeypatch, web_proxy
):
    monkeypatch.setattr(module, "set_project_root", lambda path: None)
    orchestrator = module.PaperTreeOrchestrator(
        task_id=f"scope-{bool(web_proxy)}",
        run_dir=str(tmp_path),
        max_iterations=1,
        optimization_instruction=None,
        artifact_path=None,
        web_proxy=web_proxy,
    )

    assert orchestrator.config["topic_survey"]["search_scope"] == "global"
    assert orchestrator.config["topic_survey"]["web_proxy"] == web_proxy


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
