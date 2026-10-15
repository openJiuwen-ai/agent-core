# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-08 share_file: listing checks, the listing table, assembly and the Spec size limit (PA-08-V-03)."""

import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.harness.persistent_agent.execution import handoff as HANDOFF
from openjiuwen.harness.persistent_agent.execution.share_file import ShareFileRail, ShareFileTool
from openjiuwen.harness.persistent_agent.ports import PersistentPorts
from openjiuwen.harness.persistent_agent.schema.build_context import PersistentBuildContext
from openjiuwen.harness.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.persistent_agent.schema.spec import PersistentAgentSpec
from openjiuwen.harness.persistent_agent.tools.effect_policy import is_read_only_tool_name
from openjiuwen.harness.persistent_agent.workspace.access import WorkspaceReadScope
from openjiuwen.harness.security.permission_engine.fileguard.file_tool_specs import (
    FileToolSpec,
    lookup_file_tool_specs,
)

pytestmark = pytest.mark.level0

MAX_BYTES = 1024


def execution(spec=None, **changes):
    fields = dict(
        namespace_id="tenant",
        agent_id="agent-a",
        session_id="session-a",
        request_id="request-1",
        execution_id="execution-1",
        config_version="v1",
        config_fingerprint=spec.fingerprint() if spec is not None else "0" * 64,
        source_ref="source:message-1",
        principal_ref="user",
        startup_scope=frozenset({"read"}),
        source_scope=frozenset({"read"}),
        policy_revision="p1",
        capability_snapshot=spec.enabled_features if spec is not None else frozenset({"construction"}),
    )
    fields.update(changes)
    return ExecutionContext(**fields)


class Access:
    def __init__(self, root: Path, status="allowed", **changes):
        self.root = root
        self.status = status
        self.changes = changes
        self.error = None

    async def resolve_read_scope(self, context):
        if self.error is not None:
            raise self.error
        fields = dict(
            status=self.status,
            agent_id=context.agent_id,
            session_id=context.session_id,
            workspace_root=self.root,
            allowed_roots=(self.root,),
            private_sessions_root=self.root / "sessions",
            current_session_root=self.root / "sessions" / context.session_id,
            source_ref="workspace://agent-a",
            policy_revision=context.policy_revision,
        )
        fields.update(self.changes)
        return WorkspaceReadScope(**fields)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    (root / "reports").mkdir(parents=True)
    (root / "reports" / "q3.md").write_bytes(b"third quarter")
    (root / "sessions" / "session-a").mkdir(parents=True)
    (root / "sessions" / "session-a" / "draft.md").write_bytes(b"mine")
    (root / "sessions" / "session-b").mkdir(parents=True)
    (root / "sessions" / "session-b" / "secret.md").write_bytes(b"theirs")
    (tmp_path / "outside.md").write_bytes(b"outside")
    return root


def tool_for(root, registrations=None, access=None, max_bytes=MAX_BYTES):
    return ShareFileTool(
        execution=execution(),
        workspace_access=access or Access(root),
        registrations={} if registrations is None else registrations,
        max_bytes=max_bytes,
    )


async def share(tool, **inputs):
    return await tool.invoke(inputs)


@pytest.mark.asyncio
async def test_a_listed_file_returns_the_fixed_result_and_enters_the_table(workspace):
    table = {}
    tool = tool_for(workspace, table)
    output = await share(tool, file_path="reports//q3.md", share_mode="upload", caption="第三季度")
    assert output.success
    result = json.loads(output.data["content"])
    assert result == {
        "share_file": "registered",
        "file_path": "reports/q3.md",
        "share_mode": "upload",
        "caption": "第三季度",
        "note": HANDOFF.REGISTERED_NOTE,
        "receipt": result["receipt"],
    }
    assert len(result["receipt"]) == 32 and int(result["receipt"], 16) >= 0
    assert table == {("reports/q3.md", "upload"): HANDOFF.Registration("reports/q3.md", "upload", "第三季度")}
    assert tool.render_for_llm(output) == output.data["content"], "the model sees the fixed result"
    own = await share(tool, file_path="sessions/session-a/draft.md", share_mode="attachment", caption="  ")
    assert own.success and json.loads(own.data["content"])["caption"] is None, "a blank caption is none"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inputs",
    [
        {"share_mode": "upload"},
        {"file_path": "", "share_mode": "upload"},
        {"file_path": 3, "share_mode": "upload"},
        {"file_path": "reports/q3.md"},
        {"file_path": "reports/q3.md", "share_mode": "link"},
        {"file_path": "reports/q3.md", "share_mode": "message"},
        {"file_path": "reports/q3.md", "share_mode": "upload", "caption": "x" * 4097},
        {"file_path": "reports/q3.md", "share_mode": "upload", "caption": "bad \ud800"},
        {"file_path": "reports/q3.md", "share_mode": "upload", "caption": ["list"]},
    ],
)
async def test_invalid_arguments_list_nothing(workspace, inputs):
    table = {}
    output = await share(tool_for(workspace, table), **inputs)
    assert not output.success and output.data == {"status": "invalid_arguments"}
    assert table == {}


