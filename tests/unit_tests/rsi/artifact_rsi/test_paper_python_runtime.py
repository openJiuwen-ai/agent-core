"""Tests for host Python interpreter discovery, in particular the Windows
App Execution Alias trap: a bare `python`/`py` can resolve to a real file
under %LOCALAPPDATA%\\Microsoft\\WindowsApps\\ that just prints a Microsoft
Store prompt instead of running anything.
"""

import os
from types import SimpleNamespace

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common import python_runtime


def _fake_version_run(output: str):
    def fake_run(args, **kwargs):
        del args, kwargs
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    return fake_run


def test_discover_finds_explicit_directory_and_verifies_it(tmp_path, monkeypatch):
    executable = tmp_path / "python.exe"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(python_runtime.shutil, "which", lambda name, path=None: str(executable))
    monkeypatch.setattr(python_runtime.subprocess, "run", _fake_version_run("Python 3.12.4\n"))

    runtime = python_runtime.discover_python_runtime(tmp_path, environ={"PATH": ""})

    assert runtime.available
    assert runtime.python == executable
    assert runtime.bin_dir == tmp_path


def test_discover_rejects_windows_apps_alias_even_when_which_returns_it(tmp_path, monkeypatch):
    alias_dir = tmp_path / "AppData" / "Local" / "Microsoft" / "WindowsApps"
    alias_dir.mkdir(parents=True)
    alias = alias_dir / "python.exe"
    alias.write_text("", encoding="utf-8")

    monkeypatch.setattr(python_runtime.shutil, "which", lambda name, path=None: str(alias))
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="Python 3.12.4\n", stderr="")

    monkeypatch.setattr(python_runtime.subprocess, "run", fake_run)

    runtime = python_runtime.discover_python_runtime(environ={"PATH": str(alias_dir)})

    assert not runtime.available
    # Never even worth a --version probe -- excluded before the functional check.
    assert calls == []


def test_discover_rejects_candidate_whose_version_output_is_the_store_prompt(tmp_path, monkeypatch):
    executable = tmp_path / "python.exe"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(python_runtime.shutil, "which", lambda name, path=None: str(executable))
    monkeypatch.setattr(
        python_runtime.subprocess,
        "run",
        _fake_version_run("Python was not found; run without arguments to install from the Microsoft Store.\n"),
    )

    runtime = python_runtime.discover_python_runtime(tmp_path, environ={"PATH": ""})

    assert not runtime.available


def test_with_environment_prepends_bin_dir_and_does_not_mutate_process_environ(tmp_path):
    original_path = os.environ.get("PATH")
    runtime = python_runtime.PythonRuntime(python=tmp_path / "python.exe", search_dirs=(tmp_path,))

    child_env = runtime.with_environment({"PATH": "/existing/bin"})

    assert child_env["PATH"].split(os.pathsep)[0] == str(tmp_path)
    assert "/existing/bin" in child_env["PATH"].split(os.pathsep)
    assert child_env["PYTHON_EXE"] == str(tmp_path / "python.exe")
    assert child_env["PYTHON_BIN_DIR"] == str(tmp_path)
    assert os.environ.get("PATH") == original_path


def test_ensure_on_path_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHON_EXE", raising=False)
    monkeypatch.delenv("PYTHON_BIN_DIR", raising=False)
    monkeypatch.setenv("PATH", "/existing/bin")
    runtime = python_runtime.PythonRuntime(python=tmp_path / "python.exe", search_dirs=(tmp_path,))

    python_runtime.ensure_on_path(runtime)
    first_path = os.environ["PATH"]
    python_runtime.ensure_on_path(runtime)
    second_path = os.environ["PATH"]

    assert first_path == second_path
    assert first_path.split(os.pathsep).count(str(tmp_path)) == 1
    assert os.environ["PYTHON_EXE"] == str(tmp_path / "python.exe")


def test_ensure_on_path_no_op_when_runtime_unavailable(monkeypatch):
    monkeypatch.setenv("PATH", "/existing/bin")
    python_runtime.ensure_on_path(python_runtime.PythonRuntime(python=None, search_dirs=()))
    assert os.environ["PATH"] == "/existing/bin"
