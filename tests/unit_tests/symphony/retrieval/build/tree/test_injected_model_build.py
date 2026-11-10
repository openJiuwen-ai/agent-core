import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.symphony.agent.retrieval_toolkit import AgenticSkillRetrievalToolkit, SkillRecord, _index_fingerprint
from openjiuwen.symphony.config import OneShotTreeBuildConfig, RetrievalBuildConfig
from openjiuwen.symphony.model_bridge import _bind_model_to_loop, _model_scope
from openjiuwen.symphony.retrieval.build.io import load_catalog_records, load_tree_preset
from openjiuwen.symphony.retrieval.build.tree.builder import TreeBuilder
from openjiuwen.symphony.retrieval.build.tree.one_shot import OneShotSkill, OneShotSkillTreeBuilder


class RecordingModel:
    def __init__(self, reply=None, *, name="tree-model"):
        self.reply = reply
        self.model_config = SimpleNamespace(model=name)
        self.calls = []
        self.loops = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        self.loops.append(asyncio.get_running_loop())
        if self.reply is not None:
            content = self.reply(kwargs)
            if isinstance(content, Exception):
                raise content
        else:
            names = re.findall(r"^### (.+)$", kwargs["messages"][-1]["content"], re.MULTILINE)
            content = json.dumps({"skills/documents": names})
        return SimpleNamespace(
            content=content, finish_reason="stop",
            usage_metadata=SimpleNamespace(input_tokens=10, output_tokens=4, total_tokens=14),
        )


def _policy(**kwargs):
    return RetrievalBuildConfig(output={"show_tree": False, "cache_metrics_enabled": False}, **kwargs)


async def _finished(toolkit):
    for _ in range(300):
        state = toolkit.load_index_status()
        if state["status"] not in {"running", "queued"}:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("build did not finish")


def test_one_shot_repair_and_retries_share_one_loop_and_account_usage():
    replies = iter([TimeoutError("temporary timeout"), '{"skills/documents": ["Alpha"]}',
                    '{"skills/documents": ["Beta"]}'])
    model = RecordingModel(lambda _: next(replies))
    result = OneShotSkillTreeBuilder(
        model=model, config=_policy(request={"max_retries": 1, "max_output_tokens": 256, "timeout_seconds": 3.0}),
    ).build([OneShotSkill("Alpha"), OneShotSkill("Beta")])
    assert result.llm_calls == 3
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (20, 8, 28)
    assert len(set(model.loops)) == 1
    assert model.loops[0].is_closed()
    assert all(call["max_tokens"] == 256 and call["timeout"] == 3.0 for call in model.calls)
    assert {name for leaf in result.leaves for name in leaf.skills} == {"Alpha", "Beta"}


def test_one_shot_uses_the_config_module_budget_model():
    model = RecordingModel()
    OneShotSkillTreeBuilder(
        model=model, config=OneShotTreeBuildConfig(seed=7, reasoning_effort="low", max_output_tokens=80),
    ).build([OneShotSkill("Alpha")])
    assert model.calls[0]["seed"] == 7
    assert model.calls[0]["reasoning"].effort == "low"
    assert model.calls[0]["max_tokens"] == 80


