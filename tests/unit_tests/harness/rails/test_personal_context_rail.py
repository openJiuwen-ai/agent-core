from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import openjiuwen.harness.rails.personal_context as personal_context_rail
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.prompts import PromptAttachmentKind, PromptAttachmentManager
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.harness.personal_context.distill import (
    activate_profile_version,
    publish_distilled,
)
from openjiuwen.harness.personal_context.im import ImSearchTool, SqliteImSearchStore
from openjiuwen.harness.rails.personal_context import (
    PersonalContextRail,
    register_im_search_tools,
)


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


def _write_runtime_config(home: Path, text: str = "agent_use_enabled: true\n") -> Path:
    config_path = home / "personal_context.yaml"
    config_path.write_text(text, encoding="utf-8")
    return config_path


def test_agent_use_enabled_requires_strict_boolean_and_valid_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "personal_context.yaml"

    config_path.write_text("agent_use_enabled: true\n", encoding="utf-8")
    assert personal_context_rail._agent_use_enabled(config_path) is True

    config_path.write_text("agent_use_enabled: false\n", encoding="utf-8")
    assert personal_context_rail._agent_use_enabled(config_path) is False

    config_path.write_text("agent_use_enabled: 'true'\n", encoding="utf-8")
    assert personal_context_rail._agent_use_enabled(config_path) is False

    config_path.write_text("agent_use_enabled: 1\n", encoding="utf-8")
    assert personal_context_rail._agent_use_enabled(config_path) is False

    config_path.write_text("agent_use_enabled: [\n", encoding="utf-8")
    assert personal_context_rail._agent_use_enabled(config_path) is False

    config_path.write_text("- enabled\n", encoding="utf-8")
    assert personal_context_rail._agent_use_enabled(config_path) is False

    config_path.unlink()
    assert personal_context_rail._agent_use_enabled(config_path) is False


def test_agent_use_enabled_rejects_directory_and_oversized_config(tmp_path: Path) -> None:
    config_path = tmp_path / "personal_context.yaml"
    config_path.mkdir()
    assert personal_context_rail._agent_use_enabled(config_path) is False

    config_path.rmdir()
    config_path.write_bytes(b"agent_use_enabled: true\n" + b"x" * (4 * 1024 * 1024))
    assert personal_context_rail._agent_use_enabled(config_path) is False


def test_agent_use_enabled_rejects_symlink_without_following_target(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("agent_use_enabled: true\n", encoding="utf-8")
    config_path = tmp_path / "personal_context.yaml"
    try:
        config_path.symlink_to(target)
    except OSError:
        # Windows environments without symlink privileges still exercise the
        # regular-file boundary with a directory at the exact config path.
        config_path.mkdir()
        assert personal_context_rail._agent_use_enabled(config_path) is False
    else:
        assert personal_context_rail._agent_use_enabled(config_path) is False


@pytest.mark.asyncio
async def test_disabled_runtime_clears_previous_section_without_reading_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)
    ctx = _context(agent, [AssistantMessage(content="hello")])

    await rail.before_model_call(ctx)
    assert len(await manager.collect_for_session("session-1")) == 1

    _write_runtime_config(tmp_path, "agent_use_enabled: false\n")
    monkeypatch.setattr(
        personal_context_rail,
        "_read_description",
        lambda _path: pytest.fail("disabled Rail must not read description"),
    )
    await rail.before_model_call(ctx)

    assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
async def test_missing_or_invalid_runtime_config_does_not_read_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)
    ctx = _context(agent, [AssistantMessage(content="hello")])

    monkeypatch.setattr(
        personal_context_rail,
        "_read_description",
        lambda _path: pytest.fail("invalid runtime config must not read description"),
    )
    for config_text in (None, "agent_use_enabled: [\n", "agent_use_enabled: 'true'\n"):
        config_path = tmp_path / "personal_context.yaml"
        if config_text is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_text(config_text, encoding="utf-8")
        await rail.before_model_call(ctx)
        assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
async def test_oversized_runtime_config_does_not_read_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_runtime_config(tmp_path)
    config_path.write_bytes(b"agent_use_enabled: true\n" + b"x" * (4 * 1024 * 1024))
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    monkeypatch.setattr(
        personal_context_rail,
        "_read_description",
        lambda _path: pytest.fail("oversized runtime config must not read description"),
    )
    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
async def test_symlink_runtime_config_does_not_read_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("agent_use_enabled: true\n", encoding="utf-8")
    config_path = tmp_path / "personal_context.yaml"
    try:
        config_path.symlink_to(target)
    except OSError:
        # Preserve an executable path-safety assertion when symlink creation is
        # unavailable on the Windows test runner.
        config_path.mkdir()
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    monkeypatch.setattr(
        personal_context_rail,
        "_read_description",
        lambda _path: pytest.fail("symlink runtime config must not read description"),
    )
    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
