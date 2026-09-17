"""Covers the path-staging helpers added to CodeImplementationAgent so the
coding subagent doesn't need to blind-search a huge workspace/home directory
for a path already mentioned as plain text in its instructions -- see
_extract_path_candidates, _stage_referenced_paths, and
_build_referenced_paths_prompt in modules/code_implementation/agent.py.
"""

from __future__ import annotations

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation import agent as agent_module
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.agent import (
    CodeImplementationAgent,
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