@pytest.mark.asyncio
async def test_async_toolkit_profiles_publish_on_the_callers_loop(tmp_path):
    loop = asyncio.get_running_loop()

    def reply(request):
        text = request["messages"][-1]["content"]
        if "source_description:" in text:
            ids = re.findall(r"^- id: (.+)$", text, re.MULTILINE)
            return json.dumps({"profiles": {identifier: {
                "description": "Profile description " * 8,
                "select_when": "Select this object " * 8,
                "dont_select_when": "Avoid other objects " * 8,
            } for identifier in ids}})
        names = re.findall(r"^### (.+)$", text, re.MULTILINE)
        return json.dumps({"skills/documents": names})

    model = RecordingModel(reply)
    config = RetrievalBuildConfig(
        profiles={"enabled": True, "batch_size": 1, "select_rules_enabled": False,
                  "max_description_chars": 40, "max_rule_chars": 40},
        request={"max_output_tokens": 512, "timeout_seconds": 2.0},
        output={"show_tree": False, "cache_metrics_enabled": False, "generate_tree_html": True},
    )
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=config, model=model, skills=[
        SkillRecord(name="Beta", worker_id="beta", description="Source beta"),
        SkillRecord(name="Alpha", worker_id="alpha", description="Source alpha"),
    ])
    assert toolkit.build_index_async()["success"]
    state = await _finished(toolkit)
    assert state["status"] == "success", state
    assert len(model.calls) == 3
    assert all(caller_loop is loop for caller_loop in model.loops)
    assert all(call["timeout"] == 2.0 and call["max_tokens"] == 512 for call in model.calls)
    classification = model.calls[-1]["messages"][-1]["content"]
    assert classification.index("### Alpha") < classification.index("### Beta")
    assert "Select when:" not in classification
    records = load_catalog_records(tmp_path / "index" / "catalog.jsonl")
    assert {record.metadata["source_description"] for record in records} == {"Source alpha", "Source beta"}
    assert all(len(record.description) <= 43 and not record.metadata["select_when"] for record in records)
    assert (tmp_path / "index" / "tree_index.html").is_file()
    assert not loop.is_closed()


@pytest.mark.asyncio
async def test_cancel_and_total_deadline_preserve_the_published_index(tmp_path):
    config = _policy()
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=config, skills=[SkillRecord(name="Alpha")])
    assert toolkit.build_index()["success"]
    previous = (tmp_path / "index" / "tree_index.yaml").read_bytes()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowModel:
        async def invoke(self, **kwargs):
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

    model = SlowModel()
    toolkit.build_index_async(model=model, force=True)
    await asyncio.wait_for(entered.wait(), 1)
    toolkit.cancel_build(wait=False)
    await asyncio.wait_for(cancelled.wait(), 1)
    state = await _finished(toolkit)
    assert state["status"] == "cancelled"
    assert (tmp_path / "index" / "tree_index.yaml").read_bytes() == previous

    result = await asyncio.to_thread(
        toolkit.build_index, force=True, model=_bind_model_to_loop(model, asyncio.get_running_loop()),
        config=_policy(task={"total_timeout_seconds": 0.05}, request={"max_retries": 0}),
    )
    assert not result["success"]
    assert result["data"]["previous_index_preserved"]
    assert "timeout" in result["result"].lower() or "time" in result["result"].lower()
    assert (tmp_path / "index" / "tree_index.yaml").read_bytes() == previous


@pytest.mark.asyncio
async def test_request_timeout_retries_then_fails_without_publishing(tmp_path):
    calls = []

    class SlowModel:
        async def invoke(self, **kwargs):
            calls.append(asyncio.get_running_loop())
            await asyncio.sleep(60)

    loop = asyncio.get_running_loop()
    toolkit = AgenticSkillRetrievalToolkit(
        tmp_path, config=_policy(request={"timeout_seconds": 0.02, "max_retries": 1},
                                task={"allow_fallback_tree": False}),
        skills=[SkillRecord(name="Alpha")], model=_bind_model_to_loop(SlowModel(), loop),
    )
    result = await asyncio.to_thread(toolkit.build_index)
    assert not result["success"]
    assert calls == [loop, loop]
    assert not (tmp_path / "index" / "tree_index.yaml").exists()


def test_signature_excludes_output_and_keeps_model_and_content_policy(tmp_path):
    records = [SkillRecord(name="Alpha", content="Content")]
    model = RecordingModel()
    config = _policy()
    baseline = _index_fingerprint(records, config, model=model)
    changed_output = config.model_copy(update={"output": config.output.model_copy(update={
        "show_tree": True, "verbose": True, "generate_tree_html": True, "cache_metrics_enabled": True,
        "display_capability_root": "/display-only",
    })})
    assert _index_fingerprint(records, changed_output, model=model) == baseline
    assert _index_fingerprint(records, config, model=RecordingModel(name="other-model")) != baseline
    assert _index_fingerprint(records, _policy(structure={"max_depth": 2}), model=model) != baseline
    with _model_scope(model) as bound:
        assert _index_fingerprint(records, config, model=bound) == baseline
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=config, skills=records, model=model)
    assert toolkit.build_index()["success"]
    count = len(model.calls)
    assert toolkit.build_index(config=changed_output)["data"]["build"]["stage"] == "reuse"
    assert len(model.calls) == count


