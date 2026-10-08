"""Covers the path-staging helpers added to CodeImplementationAgent so the
coding subagent doesn't need to blind-search a huge workspace/home directory
for a path already mentioned as plain text in its instructions -- see
_extract_path_candidates, _stage_referenced_paths, and
_build_referenced_paths_prompt in modules/code_implementation/agent.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import set_project_root
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation import agent as agent_module
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.agent import (
    CodeImplementationAgent,
    _extract_stdout_metrics,
    _pyright_lsp_command,
    _ReferencedPath,
)

# -- _extract_path_candidates -------------------------------------------------


def test_extract_path_candidates_finds_windows_absolute_path():
    text = "The dataset is at C:\\Users\\Administrator\\Documents\\data\\sentiment.json for this run."
    candidates = CodeImplementationAgent._extract_path_candidates(text)
    assert "C:\\Users\\Administrator\\Documents\\data\\sentiment.json" in candidates


def test_extract_path_candidates_finds_posix_absolute_path():
    text = "the file is at /home/user/data/sentiment.json for real"
    candidates = CodeImplementationAgent._extract_path_candidates(text)
    assert "/home/user/data/sentiment.json" in candidates


def test_extract_path_candidates_strips_trailing_sentence_punctuation():
    text = "see C:\\a\\b\\data.json."
    candidates = CodeImplementationAgent._extract_path_candidates(text)
    assert "C:\\a\\b\\data.json" in candidates
    assert "C:\\a\\b\\data.json." not in candidates


def test_extract_path_candidates_dedupes_across_multiple_texts():
    text_a = "dataset: C:\\data\\a.json"
    text_b = "remember the dataset at C:\\data\\a.json again"
    candidates = CodeImplementationAgent._extract_path_candidates(text_a, text_b)
    assert candidates.count("C:\\data\\a.json") == 1


def test_extract_path_candidates_caps_at_limit():
    text = " ".join(f"C:\\data\\file{i}.json" for i in range(20))
    candidates = CodeImplementationAgent._extract_path_candidates(text, limit=3)
    assert len(candidates) == 3


def test_extract_path_candidates_empty_text_returns_empty():
    assert CodeImplementationAgent._extract_path_candidates("", "") == []


# -- _stage_referenced_paths ---------------------------------------------------


def test_stage_referenced_paths_copies_existing_file_into_workspace(tmp_path):
    source = tmp_path / "source" / "data.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"a": 1}', encoding="utf-8")
    agent_workspace = tmp_path / "agent_workspace"
    agent_workspace.mkdir()

    results = CodeImplementationAgent._stage_referenced_paths([str(source)], agent_workspace)

    assert len(results) == 1
    record = results[0]
    assert record.kind == "file"
    assert record.workspace_rel is not None
    staged = agent_workspace / record.workspace_rel
    assert staged.is_file()
    assert staged.read_text(encoding="utf-8") == '{"a": 1}'


def test_stage_referenced_paths_reports_directory_without_copying(tmp_path):
    source_dir = tmp_path / "source_dir"
    source_dir.mkdir()
    (source_dir / "inner.txt").write_text("x", encoding="utf-8")
    agent_workspace = tmp_path / "agent_workspace"
    agent_workspace.mkdir()

    results = CodeImplementationAgent._stage_referenced_paths([str(source_dir)], agent_workspace)

    assert len(results) == 1
    assert results[0].kind == "dir"
    assert results[0].workspace_rel is None
    assert not (agent_workspace / "referenced_paths").exists()


def test_stage_referenced_paths_skips_nonexistent_candidate(tmp_path):
    agent_workspace = tmp_path / "agent_workspace"
    agent_workspace.mkdir()
    results = CodeImplementationAgent._stage_referenced_paths(
        [str(tmp_path / "does" / "not" / "exist.json")], agent_workspace
    )
    assert results == []


def test_stage_referenced_paths_flags_oversized_file_without_copying(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_MAX_REFERENCED_FILE_BYTES", 4)
    source = tmp_path / "big.json"
    source.write_text("this is more than four bytes", encoding="utf-8")
    agent_workspace = tmp_path / "agent_workspace"
    agent_workspace.mkdir()

    results = CodeImplementationAgent._stage_referenced_paths([str(source)], agent_workspace)

    assert len(results) == 1
    assert results[0].kind == "file_too_large"
    assert results[0].workspace_rel is None
    assert not (agent_workspace / "referenced_paths").exists()


def test_stage_referenced_paths_is_idempotent_across_repeated_calls(tmp_path):
    source = tmp_path / "data.json"
    source.write_text("v1", encoding="utf-8")
    agent_workspace = tmp_path / "agent_workspace"
    agent_workspace.mkdir()

    first = CodeImplementationAgent._stage_referenced_paths([str(source)], agent_workspace)
    source.write_text("v2", encoding="utf-8")
    second = CodeImplementationAgent._stage_referenced_paths([str(source)], agent_workspace)

    assert first[0].workspace_rel == second[0].workspace_rel
    staged = agent_workspace / second[0].workspace_rel
    assert staged.read_text(encoding="utf-8") == "v2"


def test_extract_path_candidates_finds_relative_path_with_backslash():
    text = "The dataset is at demo-input\\sentiment_icl_v1.json for this run."
    candidates = CodeImplementationAgent._extract_path_candidates(text)
    assert "demo-input\\sentiment_icl_v1.json" in candidates


def test_extract_path_candidates_finds_relative_path_with_forward_slash():
    text = "The dataset is at demo-input/sentiment_icl_v1.json for this run."
    candidates = CodeImplementationAgent._extract_path_candidates(text)
    assert "demo-input/sentiment_icl_v1.json" in candidates


# -- _referenced_path_roots ----------------------------------------------------


def test_referenced_path_roots_climbs_ancestors_of_artifact_path(tmp_path):
    artifact_path = tmp_path / "demo-input-pkg" / "demo-input" / "paper"
    artifact_path.mkdir(parents=True)

    roots = CodeImplementationAgent._referenced_path_roots(str(artifact_path))

    assert artifact_path in roots
    assert (tmp_path / "demo-input-pkg" / "demo-input") in roots
    assert (tmp_path / "demo-input-pkg") in roots


def test_referenced_path_roots_empty_for_no_artifact_path():
    assert CodeImplementationAgent._referenced_path_roots(None) == []
    assert CodeImplementationAgent._referenced_path_roots("") == []


def test_stage_referenced_paths_resolves_relative_candidate_against_roots(tmp_path):
    artifact_path = tmp_path / "demo-input-pkg" / "demo-input" / "paper"
    artifact_path.mkdir(parents=True)
    dataset = tmp_path / "demo-input-pkg" / "demo-input" / "sentiment_icl_v1.json"
    dataset.write_text('{"items": []}', encoding="utf-8")
    agent_workspace = tmp_path / "agent_workspace"
    agent_workspace.mkdir()

    roots = CodeImplementationAgent._referenced_path_roots(str(artifact_path))
    results = CodeImplementationAgent._stage_referenced_paths(
        ["demo-input\\sentiment_icl_v1.json"], agent_workspace, roots=roots
    )

    assert len(results) == 1
    assert results[0].kind == "file"
    assert results[0].host_path == str(dataset)


def test_stage_referenced_paths_drops_relative_candidate_with_no_matching_root(tmp_path):
    agent_workspace = tmp_path / "agent_workspace"
    agent_workspace.mkdir()

    results = CodeImplementationAgent._stage_referenced_paths(
        ["demo-input\\sentiment_icl_v1.json"], agent_workspace, roots=[tmp_path]
    )

    assert results == []


# -- _split_relative_candidate (cross-platform separators) -------------------


def test_split_relative_candidate_accepts_backslash_forwardslash_and_mixed():
    assert CodeImplementationAgent._split_relative_candidate(
        "demo-input\\sentiment_icl_v1.json"
    ) == ["demo-input", "sentiment_icl_v1.json"]
    assert CodeImplementationAgent._split_relative_candidate(
        "demo-input/sentiment_icl_v1.json"
    ) == ["demo-input", "sentiment_icl_v1.json"]
    assert CodeImplementationAgent._split_relative_candidate(
        "a\\b/c.json"
    ) == ["a", "b", "c.json"]


def test_split_relative_candidate_drops_dot_and_dotdot_segments():
    assert CodeImplementationAgent._split_relative_candidate(
        "..\\..\\etc\\passwd.json"
    ) == ["etc", "passwd.json"]
    assert CodeImplementationAgent._split_relative_candidate(
        ".\\demo-input\\x.json"
    ) == ["demo-input", "x.json"]


def test_stage_referenced_paths_resolves_forward_slash_candidate_against_roots(tmp_path):
    """The candidate's separator style must resolve the same way regardless
    of which separator the coding host itself uses (Windows dev box vs a
    Linux/Mac sandbox in production)."""
    artifact_path = tmp_path / "demo-input-pkg" / "demo-input" / "paper"
    artifact_path.mkdir(parents=True)
    dataset = tmp_path / "demo-input-pkg" / "demo-input" / "sentiment_icl_v1.json"
    dataset.write_text('{"items": []}', encoding="utf-8")
    agent_workspace = tmp_path / "agent_workspace"
    agent_workspace.mkdir()

    roots = CodeImplementationAgent._referenced_path_roots(str(artifact_path))
    results = CodeImplementationAgent._stage_referenced_paths(
        ["demo-input/sentiment_icl_v1.json"], agent_workspace, roots=roots
    )

    assert len(results) == 1
    assert results[0].kind == "file"
    assert results[0].host_path == str(dataset)


def test_stage_referenced_paths_skips_malformed_candidate_without_raising(tmp_path):
    agent_workspace = tmp_path / "agent_workspace"
    agent_workspace.mkdir()
    # Embedded NUL raises ValueError from Path()/os.stat on every platform --
    # this must be swallowed, not propagate, and must not stop other
    # candidates in the same batch from being processed.
    good = tmp_path / "ok.json"
    good.write_text("{}", encoding="utf-8")
    results = CodeImplementationAgent._stage_referenced_paths(["C:\\bad\x00path", str(good)], agent_workspace)
    assert len(results) == 1
    assert results[0].host_path == str(good)


# -- _build_referenced_paths_prompt -------------------------------------------


def test_build_referenced_paths_prompt_empty_list_returns_empty_string():
    assert CodeImplementationAgent._build_referenced_paths_prompt([]) == ""


def test_build_referenced_paths_prompt_describes_staged_file():
    record = _ReferencedPath(kind="file", host_path="C:\\data\\a.json", workspace_rel="referenced_paths/ab12/a.json")
    prompt = CodeImplementationAgent._build_referenced_paths_prompt([record])
    assert "referenced_paths/ab12/a.json" in prompt
    assert "C:\\data\\a.json" in prompt
    assert "do not run `find`" in prompt.lower() or "do not run" in prompt.lower()


def test_build_referenced_paths_prompt_describes_directory():
    record = _ReferencedPath(kind="dir", host_path="/home/user/data")
    prompt = CodeImplementationAgent._build_referenced_paths_prompt([record])
    assert "/home/user/data" in prompt
    assert "directory" in prompt.lower()


def test_build_referenced_paths_prompt_describes_oversized_file():
    record = _ReferencedPath(kind="file_too_large", host_path="/data/huge.bin", size_bytes=200 * 1024 * 1024)
    prompt = CodeImplementationAgent._build_referenced_paths_prompt([record])
    assert "/data/huge.bin" in prompt
    assert "not copied" in prompt.lower()


# -- _extract_stdout_metrics ---------------------------------------------------
# Smoke-test result recovery channel added after a packaged-desktop-host
# incident where the candidate's own --output file write silently landed
# under the host's home directory instead of the requested path (the
# launcher was observed changing its process cwd before running the
# candidate script). Stdout is not subject to that.


def test_extract_stdout_metrics_finds_marker_among_noisy_log_lines():
    stdout = (
        "2026-09-21 | INFO | Registered connector pool type: default\n"
        'SMOKE_METRICS_JSON:{"method": "proposed", "n_questions": 1}\n'
        "2026-09-21 | INFO | done\n"
    )
    found, payload = _extract_stdout_metrics(stdout)
    assert found is True
    assert payload == {"method": "proposed", "n_questions": 1}


def test_extract_stdout_metrics_no_marker_returns_not_found():
    found, payload = _extract_stdout_metrics("just some ordinary log output\n")
    assert found is False
    assert payload is None


def test_extract_stdout_metrics_malformed_json_reports_found_with_no_payload():
    found, payload = _extract_stdout_metrics("SMOKE_METRICS_JSON:{not valid json\n")
    assert found is True
    assert payload is None


def test_extract_stdout_metrics_keeps_last_of_repeated_markers():
    stdout = 'SMOKE_METRICS_JSON:{"n": 1}\nSMOKE_METRICS_JSON:{"n": 2}\n'
    found, payload = _extract_stdout_metrics(stdout)
    assert found is True
    assert payload == {"n": 2}


def test_extract_stdout_metrics_non_dict_payload_treated_as_not_found_content():
    found, payload = _extract_stdout_metrics("SMOKE_METRICS_JSON:[1, 2, 3]\n")
    assert found is True
    assert payload is None


# -- _resolve_smoke_metrics -----------------------------------------------------


def test_resolve_smoke_metrics_no_marker_falls_back_to_file(tmp_path):
    metrics_path = tmp_path / "proposed.metrics.json"
    metrics_path.write_text('{"method": "proposed", "n_questions": 1}', encoding="utf-8")
    metrics, state = CodeImplementationAgent._resolve_smoke_metrics(
        "plain log output, no marker", metrics_path, "proposed"
    )
    assert state == "present"
    assert metrics == {"method": "proposed", "n_questions": 1}


def test_resolve_smoke_metrics_no_marker_and_missing_file_reports_missing(tmp_path):
    metrics_path = tmp_path / "proposed.metrics.json"
    metrics, state = CodeImplementationAgent._resolve_smoke_metrics(
        "plain log output, no marker", metrics_path, "proposed"
    )
    assert state == "missing"
    assert metrics == {}


def test_resolve_smoke_metrics_marker_present_and_file_missing_repairs_file(tmp_path):
    metrics_path = tmp_path / "smoke" / "proposed.metrics.json"
    stdout = 'SMOKE_METRICS_JSON:{"method": "proposed", "n_questions": 1}\n'
    metrics, state = CodeImplementationAgent._resolve_smoke_metrics(stdout, metrics_path, "proposed")
    assert state == "present"
    assert metrics == {"method": "proposed", "n_questions": 1}
    # The candidate's own file write never landed -- the host must repair it,
    # since smoke_test_dir's metrics.json is a kept-on-disk debugging artifact.
    assert metrics_path.is_file()
    assert metrics_path.read_text(encoding="utf-8").strip().startswith("{")


def test_resolve_smoke_metrics_marker_and_file_both_present_prefers_stdout(tmp_path):
    metrics_path = tmp_path / "proposed.metrics.json"
    metrics_path.write_text('{"method": "proposed", "n_questions": 99}', encoding="utf-8")
    stdout = 'SMOKE_METRICS_JSON:{"method": "proposed", "n_questions": 1}\n'
    metrics, state = CodeImplementationAgent._resolve_smoke_metrics(stdout, metrics_path, "proposed")
    assert state == "present"
    assert metrics == {"method": "proposed", "n_questions": 1}


def test_resolve_smoke_metrics_marker_malformed_reports_invalid_json_without_touching_file(tmp_path):
    metrics_path = tmp_path / "proposed.metrics.json"
    metrics, state = CodeImplementationAgent._resolve_smoke_metrics(
        "SMOKE_METRICS_JSON:{not valid", metrics_path, "proposed"
    )
    assert state == "invalid_json"
    assert metrics == {}
    assert not metrics_path.exists()


# -- _pyright_lsp_command -------------------------------------------------------
# On the packaged desktop host, sys.executable is the launcher binary itself,
# which has no -m module-runner (same class of failure _compile_staged_python
# hit with -m compileall). harness.lsp.servers.servers.python's own pyright
# resolution already handles both an npm-global install (spawned via node,
# no Python involved) and a Windows .cmd shim by parsing it -- reuse that
# instead of re-deriving a weaker version here, and only fall back to the
# pip-installed `-m pyright.langserver` path when it finds nothing. The
# delegation itself must be exception-safe: a broken/renamed harness resolver
# should degrade to "no pyright found", not crash agent construction.

_HARNESS_RESOLVE_PYRIGHT_PATH = "openjiuwen.harness.lsp.servers.servers.python._resolve_pyright_command"


def test_pyright_lsp_command_prefers_harness_resolution(monkeypatch):
    resolved = ("/usr/bin/node", ["/opt/pyright/langserver.index.js", "--stdio"])
    monkeypatch.setattr(_HARNESS_RESOLVE_PYRIGHT_PATH, lambda: resolved)
    monkeypatch.setattr(agent_module.importlib.util, "find_spec", lambda name: object())

    command = _pyright_lsp_command()

    assert command == resolved


def test_pyright_lsp_command_falls_back_to_module_when_harness_finds_nothing(monkeypatch):
    monkeypatch.setattr(_HARNESS_RESOLVE_PYRIGHT_PATH, lambda: None)
    monkeypatch.setattr(agent_module.importlib.util, "find_spec", lambda name: object())

    command = _pyright_lsp_command()

    assert command == (agent_module.sys.executable, ["-m", "pyright.langserver", "--stdio"])


def test_pyright_lsp_command_none_when_nothing_available(monkeypatch):
    monkeypatch.setattr(_HARNESS_RESOLVE_PYRIGHT_PATH, lambda: None)
    monkeypatch.setattr(agent_module.importlib.util, "find_spec", lambda name: None)

    assert _pyright_lsp_command() is None


def test_pyright_lsp_command_survives_harness_resolver_raising(monkeypatch):
    def _boom():
        raise RuntimeError("npm list blew up")

    monkeypatch.setattr(_HARNESS_RESOLVE_PYRIGHT_PATH, _boom)
    monkeypatch.setattr(agent_module.importlib.util, "find_spec", lambda name: object())

    command = _pyright_lsp_command()

    assert command == (agent_module.sys.executable, ["-m", "pyright.langserver", "--stdio"])


def test_pyright_lsp_command_survives_harness_import_failure(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _fail_on_harness_python(name, *args, **kwargs):
        if name == "openjiuwen.harness.lsp.servers.servers.python":
            raise ImportError("simulated import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_on_harness_python)
    monkeypatch.setattr(agent_module.importlib.util, "find_spec", lambda name: None)

    assert _pyright_lsp_command() is None


# -- _build_coding_agent: max_iterations wiring --------------------------------
#
# Regression coverage for the 2026-09-22 harness change (`fix(react): Honor
# configured inner ReAct max_iterations; default to unbounded when unset.`):
# before that change, DeepAgentConfig.max_iterations was silently ignored
# whenever enable_task_loop=True (the inner ReAct loop was always forced to
# sys.maxsize), so omitting the kwarg here was harmless. After that change the
# value is genuinely honored, but create_code_agent's own default is 15 (not
# unbounded) -- omitting the kwarg now silently caps every coding session's
# inner ReAct loop at 15 rounds regardless of this module's own config,
# reproducing the "code_implementation never writes output/run.py" failure.


def test_build_coding_agent_forwards_configured_max_iterations(tmp_path):
    set_project_root(tmp_path)
    try:
        agent = CodeImplementationAgent(
            config={"code_implementation": {"max_iterations": 77}}, model=MagicMock()
        )
        with patch.object(agent_module, "_try_lsp_rail", return_value=None), patch(
            "openjiuwen.harness.subagents.create_code_agent", return_value=MagicMock()
        ) as mock_create:
            agent._build_coding_agent(tmp_path / "agent_workspace", run_id="rsi-test-run", cycle=1)
        assert mock_create.call_args.kwargs["max_iterations"] == 77
    finally:
        set_project_root(None)


def test_build_coding_agent_defaults_max_iterations_to_forty(tmp_path):
    set_project_root(tmp_path)
    try:
        agent = CodeImplementationAgent(config={}, model=MagicMock())
        with patch.object(agent_module, "_try_lsp_rail", return_value=None), patch(
            "openjiuwen.harness.subagents.create_code_agent", return_value=MagicMock()
        ) as mock_create:
            agent._build_coding_agent(tmp_path / "agent_workspace", run_id="rsi-test-run", cycle=1)
        assert mock_create.call_args.kwargs["max_iterations"] == 40
    finally:
        set_project_root(None)
