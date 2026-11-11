# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cross-group artifact contracts, approval interruption and Session boundaries."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from openjiuwen.harness import persistent_agent as core
from openjiuwen.harness.persistent_agent.tools.group_history import PersistentGroupMessageSearchTool
from openjiuwen.harness.persistent_agent.workspace.access import validate_read_scope, validate_write_scope
from openjiuwen.harness.persistent_agent.workspace.path_policy import (
    WorkspacePathError,
    resolve_read_path,
    resolve_write_path,
)

pytestmark = pytest.mark.level0


@pytest.fixture
def execution():
    return core.ExecutionContext(
        namespace_id="tenant", agent_id="agent", session_id="destination", request_id="request",
        execution_id="execution", source_ref="source", config_version="v1", config_fingerprint="a" * 64,
        principal_ref="requester", startup_scope=frozenset({"workspace_read", "workspace_write"}),
        source_scope=frozenset({"workspace_read", "workspace_write"}), policy_revision="p1",
        capability_snapshot=frozenset({"construction"}),
    )


def artifact(**changes):
    return dict(
        group_id="source-group", group_name="Source group", artifact_id="artifact",
        artifact_kind="file", title="report.txt", file_name="report.txt", file_size=5,
        file_digest="sha256:" + "a" * 64, source_session_id="origin",
        source_file_path="sessions/origin/output/report.txt", created_at=datetime.now(timezone.utc),
        imported_path="sessions/destination/imports/report.txt", snippet="approved result",
    ) | changes


def test_query_selects_content_without_losing_keyword_normalization():
    query = core.GroupMessageQuery(keywords=["Report", "report"], content_types=["files", "online_docs"])
    assert query.content_types == ("files", "online_docs")
    assert query.search_terms == ("Report",)
    assert core.GroupMessageQuery(query="report").content_types == ("messages", "files", "online_docs")
    for fields in ({"content_types": []}, {"content_types": ["files", "files"]},
                   {"content_types": ["files"], "sender_id": "sender"}):
        with pytest.raises(ValidationError):
            core.GroupMessageQuery(query="report", **fields)


@pytest.mark.asyncio
async def test_content_search_returns_import_without_source_workspace_locators(execution):
    calls = []

    class Host:
        async def search_content(self, bound, query):
            calls.append((bound, query))
            return core.GroupContentSearchResult(
                **core.InvocationRef.from_execution(bound).model_dump(), status="found", query_complete=True,
                artifacts=[artifact()],
            )

        async def search_messages(self, bound, query):
            pytest.fail("content search must use the combined host entry")

    tool = PersistentGroupMessageSearchTool(execution=execution, group_chat=Host())
    result = await tool.invoke({"query": "report", "content_types": ["files"]})
    shown = json.loads(tool.render_for_llm(result))
    assert result.success
    assert calls[0][0] is execution and calls[0][1].content_types == ("files",)
    assert shown["artifacts"][0]["imported_path"] == "sessions/destination/imports/report.txt"
    assert not {"source_file_path", "source_session_id", "file_digest"} & shown["artifacts"][0].keys()


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["messages", "files", "online_docs"])
async def test_all_content_types_use_owned_confirmation_before_release(execution, content_type):
    from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
    from openjiuwen.harness.persistent_agent.tools.group_history import GROUP_SHARE_INTERRUPT_OWNER

    class Host:
        async def search_content(self, bound, query):
            raise core.GroupContentApprovalRequired("PRIVATE-CANDIDATE", approval_id="approval")

    tool = PersistentGroupMessageSearchTool(execution=execution, group_chat=Host())
    with pytest.raises(ToolInterruptException) as caught:
        await tool.invoke({"query": "report", "content_types": [content_type]})
    pending = caught.value.request
    assert pending.interrupt_owner == GROUP_SHARE_INTERRUPT_OWNER
    assert pending.interrupt_kind == "confirm"
    assert pending.metadata == {"kind": "group_share", "approval_id": "approval"}
    assert "PRIVATE-CANDIDATE" not in pending.model_dump_json()
    assert issubclass(core.GroupMessageApprovalRequired, core.GroupContentApprovalRequired)