def test_missing_model_and_prepare_failure_follow_task_policy(tmp_path):
    strict = AgenticSkillRetrievalToolkit(
        tmp_path / "strict", config=_policy(task={"allow_fallback_tree": False}),
        skills=[SkillRecord(name="Alpha")],
    )
    assert not strict.build_index()["success"]
    model = RecordingModel()
    toolkit = AgenticSkillRetrievalToolkit(tmp_path / "live", config=_policy(), model=model,
                                          skills=[SkillRecord(name="Alpha")])
    assert toolkit.build_index()["success"]
    previous = (toolkit.index_dir / "tree_index.yaml").read_bytes()

    def reject(candidate: Path):
        assert (candidate / "catalog.jsonl").is_file()
        (candidate / "host-metadata.json").write_text("{}")
        raise ValueError("host preparation failed")

    result = toolkit.build_index(force=True, prepare_artifact=reject)
    assert not result["success"]
    assert result["data"]["previous_index_preserved"]
    assert (toolkit.index_dir / "tree_index.yaml").read_bytes() == previous
    assert not (toolkit.index_dir / "host-metadata.json").exists()


def test_configured_roots_are_checked_in_the_real_one_shot_result(tmp_path):
    config = _policy(structure={"root_categories": [{"id": "documents", "children": ["Reports"]}]},
                     task={"allow_fallback_tree": False})
    model = RecordingModel()
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=config, model=model,
                                          skills=[SkillRecord(name="Alpha")])
    result = toolkit.build_index()
    assert not result["success"]
    assert "configured categories" in result["result"]
    model.reply = lambda _: '{"skills/documents/reports": ["Alpha"]}'
    assert toolkit.build_index(force=True)["success"]
    tree = load_tree_preset(toolkit.index_dir / "tree_index.yaml")
    assert any(node.get("worker_id") == "Alpha" for node in tree["nodes"])


@pytest.mark.parametrize("existing_index", [False, True])
def test_postprocess_batch_model_failure_never_publishes_in_strict_mode(tmp_path, existing_index):
    records = [SkillRecord(name=f"Skill {index}", worker_id=f"skill-{index}") for index in range(6)]
    model = RecordingModel()
    config = _policy(request={"classification_batch_limit": 1, "max_retries": 0},
                     task={"allow_fallback_tree": False})
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=config, model=model, skills=records)
    previous = None
    if existing_index:
        assert toolkit.build_index()["success"]
        previous = (toolkit.index_dir / "tree_index.yaml").read_bytes()

    def reply(request):
        if len(request["messages"]) == 2:
            return json.dumps({"skills/documents": [f"Skill {i}" for i in range(3)],
                               "skills/images": [f"Skill {i}" for i in range(3, 6)]})
        return TimeoutError("postprocess model failed")

    model.reply = reply
    result = toolkit.build_index(force=True)
    assert not result["success"]
    assert "postprocess model failed" in result["result"]
    if existing_index:
        assert result["data"]["previous_index_preserved"]
        assert (toolkit.index_dir / "tree_index.yaml").read_bytes() == previous
    else:
        assert not (toolkit.index_dir / "tree_index.yaml").exists()


@pytest.mark.parametrize("stage", ["classification", "discovery"])
@pytest.mark.parametrize("interrupt", ["cancel", "deadline"])
def test_parallel_tree_jobs_always_propagate_interruptions(tmp_path, stage, interrupt):
    from openjiuwen.symphony.agent.retrieval_toolkit import SkillIndexBuildCancelled, SkillIndexBuildTimeout

    failure = SkillIndexBuildCancelled if interrupt == "cancel" else SkillIndexBuildTimeout
    model = RecordingModel(lambda _: failure("build interrupted"))
    builder = TreeBuilder(tmp_path, config=_policy(request={"max_retries": 0}), model=model)
    skills = [{"id": f"skill-{index}", "name": f"Skill {index}"} for index in range(4)]
    try:
        with pytest.raises(failure, match="build interrupted"):
            if stage == "classification":
                builder.grouping_engine.batched_classify_skills(skills, {"documents": {"name": "Docs"}}, batch_size=1)
            else:
                builder.grouping_engine._discover_from_samples(skills, None, batch_size=1, verbose=False)
    finally:
        builder.close()


