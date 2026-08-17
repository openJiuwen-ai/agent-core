from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import openjiuwen.harness.rails.proactive_context as proactive_context_rail
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.prompts import PromptAttachmentKind, PromptAttachmentManager
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.proactive_context import PCSContextRail


def _context(
    manager_agent: object,
    messages: list[object],
    *,
    session_id: str | None = "session-1",
) -> AgentCallbackContext:
    return AgentCallbackContext(
        agent=manager_agent,
        inputs=ModelCallInputs(messages=messages),
        session=SimpleNamespace(session_id=session_id),
    )


def _tool_group() -> list[object]:
    return [
        AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
        ),
        ToolMessage(content="ok", tool_call_id="call-1"),
    ]


@pytest.mark.asyncio
async def test_rail_is_deep_agent_rail_and_injects_fixed_attachment(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    description = context_root / "description.md"
    description.write_text("# Context\n\n内容", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)

    assert isinstance(rail, DeepAgentRail)
    rail.init(agent)
    ctx = _context(agent, [AssistantMessage(content="hello")])
    await rail.before_model_call(ctx)

    items = await manager.collect_for_session("session-1")
    assert len(items) == 1
    item = items[0]
    assert item.section == "proactive_context"
    assert item.kind == PromptAttachmentKind.RUNTIME
    assert item.source == "pcs_context_rail"
    assert item.priority == 40
    assert item.content_kind == "text/markdown"
    assert "# 主动上下文" in (item.content or "")
    assert str(context_root) in (item.content or "")
    assert "# Context" in (item.content or "")
    assert ctx.inputs.messages == [AssistantMessage(content="hello")]


@pytest.mark.asyncio
async def test_rail_caps_root_at_3000_and_reports_total_bytes_and_sources_path(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    description_text = "中" * 3001
    (context_root / "description.md").write_text(description_text, encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    [item] = await manager.collect_for_session("session-1")
    content = item.content or ""
    assert f"description_size_bytes: `{len(description_text.encode('utf-8'))}`" in content
    assert f"sources_description_path: `{context_root / 'sources' / 'description.md'}`" in content
    assert "本次仅载入前 3000 个字符" in content
    assert "中" * 3000 in content
    assert "中" * 3001 not in content


@pytest.mark.asyncio
async def test_rail_does_not_mark_exactly_3000_characters_as_truncated(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("x" * 3000, encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    [item] = await manager.collect_for_session("session-1")
    assert "x" * 3000 in (item.content or "")
    assert "本次仅载入前 3000 个字符" not in (item.content or "")


def test_read_description_requests_only_one_character_beyond_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    description = tmp_path / "description.md"
    description.write_text("x" * 4000, encoding="utf-8")
    real_open = Path.open
    requested_sizes: list[int] = []

    class TrackingReader:
        def __init__(self, path: Path, *args: object, **kwargs: object) -> None:
            self._file = real_open(path, *args, **kwargs)

        def __enter__(self) -> "TrackingReader":
            self._file.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._file.__exit__(*args)

        def read(self, size: int) -> str:
            requested_sizes.append(size)
            return self._file.read(size)

    monkeypatch.setattr(Path, "open", lambda path, *args, **kwargs: TrackingReader(path, *args, **kwargs))

    content, total_bytes = proactive_context_rail._read_description(description) or ("", 0)

    assert requested_sizes == [3001]
    assert len(content) == 3001
    assert total_bytes == 4000


@pytest.mark.asyncio
async def test_before_call_clears_old_attachment_before_fail_open(tmp_path: Path) -> None:
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)
    rail.init(agent)
    valid_ctx = _context(agent, [AssistantMessage(content="hello")])
    await manager.add_section(
        session_id="session-1",
        section="proactive_context",
        content="old",
        kind=PromptAttachmentKind.RUNTIME,
        source="pcs_context_rail",
    )

    await rail.before_model_call(valid_ctx)
    assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "messages",
    [
        [ToolMessage(content="orphan", tool_call_id="call-1")],
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="call-1", type="function", name="read", arguments="{}"),
                    ToolCall(id="call-1", type="function", name="read", arguments="{}"),
                ],
            ),
        ],
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
            ),
            AssistantMessage(content="not a result"),
        ],
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
            ),
            ToolMessage(content="wrong", tool_call_id="call-2"),
        ],
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
            ),
        ],
    ],
)
async def test_invalid_tool_message_continuity_does_not_inject(
    tmp_path: Path,
    messages: list[object],
) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, messages))

    assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
async def test_complete_tool_group_allows_injection(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, _tool_group()))

    assert len(await manager.collect_for_session("session-1")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "description_text, expected_marker",
    [("", None), ("x" * 3001, "本次仅载入前 3000 个字符")],
)
async def test_missing_empty_and_long_description_are_fail_open(
    tmp_path: Path,
    description_text: str,
    expected_marker: str | None,
) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    if description_text:
        (context_root / "description.md").write_text(description_text, encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    items = await manager.collect_for_session("session-1")
    if expected_marker is None:
        assert items == []
    else:
        assert expected_marker in (items[0].content or "")


@pytest.mark.asyncio
async def test_after_hooks_clear_attachment_and_uninit_detaches_manager(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)
    rail.init(agent)
    ctx = _context(agent, [AssistantMessage(content="hello")])

    await rail.before_model_call(ctx)
    await rail.after_model_call(ctx)
    assert await manager.collect_for_session("session-1") == []
    await rail.before_model_call(ctx)
    await rail.after_invoke(ctx)
    assert await manager.collect_for_session("session-1") == []
    rail.uninit(agent)
    assert rail._attachment_manager is None


@pytest.mark.asyncio
async def test_cancelled_file_read_is_propagated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)
    rail.init(agent)

    async def cancelled_to_thread(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "to_thread", cancelled_to_thread)
    with pytest.raises(asyncio.CancelledError):
        await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))


@pytest.mark.asyncio
async def test_missing_session_id_is_fail_open(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")], session_id=None))

    assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
async def test_invalid_utf8_description_is_fail_open(tmp_path: Path) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_bytes(b"valid-prefix\xff")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PCSContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    assert await manager.collect_for_session("session-1") == []
