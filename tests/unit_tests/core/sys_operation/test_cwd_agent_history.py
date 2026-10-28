"""Agent history uses the same data root as its jiuwenswarm host."""

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
