"""Agent history uses the same data root as its jiuwenswarm host."""

from pathlib import Path
from types import SimpleNamespace

import openjiuwen.core.sys_operation.cwd as cwd
from openjiuwen.core.sys_operation.cwd import get_agent_history_root


def test_agent_history_root_uses_legacy_data_dir_alias(monkeypatch, tmp_path):
    monkeypatch.delenv("JIUWENSWARM_DATA_DIR", raising=False)
    monkeypatch.setenv("JIUWENCLAW_DATA_DIR", str(tmp_path / "legacy-data"))
    monkeypatch.setenv("JIUWENSWARM_HOME", str(tmp_path / "home"))

    assert get_agent_history_root() == str((tmp_path / "legacy-data").resolve())


def test_agent_history_root_prefers_jiuwenswarm_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(tmp_path / "swarm-data"))
    monkeypatch.setenv("JIUWENCLAW_DATA_DIR", str(tmp_path / "legacy-data"))

    assert get_agent_history_root() == str((tmp_path / "swarm-data").resolve())


def test_agent_history_root_falls_back_to_host_home(monkeypatch, tmp_path):
    monkeypatch.delenv("JIUWENSWARM_DATA_DIR", raising=False)
    monkeypatch.delenv("JIUWENCLAW_DATA_DIR", raising=False)
    monkeypatch.setenv("JIUWENSWARM_HOME", str(tmp_path / "home"))

    assert get_agent_history_root() == str((tmp_path / "home" / ".jiuwenswarm").resolve())


def test_agent_history_root_falls_back_to_user_home(monkeypatch, tmp_path):
    monkeypatch.delenv("JIUWENSWARM_DATA_DIR", raising=False)
    monkeypatch.delenv("JIUWENCLAW_DATA_DIR", raising=False)
    monkeypatch.delenv("JIUWENSWARM_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user-home")

    assert get_agent_history_root() == str((tmp_path / "user-home" / ".jiuwenswarm").resolve())


def test_agent_history_root_falls_back_when_home_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.delenv("JIUWENSWARM_DATA_DIR", raising=False)
    monkeypatch.delenv("JIUWENCLAW_DATA_DIR", raising=False)
    monkeypatch.delenv("JIUWENSWARM_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: (_ for _ in ()).throw(RuntimeError("no home")))
    monkeypatch.setattr(cwd, "get_workspace", lambda: str(tmp_path / "workspace"))

    assert get_agent_history_root() == str((tmp_path / "workspace").resolve())


def test_restricted_history_uses_workspace_inside_sandbox(monkeypatch, tmp_path):
    workspace = tmp_path / "sandbox" / "workspace"
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(tmp_path / "host-data"))
    monkeypatch.setattr(cwd, "get_workspace", lambda: str(workspace))
    monkeypatch.setattr(cwd, "get_project_root", lambda: str(tmp_path / "project"))
    monkeypatch.setattr(cwd, "get_cwd", lambda: str(tmp_path / "project"))
    operation = SimpleNamespace(
        mode="local",
        _run_config=SimpleNamespace(restrict_to_sandbox=True, sandbox_root=[str(tmp_path / "sandbox")]),
    )

    assert get_agent_history_root(operation) == str(workspace.resolve())


def test_restricted_history_keeps_data_root_when_allowed(monkeypatch, tmp_path):
    data_root = tmp_path / "sandbox" / "host-data"
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(data_root))
    operation = SimpleNamespace(
        mode="local",
        _run_config=SimpleNamespace(restrict_to_sandbox=True, sandbox_root=[str(tmp_path / "sandbox")]),
    )

    assert get_agent_history_root(operation) == str(data_root.resolve())


def test_restricted_history_uses_configured_root_when_workspace_is_outside(monkeypatch, tmp_path):
    sandbox = tmp_path / "sandbox"
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(tmp_path / "host-data"))
    monkeypatch.setattr(cwd, "get_workspace", lambda: str(tmp_path / "other-workspace"))
    monkeypatch.setattr(cwd, "get_project_root", lambda: str(tmp_path / "other-project"))
    monkeypatch.setattr(cwd, "get_cwd", lambda: str(tmp_path / "other-project"))
    operation = SimpleNamespace(
        mode="local",
        _run_config=SimpleNamespace(restrict_to_sandbox=True, sandbox_root=[str(sandbox)]),
    )

    assert get_agent_history_root(operation) == str(sandbox.resolve())