def test_new_policy_and_model_force_full_rebuild_when_records_are_deleted(tmp_path):
    records = [SkillRecord(name=f"Skill {index}", worker_id=f"skill-{index}") for index in range(4)]
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=_policy(), model=RecordingModel(), skills=records)
    assert toolkit.build_index()["success"]
    changed_model = RecordingModel(lambda request: json.dumps({"skills/images": re.findall(
        r"^### (.+)$", request["messages"][-1]["content"], re.MULTILINE,
    )}), name="new-model")
    result = toolkit.build_index(
        skills=records[:3], model=changed_model,
        config=_policy(structure={"root_categories": ["Images"]}),
    )
    assert result["success"], result
    assert len(changed_model.calls) == 1
    roots = [node["cid"] for node in load_tree_preset(toolkit.index_dir / "tree_index.yaml")["nodes"]
             if "." not in node["cid"]]
    assert roots == ["images"]
    assert toolkit.build_index()["data"]["build"]["stage"] == "reuse"


def test_missing_policy_signature_forces_full_build_even_when_content_matches(tmp_path):
    model = RecordingModel()
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=_policy(), model=model,
                                          skills=[SkillRecord(name="Alpha")])
    assert toolkit.build_index()["success"]
    commit_path = toolkit.index_dir / "commit.json"
    commit = json.loads(commit_path.read_text())
    commit.pop("build_policy_fingerprint")
    commit_path.write_text(json.dumps(commit))
    result = toolkit.build_index()
    assert result["success"]
    assert len(model.calls) == 2
    assert result["data"]["build"]["stage"] == "success"


def test_model_signature_tracks_native_protocol_and_route_but_ignores_credentials():
    from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
    from openjiuwen.symphony.model_bridge import _model_identity

    request = ModelRequestConfig(model="same-model")
    client = ModelClientConfig(client_provider="OpenAI", api_base="https://example.invalid/v1",
                               api_key="key-a", api_mode="chat_completions",
                               custom_headers={"X-Route": "a", "Authorization": "Bearer secret-a"})
    model = SimpleNamespace(model_config=request, model_client_config=client)
    identity = _model_identity(model)
    for changes in ({"api_mode": "responses"}, {"custom_headers": {"X-Route": "b"}}):
        changed = SimpleNamespace(model_config=request, model_client_config=client.model_copy(update=changes))
        assert _model_identity(changed) != identity
    changed = SimpleNamespace(model_config=request, model_client_config=client.model_copy(update={
        "client_id": "another-client", "api_key": "key-b",
        "custom_headers": {"X-Route": "a", "Authorization": "Bearer secret-b"},
    }))
    assert _model_identity(changed) == identity
    assert "secret-a" not in json.dumps(identity) and "key-a" not in json.dumps(identity)


@pytest.mark.parametrize("status", [429, 500, 401, 400])
def test_recursive_requests_handle_native_wrapped_provider_errors(tmp_path, status):
    import httpx
    from openai import AuthenticationError, BadRequestError, InternalServerError, RateLimitError
    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.exception.errors import build_error

    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid/v1"))
    error_type = {429: RateLimitError, 500: InternalServerError, 401: AuthenticationError, 400: BadRequestError}[status]
    message = "invalid parameter: timeout must be greater than zero" if status == 400 else "provider rejected request"
    cause = error_type(message, response=response, body=None)
    wrapped = build_error(StatusCode.MODEL_CALL_FAILED, error_msg=f"openAI API async invoke error: {cause}")
    wrapped.__cause__ = cause
    responses = iter([wrapped, '{"after": "retry"}'])
    model = RecordingModel(lambda _: next(responses))
    builder = TreeBuilder(tmp_path, config=_policy(request={"max_retries": 2},
                                                  task={"allow_fallback_tree": False}), model=model)
    try:
        if status in {400, 401}:
            with pytest.raises(type(wrapped)):
                builder._call_llm("classify")
            assert len(model.calls) == 1
        else:
            assert builder._call_llm("classify") == '{"after": "retry"}'
            assert len(model.calls) == 2
            assert builder.state.retry_calls == 1
    finally:
        builder.close()