async def test_disabled_or_missing_runtime_config_keeps_normal_request_usable(
    tmp_path: Path,
) -> None:
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)
    messages = [AssistantMessage(content="ordinary request")]
    ctx = _context(agent, messages)

    await rail.before_model_call(ctx)
    assert ctx.inputs.messages == messages
    assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
async def test_rail_is_deep_agent_rail_and_injects_fixed_attachment(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    description = context_root / "description.md"
    description.write_text("# Context\n\n内容", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)

    assert isinstance(rail, DeepAgentRail)
    rail.init(agent)
    ctx = _context(agent, [AssistantMessage(content="hello")])
    await rail.before_model_call(ctx)

    items = await manager.collect_for_session("session-1")
    assert len(items) == 1
    item = items[0]
    assert item.section == "personal_context"
    assert item.kind == PromptAttachmentKind.RUNTIME
    assert item.source == "personal_context_rail"
    assert item.priority == 40
    assert item.content_kind == "text/markdown"
    assert "# 主动上下文" in (item.content or "")
    assert str(context_root) in (item.content or "")
    assert "# Context" in (item.content or "")
    assert ctx.inputs.messages == [AssistantMessage(content="hello")]


@pytest.mark.asyncio
async def test_rail_caps_root_at_3000_and_points_to_semantic_root_navigation(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    description_text = "中" * 3001
    (context_root / "description.md").write_text(description_text, encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    [item] = await manager.collect_for_session("session-1")
    content = item.content or ""
    assert f"description_size_bytes: `{len(description_text.encode('utf-8'))}`" in content
    assert f"context_root: `{context_root}`" in content
    assert f"description_path: `{context_root / 'description.md'}`" in content
    assert "sources_description_path" not in content
    assert "从顶层 description.md 开始，按其中相对链接继续读取" in content
    assert "本次仅载入前 3000 个字符" in content
    assert "中" * 3000 in content
    assert "中" * 3001 not in content


@pytest.mark.asyncio
async def test_rail_does_not_mark_exactly_3000_characters_as_truncated(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("x" * 3000, encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
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
            self._file = real_open(path, *args, **kwargs)  # type: ignore[call-overload]

        def __enter__(self) -> "TrackingReader":
            self._file.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._file.__exit__(*args)

        def read(self, size: int) -> str:
            requested_sizes.append(size)
            return self._file.read(size)

    monkeypatch.setattr(Path, "open", lambda path, *args, **kwargs: TrackingReader(path, *args, **kwargs))

    content, total_bytes = personal_context_rail._read_description(description) or ("", 0)

    assert requested_sizes == [3001]
    assert len(content) == 3001
    assert total_bytes == 4000


@pytest.mark.asyncio
async def test_before_call_clears_old_attachment_before_fail_open(tmp_path: Path) -> None:
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)
    valid_ctx = _context(agent, [AssistantMessage(content="hello")])
    await manager.add_section(
        session_id="session-1",
        section="personal_context",
        content="old",
        kind=PromptAttachmentKind.RUNTIME,
        source="personal_context_rail",
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
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, messages))

    assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
async def test_complete_tool_group_allows_injection(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
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
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    if description_text:
        (context_root / "description.md").write_text(description_text, encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    items = await manager.collect_for_session("session-1")
    if expected_marker is None:
        assert items == []
    else:
        assert expected_marker in (items[0].content or "")


@pytest.mark.asyncio
async def test_after_hooks_clear_attachment_and_uninit_detaches_manager(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
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
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    async def cancelled_to_thread(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "to_thread", cancelled_to_thread)
    with pytest.raises(asyncio.CancelledError):
        await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))


@pytest.mark.asyncio
async def test_missing_session_id_is_fail_open(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("content", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")], session_id=None))

    assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
async def test_invalid_utf8_description_is_fail_open(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_bytes(b"valid-prefix\xff")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    assert await manager.collect_for_session("session-1") == []


def _activate_profile(home: Path, job_id: str, *, persona: str, work: str) -> None:
    publish_distilled(
        str(home),
        job_id,
        persona_md=persona,
        work_md=work,
        meta={"job_id": job_id},
        merge_with_existing=False,
    )
    activate_profile_version(str(home), job_id, source="distill")


@pytest.mark.asyncio
async def test_wiki_only_injection_has_no_profile_section(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Wiki\n\nhello", encoding="utf-8")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    [item] = await manager.collect_for_session("session-1")
    content = item.content or ""
    assert "## 当前上下文说明" in content
    assert "# Wiki" in content
    assert "## 现行用户画像" not in content


@pytest.mark.asyncio
async def test_wiki_and_profile_injection_includes_job_id(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("# Wiki\n\nbody", encoding="utf-8")
    _activate_profile(tmp_path, "job-rail", persona="persona-text", work="work-text")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    [item] = await manager.collect_for_session("session-1")
    content = item.content or ""
    assert "## 当前上下文说明" in content
    assert "## 现行用户画像" in content
    assert "job_id: `job-rail`" in content
    assert "source: `distill`" in content
    assert "persona-text" in content
    assert "work-text" in content


@pytest.mark.asyncio
async def test_profile_only_when_description_missing(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    (tmp_path / "workspace" / "context").mkdir(parents=True)
    _activate_profile(tmp_path, "job-only", persona="p", work="w")
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    [item] = await manager.collect_for_session("session-1")
    content = item.content or ""
    assert "## 现行用户画像" in content
    assert "job_id: `job-only`" in content
    assert "## 当前上下文说明" not in content


@pytest.mark.asyncio
async def test_neither_wiki_nor_profile_skips_add_section(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    (tmp_path / "workspace" / "context").mkdir(parents=True)
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    assert await manager.collect_for_session("session-1") == []


@pytest.mark.asyncio
async def test_incomplete_version_does_not_inject_profile(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    context_root = tmp_path / "workspace" / "context"
    context_root.mkdir(parents=True)
    (context_root / "description.md").write_text("wiki-only", encoding="utf-8")
    version = tmp_path / "im" / "profiles" / "versions" / "broken"
    version.mkdir(parents=True)
    (version / "persona.md").write_text("only-persona", encoding="utf-8")
    (tmp_path / "im" / "profiles" / "current.json").write_text(
        '{"job_id": "broken", "published_at_ms": 1, "source": "distill"}\n',
        encoding="utf-8",
    )
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    [item] = await manager.collect_for_session("session-1")
    content = item.content or ""
    assert "wiki-only" in content
    assert "## 现行用户画像" not in content
    assert "only-persona" not in content


@pytest.mark.asyncio
async def test_profile_sections_capped_at_3000_chars(tmp_path: Path) -> None:
    _write_runtime_config(tmp_path)
    (tmp_path / "workspace" / "context").mkdir(parents=True)
    _activate_profile(
        tmp_path,
        "job-long",
        persona="P" * 3001,
        work="W" * 3001,
    )
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(prompt_attachment_manager=manager)
    rail = PersonalContextRail(tmp_path)
    rail.init(agent)

    await rail.before_model_call(_context(agent, [AssistantMessage(content="hello")]))

    [item] = await manager.collect_for_session("session-1")
    content = item.content or ""
    assert "P" * 3000 in content
    assert "P" * 3001 not in content
    assert "W" * 3000 in content
    assert "W" * 3001 not in content
    assert "本次仅载入前 3000 个字符" in content


def _stub_search_tool(name: str = "im_search_messages") -> LocalFunction:
    card = ToolCard(id=name, name=name, description=f"{name} stub", stateless=True)

    async def _func(**_):
        return []

    return LocalFunction(card=card, func=_func)


@pytest.mark.asyncio
async def test_register_im_search_tools_mounts_stub_tools() -> None:
    await Runner.start()
    try:
        am = AbilityManager(owner_id="pc-register")
        agent = SimpleNamespace(ability_manager=am)
        tool = _stub_search_tool()
        register_im_search_tools(agent, [tool])
        assert am.get("im_search_messages") is not None
        assert Runner.resource_mgr.get_tool(tool.card.id) is tool
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_register_im_search_tools_mounts_im_search_tool(tmp_path: Path) -> None:
    """Real ImSearchTool + SqliteImSearchStore share the same home as the rail."""
    await Runner.start()
    try:
        home = tmp_path / "pc_home"
        home.mkdir()
        store = SqliteImSearchStore(home)
        tool = ImSearchTool(store)
        assert tool.card.name == "im_search"

        rail = PersonalContextRail(home)
        assert rail._home == store._home.resolve()

        am = AbilityManager(owner_id="pc-register-im-search")
        agent = SimpleNamespace(ability_manager=am)
        register_im_search_tools(agent, [tool])

        ability = am.get("im_search")
        assert ability is not None
        assert ability.name == "im_search"
        assert Runner.resource_mgr.get_tool(tool.card.id) is tool
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_register_im_search_tools_empty_is_noop() -> None:
    await Runner.start()
    try:
        am = AbilityManager(owner_id="pc-register-empty")
        agent = SimpleNamespace(ability_manager=am)
        register_im_search_tools(agent, [])
        assert am.get("im_search_messages") is None
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_register_im_search_tools_without_ability_manager_is_fail_open() -> None:
    register_im_search_tools(SimpleNamespace(), [_stub_search_tool()])