@pytest.mark.asyncio
async def test_inputs_that_are_not_a_mapping_list_nothing(workspace):
    output = await tool_for(workspace).invoke(None)
    assert output.data == {"status": "invalid_arguments"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("../outside.md", "forbidden"),
        ("/etc/passwd", "forbidden"),
        ("sessions/session-b/secret.md", "forbidden"),
        ("reports", "not_a_file"),
        ("reports/missing.md", "not_found"),
    ],
)
async def test_paths_outside_the_rules_or_not_regular_files_are_refused(workspace, path, status):
    table = {}
    output = await share(tool_for(workspace, table), file_path=path, share_mode="upload")
    assert output.data == {"status": status} and table == {}
    assert str(workspace) not in output.error, "no host path reaches the model"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks and FIFOs need POSIX")
@pytest.mark.asyncio
async def test_symlinks_out_of_the_workspace_and_fifos_are_refused(workspace):
    (workspace / "escape.md").symlink_to(workspace.parent / "outside.md")
    os.mkfifo(workspace / "pipe")
    tool = tool_for(workspace)
    assert (await share(tool, file_path="escape.md", share_mode="upload")).data == {"status": "forbidden"}
    assert (await share(tool, file_path="pipe", share_mode="upload")).data == {"status": "not_a_file"}


@pytest.mark.skipif(sys.platform == "win32", reason="control characters in file names need POSIX")
@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["bad\x01.md", "bad\x7f.md"])
async def test_a_path_an_output_event_cannot_carry_is_refused(workspace, name):
    (workspace / name).write_bytes(b"x")
    table = {}
    output = await share(tool_for(workspace, table), file_path=name, share_mode="upload")
    assert output.data == {"status": "invalid_arguments"} and table == {}


@pytest.mark.asyncio
async def test_a_file_over_the_limit_is_refused_when_listed(workspace):
    output = await share(tool_for(workspace, max_bytes=5), file_path="reports/q3.md", share_mode="upload")
    assert output.data == {"status": "too_large"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("access", "status"),
    [("forbidden", "forbidden"), ("unknown", "unknown"), ("mismatch", "unknown"), ("raises", "unknown")],
)
async def test_authorization_fails_closed(workspace, access, status):
    port = {
        "forbidden": Access(workspace, status="forbidden"),
        "unknown": Access(workspace, status="unknown"),
        "mismatch": Access(workspace, agent_id="agent-b"),
        "raises": Access(workspace),
    }[access]
    if access == "raises":
        port.error = RuntimeError(f"lookup failed for {workspace}")
    table = {}
    output = await share(tool_for(workspace, table, port), file_path="reports/q3.md", share_mode="upload")
    assert output.data == {"status": status} and table == {}
    assert str(workspace) not in output.error


@pytest.mark.asyncio
async def test_repeated_listings_merge_and_the_limit_applies_to_the_merged_table(workspace):
    table = {}
    tool = tool_for(workspace, table)
    await share(tool, file_path="reports/q3.md", share_mode="upload", caption="first")
    await share(tool, file_path="sessions/session-a/draft.md", share_mode="upload")
    await share(tool, file_path="reports/q3.md", share_mode="upload", caption="latest")
    await share(tool, file_path="reports/q3.md", share_mode="attachment")
    assert list(table) == [
        ("reports/q3.md", "upload"),
        ("sessions/session-a/draft.md", "upload"),
        ("reports/q3.md", "attachment"),
    ]
    assert table[("reports/q3.md", "upload")].caption == "latest"

    full = {(f"f{index}", "upload"): None for index in range(HANDOFF.MAX_REGISTRATIONS)}
    tool = tool_for(workspace, full)
    output = await share(tool, file_path="reports/q3.md", share_mode="upload")
    assert output.data == {"status": "too_many_files"} and ("reports/q3.md", "upload") not in full