@pytest.mark.parametrize("stage", ["one_shot", "subtree"])
@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_tree_stages_retry_only_transient_native_provider_errors(tmp_path, stage, status):
    import httpx
    from openai import APIStatusError, BadRequestError
    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.exception.errors import build_error
    from openjiuwen.symphony.retrieval.build.workflows.subtree_rebuild import IncrementalSubtreeRebuilder

    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid/v1"))
    cause = (BadRequestError("invalid parameter: timeout must be greater than zero", response=response, body=None)
             if status == 400 else APIStatusError("provider rejected request", response=response, body=None))
    wrapped = build_error(StatusCode.MODEL_CALL_FAILED, error_msg=f"openAI API async invoke error: {cause}")
    wrapped.__cause__ = cause
    reply = ('{"skills/documents": ["Alpha", "Beta"]}' if stage == "one_shot" else
             '{"groups": [{"id": "documents", "skill_ids": ["alpha", "beta"]}]}')
    responses = iter([wrapped, reply])
    model = RecordingModel(lambda _: next(responses))
    config = _policy(request={"max_retries": 2}, task={"allow_fallback_tree": False})
    if stage == "one_shot":
        toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=config, model=model, skills=[
            SkillRecord(name="Alpha", worker_id="alpha"), SkillRecord(name="Beta", worker_id="beta"),
        ])
        result = toolkit.build_index()
        assert result["success"] is (status in {429, 500})
        assert (tmp_path / "index" / "tree_index.yaml").exists() is (status in {429, 500})
    else:
        nodes = [{"cid": "root", "type": "branch"},
                 {"cid": "root.alpha", "worker_id": "alpha", "type": "leaf"},
                 {"cid": "root.beta", "worker_id": "beta", "type": "leaf"}]
        rebuilder = IncrementalSubtreeRebuilder(config, model)
        if status in {429, 500}:
            assert rebuilder.rebuild(nodes, "root", {}, 2)
        else:
            with pytest.raises(type(wrapped)):
                rebuilder.rebuild(nodes, "root", {}, 2)
    assert len(model.calls) == (2 if status in {429, 500} else 1)


@pytest.mark.asyncio
async def test_timeout_waits_for_model_cleanup_before_retry_and_return(tmp_path):
    class CleaningModel:
        calls = 0
        active = 0
        peak_active = 0

        async def invoke(self, **kwargs):
            self.calls += 1
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            try:
                await asyncio.sleep(60)
            finally:
                await asyncio.sleep(0.15)
                self.active -= 1

    model = CleaningModel()
    toolkit = AgenticSkillRetrievalToolkit(
        tmp_path, config=_policy(request={"timeout_seconds": 0.02, "max_retries": 1, "max_concurrency": 1},
                                task={"allow_fallback_tree": False}),
        skills=[SkillRecord(name="Alpha")], model=_bind_model_to_loop(model, asyncio.get_running_loop()),
    )
    result = await asyncio.to_thread(toolkit.build_index)
    try:
        assert not result["success"]
        assert model.calls == 2
        assert model.peak_active == 1
        assert model.active == 0
        assert not toolkit.index_dir.exists()
    finally:
        await asyncio.sleep(0.2)


@pytest.mark.parametrize("signal", ["cancel", "deadline"])
@pytest.mark.asyncio
async def test_interruption_waits_for_model_cleanup_before_return(tmp_path, signal):
    import threading

    entered = asyncio.Event()
    cleaned = asyncio.Event()
    calls = []

    class CleaningModel:
        async def invoke(self, **kwargs):
            calls.append(kwargs)
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                await asyncio.sleep(0.05)
                cleaned.set()

    cancel = threading.Event()
    toolkit = AgenticSkillRetrievalToolkit(
        tmp_path, config=_policy(task={"total_timeout_seconds": 0.05} if signal == "deadline" else {}),
        skills=[SkillRecord(name="Alpha")],
        model=_bind_model_to_loop(CleaningModel(), asyncio.get_running_loop()),
    )
    build = asyncio.create_task(asyncio.to_thread(toolkit.build_index, cancel_token=cancel))
    await asyncio.wait_for(entered.wait(), 1)
    if signal == "cancel":
        cancel.set()
    result = await build
    assert not result["success"]
    assert cleaned.is_set()
    assert len(calls) == 1
    assert not toolkit.index_dir.exists()


