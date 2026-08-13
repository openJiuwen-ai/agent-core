from __future__ import annotations


class _FakeRawResult:
    stdout = "OK"
    stderr = ""
    exit_code = 0


class _FakeCommands:
    def __init__(self) -> None:
        self.calls = []

    def run(self, command, timeout=None, background=False):
        self.calls.append((command, timeout, background))
        return _FakeRawResult()


class _FakeSandbox:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.sandbox_id = "sandbox-1"
        self.commands = _FakeCommands()
        self.killed = False

    def get_port_url(self, port, internal=False):
        return f"http://sandbox/{port}?internal={internal}"

    def kill(self):
        self.killed = True


def test_yuanrong_sandbox_manager_lifecycle():
    from openjiuwen.agent_evolving.agent_rl.online.sandbox import (
        YuanrongSandboxConfig,
        YuanrongSandboxManager,
    )

    created = []

    def factory(**kwargs):
        sandbox = _FakeSandbox(**kwargs)
        created.append(sandbox)
        return sandbox

    config = YuanrongSandboxConfig(image="test-image", install_swerex=False)
    manager = YuanrongSandboxManager(config, sandbox_factory=factory)

    assert manager.sandbox_id == "sandbox-1"
    result = manager.run("echo ok", timeout=3)
    url = manager.ensure_swerex_server(startup_wait_seconds=0)
    manager.close()

    assert result.stdout == "OK"
    assert url == "https://sandbox/8000?internal=False"
    assert created[0].kwargs["image"] == "test-image"
    assert created[0].commands.calls[0] == ("echo ok", 3, False)
    assert created[0].killed is True