@pytest.mark.parametrize("changes", [
    {"source_file_path": "sessions/origin/draft.txt"},
    {"imported_path": "sessions/other/imports/report.txt"},
])
def test_artifact_result_rejects_drafts_and_other_destination_sessions(execution, changes):
    with pytest.raises(ValidationError):
        core.GroupMessageSearchResult(
            **core.InvocationRef.from_execution(execution).model_dump(), status="found", query_complete=True,
            artifacts=[artifact(**changes)],
        )


@pytest.mark.asyncio
async def test_legacy_message_host_rejects_explicit_artifact_search(execution):
    calls = []

    class Host(core.GroupChatPort):
        async def search_messages(self, bound, query):
            calls.append(query)
            return core.GroupMessageSearchResult(
                **core.InvocationRef.from_execution(bound).model_dump(), status="empty", query_complete=True,
            )

    tool = PersistentGroupMessageSearchTool(execution=execution, group_chat=Host())
    assert (await tool.invoke({"query": "report"})).success
    assert not (await tool.invoke({"query": "report", "content_types": ["files"]})).success
    assert len(calls) == 1
    assert calls[0].content_types == ("messages",)


@pytest.mark.asyncio
async def test_legacy_message_host_can_opt_out_of_search_content(execution):
    calls = []

    class Host(core.GroupChatPort):
        async def search_messages(self, bound, query):
            calls.append(query)
            return core.GroupMessageSearchResult(
                **core.InvocationRef.from_execution(bound).model_dump(), status="empty", query_complete=True,
            )

        async def search_content(self, bound, query):
            raise NotImplementedError

    tool = PersistentGroupMessageSearchTool(execution=execution, group_chat=Host())
    assert (await tool.invoke({"query": "report", "content_types": ["messages"]})).success
    assert calls[0].content_types == ("messages",)
    assert not (await tool.invoke({"query": "report", "content_types": ["online_docs"]})).success
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_search_content_returning_none_falls_back_to_message_search(execution):
    calls = []

    class Host(core.GroupChatPort):
        async def search_messages(self, bound, query):
            calls.append(query)
            return core.GroupMessageSearchResult(
                **core.InvocationRef.from_execution(bound).model_dump(), status="empty", query_complete=True,
            )

        async def search_content(self, bound, query):
            return None

    tool = PersistentGroupMessageSearchTool(execution=execution, group_chat=Host())
    assert (await tool.invoke({"query": "report", "content_types": ["messages"]})).success
    assert calls[0].content_types == ("messages",)
    assert not (await tool.invoke({"query": "report", "content_types": ["files"]})).success
    assert len(calls) == 1


def test_full_access_can_keep_host_granted_session_boundary(execution, tmp_path):
    current = tmp_path / "sessions" / execution.session_id
    current.mkdir(parents=True)
    identity = dict(
        status="allowed", agent_id=execution.agent_id, session_id=execution.session_id,
        workspace_root=tmp_path, private_sessions_root=tmp_path / "sessions", current_session_root=current,
        source_ref="workspace://agent", policy_revision=execution.policy_revision,
        principal_ref=execution.principal_ref, host_wide=False,
    )
    read = core.WorkspaceReadScope(**identity, allowed_roots=())
    write = core.WorkspaceWriteScope(**identity, allowed_write_roots=())
    assert validate_read_scope(read, execution, permission_profile="full_access") is read
    assert validate_write_scope(write, execution, permission_profile="full_access") is write
    for resolver, scope in ((resolve_read_path, read), (resolve_write_path, write)):
        assert resolver(scope, "sessions/destination/report.txt").path == current / "report.txt"
        with pytest.raises(WorkspacePathError):
            resolver(scope, "sessions/other/report.txt")