@pytest.mark.asyncio
async def test_unconfirmed_model_cleanup_aborts_without_retry_or_fallback(tmp_path, monkeypatch):
    import openjiuwen.symphony.model_bridge as bridge_module

    monkeypatch.setattr(bridge_module, "_MODEL_CLEANUP_TIMEOUT_SECONDS", 0.01)
    release = asyncio.Event()
    cleaned = asyncio.Event()
    calls = []

    class CleaningModel:
        async def invoke(self, **kwargs):
            calls.append(kwargs)
            try:
                await asyncio.sleep(60)
            finally:
                await release.wait()
                cleaned.set()

    toolkit = AgenticSkillRetrievalToolkit(
        tmp_path, config=_policy(request={"timeout_seconds": 0.02, "max_retries": 2}),
        skills=[SkillRecord(name="Alpha")],
        model=_bind_model_to_loop(CleaningModel(), asyncio.get_running_loop()),
    )
    try:
        result = await asyncio.to_thread(toolkit.build_index)
        assert not result["success"]
        assert "cancellation cleanup did not finish" in result["result"]
        assert len(calls) == 1
        assert not toolkit.index_dir.exists()
    finally:
        release.set()
        await asyncio.wait_for(cleaned.wait(), 1)


def test_status_without_model_checks_content_policy_and_new_signature(tmp_path):
    config = _policy()
    records = [SkillRecord(name="Alpha", content="original")]
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=config, model=RecordingModel(), skills=records)
    assert toolkit.build_index()["success"]
    reader = AgenticSkillRetrievalToolkit(tmp_path, config=config, skills=records)
    status = reader.load_index_status()
    assert status["fresh"]
    assert status["model_identity_checked"] is False
    assert reader.load_index_tree()["success"]
    assert toolkit.load_index_status()["model_identity_checked"] is True
    changed_records = AgenticSkillRetrievalToolkit(
        tmp_path, config=config, skills=[SkillRecord(name="Alpha", content="changed")],
    )
    assert not changed_records.load_index_status()["fresh"]
    changed_policy = AgenticSkillRetrievalToolkit(tmp_path, config=_policy(structure={"max_depth": 8}), skills=records)
    assert not changed_policy.load_index_status()["fresh"]
    commit_path = toolkit.index_dir / "commit.json"
    commit = json.loads(commit_path.read_text())
    commit.pop("build_settings_fingerprint")
    commit_path.write_text(json.dumps(commit))
    assert not reader.load_index_status()["fresh"]
    assert not reader.load_index_tree()["success"]
    assert not toolkit.load_index_status()["fresh"]


def test_success_reports_actual_build_delete_and_reuse_operations(tmp_path, monkeypatch):
    import openjiuwen.symphony.agent.retrieval_toolkit as toolkit_module

    records = [SkillRecord(name=f"Object{index}", worker_id=f"object-{index}") for index in range(4)]
    model = RecordingModel()
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=_policy(), model=model, skills=records)
    assert toolkit.build_index()["data"]["build"]["operation"] == "build"
    original_builder = toolkit_module._run_index_builder

    def fail_incremental_add(**kwargs):
        if kwargs["operation"] == "add":
            raise ValueError("incremental placement is unavailable")
        return original_builder(**kwargs)

    monkeypatch.setattr(toolkit_module, "_run_index_builder", fail_incremental_add)
    added = [*records, SkillRecord(name="New", worker_id="new")]
    result = toolkit.build_index(skills=added)
    assert result["success"]
    assert result["data"]["build"]["planned_operation"] == "add"
    assert result["data"]["build"]["operation"] == "build"
    assert len(load_catalog_records(toolkit.index_dir / "catalog.jsonl")) == 5
    assert len(model.calls) == 2
    reused = toolkit.build_index()
    assert reused["data"]["build"]["operation"] == "reuse"
    assert len(model.calls) == 2
    deleted = toolkit.build_index(skills=records)
    assert deleted["success"]
    assert deleted["data"]["build"]["operation"] == "delete"
    assert toolkit.load_index_status()["operation"] == "delete"
    assert len(load_catalog_records(toolkit.index_dir / "catalog.jsonl")) == 4
    assert len(model.calls) == 2