def test_share_file_is_a_read_for_the_permission_engine_and_has_no_side_effect():
    assert lookup_file_tool_specs("share_file") == [FileToolSpec("share_file", "file_path", "read")]
    assert is_read_only_tool_name("share_file") is True


def test_the_tool_card_offers_two_share_modes_and_no_target(workspace):
    card = tool_for(workspace).card
    assert card.name == "share_file" and card.idempotent is True
    assert card.input_params["properties"]["share_mode"]["enum"] == ["upload", "attachment"]
    assert set(card.input_params["properties"]) == {"file_path", "share_mode", "caption"}
    assert card.input_params["required"] == ["file_path", "share_mode"]


class AbilityManager:
    def __init__(self):
        self.added = []
        self.removed = []

    def add_ability(self, card, tool):
        self.added.append((card.name, tool))

    def remove_ability(self, name):
        self.removed.append(name)


def test_the_rail_registers_one_tool_bound_to_the_execution_table(workspace):
    table = {}
    rail = ShareFileRail(execution(), Access(workspace), table, MAX_BYTES)
    agent = type("Agent", (), {"ability_manager": AbilityManager()})()
    rail.init(agent)
    rail.init(agent)
    assert [name for name, _ in agent.ability_manager.added] == ["share_file"]
    assert agent.ability_manager.added[0][1]._registrations is table
    rail.uninit(agent)
    assert agent.ability_manager.removed == ["share_file"] and rail.tools == []


class UntouchedKVStore(BaseKVStore):
    def _touched(self, *args, **kwargs):
        raise AssertionError("assembly must not access the window backend")

    set = exclusive_set = get = exists = delete = get_by_prefix = _touched
    delete_by_prefix = mget = batch_delete = pipeline = _touched


class HostPorts:
    async def record(self, *args):
        raise AssertionError("assembly must not record events")

    async def read_execution_log(self, *args):
        raise AssertionError("assembly must not read logs")

    async def admit(self, *args):
        raise AssertionError("assembly must not admit model calls")

    async def report(self, *args):
        raise AssertionError("assembly must not report usage")


@pytest.mark.parametrize("features", [{"construction"}, {"construction", "workspace_read"}])
def test_share_file_is_assembled_only_with_workspace_read(workspace, features):
    spec = PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enable_task_loop=False,
        auto_create_workspace=False,
        enabled_features=frozenset(features),
        share_file_max_bytes=2048,
    )
    context = PersistentBuildContext(
        execution=execution(spec),
        session=create_agent_session(session_id="session-a"),
        ports=PersistentPorts(
            commit=HostPorts(),
            budget=HostPorts(),
            window_backend=UntouchedKVStore(),
            workspace_access=Access(workspace),
        ),
    )
    agent = spec.build(context=context)
    try:
        rails = agent.find_rails_by_type((ShareFileRail,))
        if "workspace_read" in features:
            assert len(rails) == 1 and rails[0].registrations is context.binding.control.registrations
            assert rails[0].max_bytes == 2048
        else:
            assert rails == []
    finally:
        agent.ability_manager.teardown_tools()


def test_the_size_limit_is_bounded_and_part_of_the_fingerprint():
    card = AgentCard(id="agent-a", name="test")
    default = PersistentAgentSpec(card=card)
    assert default.share_file_max_bytes == 32 * 1024 * 1024
    assert PersistentAgentSpec(card=card, share_file_max_bytes=1024**3).share_file_max_bytes == 1024**3
    for invalid in (0, -1, 1024**3 + 1):
        with pytest.raises(ValidationError):
            PersistentAgentSpec(card=card, share_file_max_bytes=invalid)
    assert PersistentAgentSpec(card=card, share_file_max_bytes=1024).fingerprint() != default.fingerprint()
