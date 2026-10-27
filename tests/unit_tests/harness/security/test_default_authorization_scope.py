"""Missing client fields select the server default; explicit exact remains exact."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmPayload
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, InterruptResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.models import PermissionConfirmResponse


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["file", "url", "shell", "single_file_shell", "single_url_shell", "unsupported"])
@pytest.mark.parametrize("reply", ["omitted", "exact", "allow", "scope", "mode"])
@pytest.mark.parametrize("hosted", [False, True])
async def test_default_and_explicit_scope_round_trip(tmp_path, kind, reply, hosted):
    cfg = {
        "enabled": True, "package_builtin_rules": False, "tools": {"bash": "ask"},
        "defaults": {"*": "allow"},
        "file_guard": {"enabled": True, "defaults": {"read": "ask"}, "workspace": {"read": "ask"}},
        "net_guard": {"enabled": True, "defaults": "ask", "urls": {}},
    }
    tool, args, reusable, default = {
        "file": ("read_file", {"file_path": str(tmp_path / "a.txt")},
                 {"file_path": str(tmp_path / "b.txt")}, "parent"),
        "url": ("mcp_fetch_webpage", {"url": "https://api.example.co.uk/a"},
                {"url": "https://other.example.co.uk/b"}, "domain"),
        "shell": ("bash", {"command": "chmod --version && chown --version", "workdir": str(tmp_path)},
                  {"command": "chmod --version", "workdir": str(tmp_path)}, "subcommands"),
        "single_file_shell": ("bash", {"command": "cat ./secret.txt", "workdir": str(tmp_path)},
                              {"command": "cat ./other.txt", "workdir": str(tmp_path)}, "subcommands"),
        "single_url_shell": ("bash", {"command": "curl https://api.example.co.uk/a", "workdir": str(tmp_path)},
                             {"command": "curl https://api.example.co.uk/b", "workdir": str(tmp_path)}, "subcommands"),
        "unsupported": ("bash", {"command": "echo A > a.txt", "workdir": str(tmp_path)},
                        {"command": "echo B > a.txt", "workdir": str(tmp_path)}, "exact"),
    }[kind]
    fields = {
        "omitted": {}, "exact": {"authorization_scope": "exact"}, "allow": {"authorization_mode": "allow"},
        "scope": {"authorization_scope": default}, "mode": {"authorization_mode": "allow_with_scope"},
    }[reply]
    response = {"approved": True, "auto_confirm": True, "persist_allow": False, **fields}
    writes = []

    def save(updated, *, approval_grants=None):
        cfg.update(deepcopy(updated))
        writes.append(updated)
        return True

    async def confirm(_request):
        return PermissionConfirmResponse(**response)

    rail = PermissionInterruptRail(config=cfg, host=ToolPermissionHost(
        resolve_workspace_dir=lambda: tmp_path, get_permissions_snapshot=lambda: cfg,
        persist_session_allow_rule=save, request_permission_confirmation=confirm if hosted else None,
    ))
    state = {}
    session = SimpleNamespace(session_id="defaults", get_state=state.get, update_state=state.update)
    ctx = AgentCallbackContext(agent=object(), session=session)
    call = ToolCall(id="approval", type="function", name=tool, arguments=json.dumps(args))
    if hosted:
        result = await rail.resolve_interrupt(ctx, call, None)
    else:
        assert isinstance(await rail.resolve_interrupt(ctx, call, None), InterruptResult)
        # JSON and typed Pydantic replies must both preserve missing fields.
        user_input = json.dumps(response) if reply == "omitted" else ConfirmPayload(**response)
        result = await rail.resolve_interrupt(ctx, call, user_input)
    assert isinstance(result, ApproveResult)
    assert len(writes) == 1
    expected = "exact" if reply in {"exact", "allow"} else default
    grants = cfg["approval_overrides"]
    assert len(grants) == (2 if expected == "subcommands" else 1)
    assert all(json.loads(grant["pattern"])["scope"] == (
        "exact" if expected == "subcommands" else expected
    ) for grant in grants)
    if expected == "subcommands" and kind.startswith("single_"):
        assert [grant["match_type"] for grant in grants] == ["shell_subcommand", "exact_operation"]
        assert not state
    else:
        assert all(grant["match_type"] == (
            "shell_subcommand" if expected == "subcommands" else "exact_operation"
        ) for grant in grants)
    engine = PermissionEngine(cfg, workspace_root=tmp_path)
    assert (await engine.check_permission(tool, args)).is_allowed
    assert (await engine.check_permission(tool, reusable)).is_allowed == (
        expected != "exact" and not kind.startswith("single_")
    )