def test_change_ratio_escalation_reports_a_full_build(tmp_path):
    records = [SkillRecord(name=f"Object{index}", worker_id=f"object-{index}") for index in range(2)]
    model = RecordingModel()
    toolkit = AgenticSkillRetrievalToolkit(
        tmp_path, config=_policy(incremental={"max_change_ratio": 0.25}), model=model, skills=records,
    )
    assert toolkit.build_index()["success"]
    result = toolkit.build_index(skills=[*records, SkillRecord(name="New", worker_id="new")])
    assert result["success"]
    assert result["data"]["build"]["planned_operation"] == "add"
    assert result["data"]["build"]["operation"] == "build"
    assert toolkit.load_index_status()["operation"] == "build"
    assert json.loads((toolkit.index_dir / "manifest.json").read_text())["mode"] == "full"
    assert len(load_catalog_records(toolkit.index_dir / "catalog.jsonl")) == 3
    assert len(model.calls) == 2


@pytest.mark.parametrize("window", ["copy", "lock"])
def test_cancellation_during_publication_keeps_the_previous_generation(tmp_path, monkeypatch, window):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import openjiuwen.symphony.agent.retrieval_toolkit as toolkit_module

    model = RecordingModel()
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=_policy(), model=model,
                                          skills=[SkillRecord(name="Alpha")])
    assert toolkit.build_index()["success"]
    previous = (toolkit.index_dir / "tree_index.yaml").read_bytes()
    model.reply = lambda _: '{"skills/images": ["Alpha"]}'
    cancelled = threading.Event()
    copied = threading.Event()
    release_copy = threading.Event()
    lock_attempted = threading.Event()
    original_copy = toolkit_module.shutil.copytree
    original_lock = toolkit_module.portalocker.Lock
    main_thread = threading.current_thread()

    def copy(source, destination, *args, **kwargs):
        result = original_copy(source, destination, *args, **kwargs)
        if ".pending-" in Path(destination).name:
            copied.set()
            assert release_copy.wait(3)
        return result

    def lock(*args, **kwargs):
        if copied.is_set() and threading.current_thread() is not main_thread:
            lock_attempted.set()
        return original_lock(*args, **kwargs)

    monkeypatch.setattr(toolkit_module.shutil, "copytree", copy)
    monkeypatch.setattr(toolkit_module.portalocker, "Lock", lock)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(toolkit.build_index, force=True, cancel_token=cancelled)
        assert copied.wait(3)
        if window == "lock":
            with original_lock(str(tmp_path / ".skill-index.publish.lock"), mode="a+", timeout=2):
                release_copy.set()
                assert lock_attempted.wait(3)
                cancelled.set()
        else:
            cancelled.set()
            release_copy.set()
        result = pending.result(timeout=3)
    assert not result["success"]
    assert result["data"]["build"]["status"] == "cancelled"
    assert (toolkit.index_dir / "tree_index.yaml").read_bytes() == previous
    assert not list(tmp_path.glob(".index.pending-*"))


def test_small_one_shot_tree_preserves_classification_ids_without_postprocess_calls(tmp_path):
    model = RecordingModel()
    toolkit = AgenticSkillRetrievalToolkit(tmp_path, config=_policy(), model=model, skills=[
        SkillRecord(name=name, worker_id=name) for name in ("alpha", "beta", "hidden")
    ])
    assert toolkit.build_index()["success"]
    assert len(model.calls) == 1
    records = load_catalog_records(toolkit.index_dir / "catalog.jsonl")
    assert {record.branch_path for record in records} == {("documents",)}
    assert {record.cid for record in records} == {"documents.alpha", "documents.beta", "documents.hidden"}
