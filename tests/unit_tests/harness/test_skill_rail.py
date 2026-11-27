# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import builtins
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

import openjiuwen.harness.rails.skills.skill_use_rail as skill_use_rail_module
from openjiuwen.core.foundation.llm.model import init_model
from openjiuwen.core.foundation.llm.schema.message import SystemMessage
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperation,
    SysOperationCard,
)
from openjiuwen.core.sys_operation.result import ReadFileData, ReadFileResult
from openjiuwen.harness import Workspace
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts.builder import PromptSection, SystemPromptBuilder
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.tools import ListSkillTool
from openjiuwen.harness.tools.skills.skill_tool import SKILL_TOOL_MARKDOWN_IMAGES_HINT


class _DummyResponse:
    def __init__(self, content: str):
        self.content = content


class _DummyModel:
    def __init__(self, content: str):
        self._content = content
        self.calls: List[Dict[str, Any]] = []

    async def invoke(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return _DummyResponse(self._content)


class _DummyEvolutionStore:
    def __init__(self, texts: Dict[str, str]):
        self.texts = texts

    async def format_desc_experience_text(self, skill_name: str) -> str:
        return self.texts.get(skill_name, "")


class _SessionState:
    def __init__(self, session_id: str, state: Dict[str, Any] | None = None):
        self.session_id = session_id
        self.state = state or {}

    def get_session_id(self) -> str:
        return self.session_id

    def get_state(self, key: str | None = None):
        return self.state if key is None else self.state.get(key)

    def update_state(self, data: Dict[str, Any]) -> None:
        self.state.update(data)


class _TrackingSkillUseRail(SkillUseRail):
    """Test-only SkillUseRail that records _load_skill calls via subclass override."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.load_calls: List[str] = []

    async def _load_skill(self, skill_dir, update_at):
        self.load_calls.append(skill_dir.name)
        return await super()._load_skill(skill_dir, update_at)


def _write_skill(
    root: Path,
    name: str,
    description: str,
    body: str = "",
    *,
    description_cn: str | None = None,
    description_en: str | None = None,
) -> Path:
    """Create a minimal skill directory with SKILL.md."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    frontmatter = ["---", f"description: {description}"]
    if description_cn is not None:
        frontmatter.append(f"description_cn: {description_cn}")
    if description_en is not None:
        frontmatter.append(f"description_en: {description_en}")
    frontmatter.extend(["---", "", f"# {name}", body])
    skill_md.write_text("\n".join(frontmatter), encoding="utf-8")
    return skill_dir


def _long_front_matter_text(name: str, description: str, padding: int = 80) -> str:
    """Return SKILL.md text whose YAML front matter exceeds the 64-line probe window.

    ``description`` is placed after the padding keys so a bounded head read
    cannot reach it and the loader has to fall back to reading the whole file.
    """
    lines = ["---"]
    lines.extend(f"meta_{index:03d}: value {index}" for index in range(padding))
    lines.extend([f"description: {description}", "---", "", f"# {name}"])
    return "\n".join(lines)


def _write_long_front_matter_skill(root: Path, name: str, description: str, padding: int = 80) -> Path:
    """Create a skill whose YAML front matter exceeds the 64-line probe window."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(_long_front_matter_text(name, description, padding), encoding="utf-8")
    return skill_dir


def _rewrite_skill(skill_md: Path, description: str, *, mtime: float) -> None:
    """Give an existing SKILL.md a new description and an exact mtime."""
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    lines = [f"description: {description}" if line.startswith("description:") else line for line in lines]
    skill_md.write_text("\n".join(lines), encoding="utf-8")
    os.utime(skill_md, (mtime, mtime))


class _RecordingLogger:
    """Stand-in for the module logger that records formatted messages per level."""

    def __init__(self):
        self.messages: List[Tuple[str, str]] = []

    def __getattr__(self, level: str):
        def _log(message: str, *args: Any) -> None:
            self.messages.append((level, message % args if args else str(message)))

        return _log


class _ReadFileSpy:
    """Wraps a real fs operation and records every read_file call."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.reads: List[Tuple[str, Optional[int]]] = []

    async def read_file(self, path: str, **kwargs: Any):
        self.reads.append((path, kwargs.get("head")))
        return await self._inner.read_file(path, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _CannedBackend:
    """sys_operation double whose fs().read_file serves one fixed SKILL.md text.

    Honors ``head`` the way LocalFS does (the first *head* lines, none for a
    negative head, the whole text otherwise) and records every ``(path, head)``
    pair so a test can prove which backend answered and with which head budget.
    With *gate* set, every read sets *started* and then parks on the gate, so
    a test can rebind the rail while a read is in flight and release it later.
    """

    def __init__(
        self,
        skill_md_text: str,
        *,
        started: Optional[asyncio.Event] = None,
        gate: Optional[asyncio.Event] = None,
    ):
        self._text = skill_md_text
        self._started = started
        self._gate = gate
        self.reads: List[Tuple[str, Optional[int]]] = []

    def fs(self):
        return self

    async def read_file(self, path: str, **kwargs: Any):
        head = kwargs.get("head")
        self.reads.append((path, head))
        if self._gate is not None:
            self._started.set()
            await self._gate.wait()
        text = self._text
        if head is not None:
            text = "".join(text.splitlines(True)[:head]) if head > 0 else ""
        return ReadFileResult(
            code=0,
            message="success",
            data=ReadFileData(path=path, content=text, mode="text"),
        )


class _TwoPausedReadsBackend(_CannedBackend):
    """Gate the first two reads independently and leave any retry free to finish."""

    def __init__(self, description: str, *, description_en: str | None = None):
        localized = f"description_en: {description_en}\n" if description_en is not None else ""
        super().__init__(f"---\ndescription: {description}\n{localized}---\n")
        self.started = [asyncio.Event(), asyncio.Event()]
        self.resume = [asyncio.Event(), asyncio.Event()]
        self.first_read_finished = asyncio.Event()

    async def read_file(self, path: str, **kwargs: Any):
        index = len(self.reads)
        result = await super().read_file(path, **kwargs)
        if index < len(self.started):
            self.started[index].set()
            await self.resume[index].wait()
            if index == 0:
                self.first_read_finished.set()
        return result


def _fail_second_skill_load(rail: SkillUseRail, backend: _TwoPausedReadsBackend, monkeypatch: pytest.MonkeyPatch):
    """Fail the producer after its read while letting the waiter's retry load the skill."""
    load_skill = rail._load_skill

    async def fail_second_load(skill_dir: Path, update_at: float):
        is_second_read = len(backend.reads) == 1
        skill = await load_skill(skill_dir, update_at)
        if is_second_read:
            raise RuntimeError("skill scan failed")
        return skill

    monkeypatch.setattr(rail, "_load_skill", fail_second_load)


class _LocalLibraryBackend:
    """sys_operation double serving the real SKILL.md texts of a library, parking on one skill's read.

    The texts are read from *root* once, at construction, so a test needs no
    second copy of what ``_write_skill`` wrote and the async read does no
    I/O. ``head`` is honoured like LocalFS. Only a read whose path contains
    *pause_on* sets *started* and parks on *gate*, so a test can change
    something under one specific skill's read in a multi-skill scan.
    """

    def __init__(self, root: Path, *, pause_on: str, started: asyncio.Event, gate: asyncio.Event):
        self._texts = {
            skill_dir.name: (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            for skill_dir in root.iterdir()
            if skill_dir.is_dir()
        }
        self._pause_on = pause_on
        self._started = started
        self._gate = gate
        self.reads: List[str] = []

    def fs(self):
        return self

    async def read_file(self, path: str, **kwargs: Any):
        self.reads.append(path)
        if self._pause_on in path:
            self._started.set()
            await self._gate.wait()
        text = self._texts[Path(path).parent.name]
        head = kwargs.get("head")
        if head is not None:
            text = "".join(text.splitlines(True)[:head]) if head > 0 else ""
        return ReadFileResult(
            code=0,
            message="success",
            data=ReadFileData(path=path, content=text, mode="text"),
        )


class _ParkingDiskBackend:
    """sys_operation double reading the real SKILL.md; one read parks with the bytes it started with.

    Every read takes its text from disk at call time, so a later scan sees an
    edit made meanwhile. Only the first read whose path contains *park_on*
    parks: it sets *started* and waits on *gate* *after* taking its text, so
    the scan that parked lands with the bytes that existed when it started.
    """

    def __init__(self, park_on: str, started: asyncio.Event, gate: asyncio.Event):
        self._park_on = park_on
        self._started = started
        self._gate = gate

    @staticmethod
    def _disk_text(path: str, head: Optional[int]) -> str:
        text = Path(path).read_text(encoding="utf-8")
        if head is not None:
            text = "".join(text.splitlines(True)[:head]) if head > 0 else ""
        return text

    def fs(self):
        return self

    async def read_file(self, path: str, **kwargs: Any):
        text = self._disk_text(path, kwargs.get("head"))
        if self._park_on and self._park_on in path:
            self._park_on = None
            self._started.set()
            await self._gate.wait()
        return ReadFileResult(
            code=0,
            message="success",
            data=ReadFileData(path=path, content=text, mode="text"),
        )


class _CountingTextHandle:
    """Text handle proxy counting every character handed out through read() or iteration."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.consumed_chars = 0

    def read(self, *args: Any) -> str:
        data = self._inner.read(*args)
        self.consumed_chars += len(data)
        return data

    def __iter__(self) -> "_CountingTextHandle":
        return self

    def __next__(self) -> str:
        line = next(self._inner)
        self.consumed_chars += len(line)
        return line

    def __enter__(self) -> "_CountingTextHandle":
        return self

    def __exit__(self, *exc: Any) -> Any:
        return self._inner.__exit__(*exc)


class _CountingOpen:
    """``open`` stand-in that wraps every text handle in a _CountingTextHandle."""

    def __init__(self):
        self.handles: List[_CountingTextHandle] = []

    def __call__(self, path: Any, *args: Any, **kwargs: Any) -> _CountingTextHandle:
        handle = _CountingTextHandle(builtins.open(path, *args, **kwargs))
        self.handles.append(handle)
        return handle

    @property
    def consumed_chars(self) -> int:
        return sum(handle.consumed_chars for handle in self.handles)


def _resolved(paths) -> List[str]:
    """Resolve paths for comparison, outside the async test bodies."""
    return [str(Path(path).resolve()) for path in paths]


def _make_sys_operation(tmp_path: Path):
    """Create a local SysOperation for tests."""
    card = SysOperationCard(
        id=f"test_skill_rail_sysop_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(card)
    return Runner.resource_mgr.get_sys_operation(card.id)


def _make_sandboxed_sys_operation(
    tmp_path: Path, name: str, sandbox_root: Path, monkeypatch: pytest.MonkeyPatch
) -> Tuple[Any, _ReadFileSpy]:
    """Create a LOCAL SysOperation confined to *sandbox_root*, spying on its reads."""
    card = SysOperationCard(
        id=f"test_skill_rail_sysop_{tmp_path.name}_{name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(sandbox_root=[str(sandbox_root)], restrict_to_sandbox=True),
    )
    Runner.resource_mgr.add_sys_operation(card)
    sys_operation = Runner.resource_mgr.get_sys_operation(card.id)
    spy = _ReadFileSpy(sys_operation.fs())
    monkeypatch.setattr(sys_operation, "fs", lambda: spy)
    return sys_operation, spy


def _make_agent(sys_operation, workspace, *, language: str | None = None):
    """Create a DeepAgent for tests."""
    model = init_model(
        provider="OpenAI",
        model_name="dummy-model",
        api_key="dummy-key",
        api_base="https://example.com/v1",
        verify_ssl=False,
    )
    return create_deep_agent(
        model=model,
        card=AgentCard(name="test_skill_agent", description="test skill agent"),
        system_prompt="You are a test assistant.",
        max_iterations=3,
        enable_task_loop=False,
        workspace=workspace,
        sys_operation=sys_operation,
        language=language,
    )


def _sorted_skill_names(skills) -> List[str]:
    return sorted(skill.name for skill in skills)


@pytest.mark.asyncio
async def test_skill_rail_all_mode_loads_skills_on_before_invoke(tmp_path: Path):
    """SkillUseRail should auto-load skills in before_invoke without explicit prepare()."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=True,
    )

    ctx = AgentCallbackContext(
        agent=None,
        inputs=None,
        session=None,
    )

    await skill_rail.before_invoke(ctx)

    assert _sorted_skill_names(skill_rail.skills) == [
        "invoice-parser",
        "xlsx-writer",
    ]
    assert _sorted_skill_names(skill_rail.skills_meta) == [
        "invoice-parser",
        "xlsx-writer",
    ]


@pytest.mark.asyncio
async def test_skill_rail_finds_skills_filed_under_grouping_dirs(tmp_path: Path):
    """A grouping directory is walked through, not reported as a skill.

    Skill libraries are commonly filed by vendor or team
    (``skills/lark/lark-doc/SKILL.md``). A one-level scan saw only ``lark``,
    which holds no SKILL.md, and reported nothing.
    """
    skills_root = tmp_path / "skills"
    _write_skill(skills_root / "lark", "lark-doc", "Edit Feishu docs")
    _write_skill(skills_root / "lark" / "deep", "lark-base", "Query Feishu Base")
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=True,
    )

    await skill_rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))

    assert _sorted_skill_names(skill_rail.skills) == [
        "invoice-parser",
        "lark-base",
        "lark-doc",
    ]


@pytest.mark.asyncio
async def test_skill_rail_does_not_promote_skills_nested_inside_a_skill(tmp_path: Path):
    """A skill's own subdirectories stay private to it.

    Once a directory has a SKILL.md it *is* the skill, and what it keeps
    inside is detail its author discloses through its own content (skill_tool
    lists nested skills when the parent is opened) — not a top-level entry the
    model sees before having read the parent.
    """
    skills_root = tmp_path / "skills"
    parent = _write_skill(skills_root, "writing", "Write documents")
    _write_skill(parent, "designer", "Internal sub-step of writing")

    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=True,
    )

    await skill_rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))

    assert _sorted_skill_names(skill_rail.skills) == ["writing"]


@pytest.mark.asyncio
async def test_skill_rail_scan_survives_a_symlink_cycle(tmp_path: Path):
    """Sharing one library by symlink must not send the walk into a loop."""
    skills_root = tmp_path / "skills"
    group = skills_root / "group"
    _write_skill(group, "shared", "A shared skill")
    try:
        (group / "loop").symlink_to(skills_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=True,
    )

    await skill_rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))

    assert _sorted_skill_names(skill_rail.skills) == ["shared"]


@pytest.mark.asyncio
async def test_skill_rail_all_mode_injects_skill_prompt(tmp_path: Path):
    """All mode should add skills section to builder before model call."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=True,
    )
    skill_rail.set_workspace(Workspace(root_path=str(tmp_path)))
    skill_rail.set_sys_operation(sys_operation)

    # Simulate what _create_react_agent does: provide a builder on the rail.
    builder = SystemPromptBuilder()
    builder.add_section(PromptSection(
        name="identity",
        content={"cn": "Base system prompt.", "en": "Base system prompt."},
    ))
    skill_rail.system_prompt_builder = builder

    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(tools=[]),
        session=None,
    )

    await skill_rail.before_invoke(ctx)
    await skill_rail.before_model_call(ctx)

    # Skills are added to the builder; build() produces the final system prompt.
    content = builder.build()

    assert "Base system prompt." in content
    assert "invoice-parser" in content
    assert "xlsx-writer" in content
    assert "Parse invoice pdf files" in content
    assert "Write xlsx reports" in content
    assert "list_skill" not in content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "description_cn", "description_en", "expected"),
    [
        ("cn", "中文技能描述", "English localized description", "中文技能描述"),
        ("en", "中文技能描述", "English localized description", "English localized description"),
        ("cn", None, None, "Default skill description"),
    ],
)
async def test_skill_rail_uses_localized_description_with_default_fallback(
    tmp_path: Path,
    language: str,
    description_cn: str | None,
    description_en: str | None,
    expected: str,
):
    """Skill descriptions should follow prompt language and fall back to description."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(
        skills_root,
        "localized-skill",
        "Default skill description",
        description_cn=description_cn,
        description_en=description_en,
    )

    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_sys_operation(_make_sys_operation(tmp_path))
    skill_rail.system_prompt_builder = SystemPromptBuilder(language=language)

    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx)
    await skill_rail.before_model_call(ctx)

    content = skill_rail.system_prompt_builder.build()
    assert expected in content


@pytest.mark.asyncio
async def test_skill_rail_before_model_call_refreshes_when_before_invoke_is_skipped(tmp_path: Path):
    """before_model_call should load skills for task-loop paths that skip before_invoke."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = _TrackingSkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_sys_operation(sys_operation)

    builder = SystemPromptBuilder()
    builder.add_section(PromptSection(
        name="identity",
        content={"cn": "Base system prompt.", "en": "Base system prompt."},
    ))
    skill_rail.system_prompt_builder = builder

    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(tools=[]),
        session=None,
    )

    await skill_rail.before_model_call(ctx)

    assert skill_rail.load_calls == ["invoice-parser"]
    assert _sorted_skill_names(skill_rail.skills) == ["invoice-parser"]
    assert "invoice-parser" in builder.build()


@pytest.mark.asyncio
async def test_skill_rail_before_model_call_refreshes_only_when_skill_snapshot_changes(tmp_path: Path):
    """before_model_call should refresh only when directories or SKILL.md mtimes change."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = _TrackingSkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_sys_operation(sys_operation)

    builder = SystemPromptBuilder()
    builder.add_section(PromptSection(
        name="identity",
        content={"cn": "Base system prompt.", "en": "Base system prompt."},
    ))
    skill_rail.system_prompt_builder = builder

    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(tools=[]),
        session=None,
    )

    await skill_rail.before_model_call(ctx)
    assert skill_rail.load_calls == ["invoice-parser"]

    skill_rail.load_calls.clear()
    await skill_rail.before_model_call(ctx)
    assert skill_rail.load_calls == []

    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")
    await skill_rail.before_model_call(ctx)
    assert skill_rail.load_calls == ["xlsx-writer"]

    skill_rail.load_calls.clear()
    time.sleep(1.1)
    skill_md = skills_root / "invoice-parser" / "SKILL.md"
    original = skill_md.read_text(encoding="utf-8")
    skill_md.write_text(original + "\n<!-- updated -->\n", encoding="utf-8")

    await skill_rail.before_model_call(ctx)
    assert skill_rail.load_calls == ["invoice-parser"]


@pytest.mark.asyncio
async def test_skill_rail_filters_enabled_and_disabled_skills(tmp_path: Path):
    """SkillUseRail should respect enabled_skills and disabled_skills."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")
    _write_skill(skills_root, "legacy-skill", "Old skill")

    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        enabled_skills="invoice-parser,xlsx-writer,legacy-skill",
        disabled_skills="legacy-skill",
        include_tools=True,
    )

    ctx = AgentCallbackContext(
        agent=None,
        inputs=None,
        session=None,
    )

    await skill_rail.before_invoke(ctx)

    assert _sorted_skill_names(skill_rail.skills) == [
        "invoice-parser",
        "xlsx-writer",
    ]


@pytest.mark.asyncio
async def test_skill_rail_skill_tool_reads_multimodal_skill_in_hint_mode(tmp_path: Path):
    """Registered skill_tool should read SKILL.md from disk and inject media hints."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(
        skills_root,
        "media-skill",
        "Skill with reference screenshots",
        "See ![landing](images/landing.png) for the layout.\n",
    )

    sys_operation = _make_sys_operation(tmp_path)
    agent = create_deep_agent(
        model=init_model(
            provider="OpenAI",
            model_name="dummy-model",
            api_key="dummy-key",
            api_base="https://example.com/v1",
            verify_ssl=False,
        ),
        card=AgentCard(name="test_skill_agent", description="test skill agent"),
        system_prompt="You are a test assistant.",
        max_iterations=3,
        enable_task_loop=False,
        workspace=skills_root,
        sys_operation=sys_operation,
        enable_read_image_multimodal=True,
    )
    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        multimodal_skill_mode="hint",
        include_tools=False,
    )
    await agent.register_rail(skill_rail)

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    skill_tool_card = next(
        item for item in agent.ability_manager.list()
        if getattr(item, "name", None) == "skill_tool"
    )
    skill_tool = Runner.resource_mgr.get_tool(skill_tool_card.id)

    result = await skill_tool.invoke({"skill_name": "media-skill"})

    assert result.success is True
    assert SKILL_TOOL_MARKDOWN_IMAGES_HINT in result.data["content"]
    assert "images/landing.png" in result.data["content"]


@pytest.mark.asyncio
async def test_skill_rail_register_rail_auto_list_registers_list_skill_tool(tmp_path: Path):
    """auto_list mode should register list_skill tool through agent.register_rail()."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    routing_model = _DummyModel(
        content='{"skills": ["invoice-parser"]}'
    )
    agent = _make_agent(sys_operation, skills_root)

    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="auto_list",
        list_skill_model=routing_model,
        include_tools=True,
    )

    await agent.register_rail(skill_rail)

    ability_names = {
        getattr(item, "name", None)
        for item in agent.ability_manager.list()
        if getattr(item, "name", None)
    }

    assert "read_file" in ability_names
    assert "code" not in ability_names
    assert "bash" in ability_names
    assert "list_skill" in ability_names


@pytest.mark.asyncio
async def test_auto_list_prompt_is_injected_without_preselecting_skills(tmp_path: Path):
    """auto_list mode should add guide prompt to builder without pre-expanding skills."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="auto_list",
        include_tools=True,
    )

    # Simulate what _create_react_agent does: provide a builder on the rail.
    builder = SystemPromptBuilder()
    builder.add_section(PromptSection(
        name="identity",
        content={"cn": "Base system prompt.", "en": "Base system prompt."},
    ))
    skill_rail.system_prompt_builder = builder

    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(tools=[]),
        session=None,
    )

    await skill_rail.before_invoke(ctx)
    await skill_rail.before_model_call(ctx)

    # Skills section is added to the builder; build() produces the final prompt.
    content = builder.build()
    assert "Base system prompt." in content
    assert "list_skill" in content
    assert "invoice-parser" not in content
    assert "read_file" in content
    assert "code" not in content
    assert "bash" in content


@pytest.mark.asyncio
async def test_list_skill_tool_reads_latest_skills_from_skill_rail(tmp_path: Path):
    """ListSkillTool should read latest skills via get_skills instead of fixed snapshot."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    routing_model = _DummyModel(
        content='{"skills": ["xlsx-writer"]}'
    )
    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="auto_list",
        list_skill_model=routing_model,
        include_tools=True,
    )

    tool = ListSkillTool(
        get_skills=lambda: skill_rail.skills,
        list_skill_model=routing_model,
    )

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    result = await tool.invoke({"query": "generate xlsx report"})

    assert result.success is True
    assert result.data["mode"] == "filtered"
    assert result.data["selected_skill_names"] == ["xlsx-writer"]
    assert len(result.data["skills"]) == 1
    assert result.data["skills"][0]["name"] == "xlsx-writer"


@pytest.mark.asyncio
async def test_list_skill_tool_returns_all_skills_when_query_empty(tmp_path: Path):
    """ListSkillTool should return all skills when query is empty."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    skill_rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="auto_list",
        include_tools=True,
    )

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    tool = ListSkillTool(
        get_skills=lambda: skill_rail.skills,
        list_skill_model=None,
    )

    result = await tool.invoke({})

    assert result.success is True
    assert result.data["mode"] == "all"
    assert sorted(item["name"] for item in result.data["skills"]) == [
        "invoice-parser",
        "xlsx-writer",
    ]


@pytest.mark.asyncio
async def test_skill_rail_reuses_cached_skills_across_invokes(tmp_path: Path):
    """SkillUseRail should reuse cached skills across invokes when no skill is changed."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = _TrackingSkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_workspace(Workspace(root_path=str(tmp_path)))
    skill_rail.set_sys_operation(sys_operation)

    ctx1 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx1)
    assert sorted(skill_rail.load_calls) == ["invoice-parser", "xlsx-writer"]

    skill_rail.load_calls.clear()

    ctx2 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx2)
    assert skill_rail.load_calls == []


@pytest.mark.asyncio
async def test_skill_rail_only_loads_new_skill_on_incremental_refresh(tmp_path: Path):
    """SkillUseRail should load only newly added skills on later invokes."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = _TrackingSkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_workspace(Workspace(root_path=str(tmp_path)))
    skill_rail.set_sys_operation(sys_operation)

    ctx1 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx1)
    assert skill_rail.load_calls == ["invoice-parser"]

    skill_rail.load_calls.clear()
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    ctx2 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx2)
    assert skill_rail.load_calls == ["xlsx-writer"]


@pytest.mark.asyncio
async def test_skill_rail_reload_updated_skill_by_update_at(tmp_path: Path):
    """SkillUseRail should reload only updated skills when SKILL.md update_at changes."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = _TrackingSkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_workspace(Workspace(root_path=str(tmp_path)))
    skill_rail.set_sys_operation(sys_operation)
    ctx1 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx1)
    assert sorted(skill_rail.load_calls) == ["invoice-parser", "xlsx-writer"]

    skill_rail.load_calls.clear()

    time.sleep(1.1)
    skill_md = skills_root / "invoice-parser" / "SKILL.md"
    original = skill_md.read_text(encoding="utf-8")
    skill_md.write_text(original + "\n<!-- updated -->\n", encoding="utf-8")

    ctx2 = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[SystemMessage(content="x")], tools=[]),
        session=None,
    )
    await skill_rail.before_invoke(ctx2)
    assert skill_rail.load_calls == ["invoice-parser"]


@pytest.mark.asyncio
async def test_skill_rail_persists_baseline_and_attaches_only_runtime_additions(tmp_path: Path):
    """A session baseline is stable while later filesystem additions are attached dynamically."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    skill_rail.set_sys_operation(sys_operation)
    skill_rail.system_prompt_builder = SystemPromptBuilder()
    skill_rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("session-a")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await skill_rail.before_invoke(ctx)

    baseline = session.get_state("skill_use")
    assert [item["name"] for item in baseline["baseline_skills"]] == ["invoice-parser"]

    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")
    await skill_rail.before_model_call(ctx)

    # The persisted system-prompt baseline does not change.
    assert [item["name"] for item in session.get_state("skill_use")["baseline_skills"]] == [
        "invoice-parser"
    ]
    assert "invoice-parser" in skill_rail.system_prompt_builder.build()
    assert "xlsx-writer" not in skill_rail.system_prompt_builder.build()

    attachments = await skill_rail.attachment_manager.collect_for_session("session-a")
    assert len(attachments) == 1
    assert attachments[0].section == "skills.runtime_changes"
    assert "xlsx-writer" in (attachments[0].content or "")
    assert "Skill 环境状态更新。请根据当前任务需要，按需调用相关 Skill" in (
        attachments[0].content or ""
    )
    assert "使用规则" not in (attachments[0].content or "")
    assert "Activation rule" not in (attachments[0].content or "")


@pytest.mark.asyncio
async def test_skill_rail_puts_baseline_evolution_experience_in_attachment(tmp_path: Path):
    """Baseline evolution experience is attached without changing the system prompt."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
        evolution_store=_DummyEvolutionStore(
            {"invoice-parser": "- Prefer extracting tables before summarizing the invoice."}
        ),
    )
    rail.set_sys_operation(sys_operation)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("evolution-baseline")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    system_prompt = rail.system_prompt_builder.build()
    assert "Parse invoice pdf files" in system_prompt
    assert "Prefer extracting tables" not in system_prompt

    attachments = await rail.attachment_manager.collect_for_session("evolution-baseline")
    assert len(attachments) == 1
    content = attachments[0].content or ""
    assert "Skill 演进经验参考：" in content
    assert "[Skill: invoice-parser]" in content
    assert "Prefer extracting tables before summarizing the invoice." in content


@pytest.mark.asyncio
async def test_skill_rail_combines_additions_removals_and_evolution_in_one_attachment(tmp_path: Path):
    """Skill changes and evolution experience share one Skill attachment."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    removed_dir = _write_skill(skills_root, "legacy-parser", "Parse legacy files")

    sys_operation = _make_sys_operation(tmp_path)
    rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
        evolution_store=_DummyEvolutionStore(
            {
                "invoice-parser": "- Prefer extracting tables first.",
                "legacy-parser": "- Preserve legacy field names.",
                "new-parser": "- Validate the output schema before returning.",
            }
        ),
    )
    rail.set_sys_operation(sys_operation)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("combined-changes")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await rail.before_invoke(ctx)

    removed_dir.joinpath("SKILL.md").unlink()
    _write_skill(skills_root, "new-parser", "Parse new files")
    await rail.before_model_call(ctx)

    attachments = await rail.attachment_manager.collect_for_session("combined-changes")
    assert len(attachments) == 1
    content = attachments[0].content or ""
    assert "新增可用 Skill：" in content
    assert "0. `new-parser`：Parse new files" in content
    assert "已移除、当前不可用的 Skill：" in content
    assert "legacy-parser" in content
    assert "[Skill: invoice-parser]" in content
    assert "[Skill: legacy-parser]" in content
    assert "[Skill: new-parser]" in content


@pytest.mark.asyncio
async def test_skill_rail_renders_evolution_attachment_in_english(tmp_path: Path):
    """English prompt builders use the English Skill attachment headings."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
        evolution_store=_DummyEvolutionStore({"invoice-parser": "- Extract tables first."}),
    )
    rail.set_sys_operation(sys_operation)
    rail.system_prompt_builder = SystemPromptBuilder(language="en")
    rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("evolution-english")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    attachments = await rail.attachment_manager.collect_for_session("evolution-english")
    content = attachments[0].content or ""
    assert "Skill environment status update." in content
    assert "Skill evolution experience reference:" in content
    assert "[Skill: invoice-parser]" in content
    assert "Extract tables first." in content


@pytest.mark.asyncio
async def test_skill_rail_refreshes_evolution_when_skill_files_are_unchanged(tmp_path: Path):
    """Evolution changes are refreshed independently from the Skill.md snapshot."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    evolution_store = _DummyEvolutionStore({"invoice-parser": "- Use the first experience."})

    sys_operation = _make_sys_operation(tmp_path)
    rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
        evolution_store=evolution_store,
    )
    rail.set_sys_operation(sys_operation)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("evolution-refresh")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)

    evolution_store.texts["invoice-parser"] = "- Use the updated experience."
    await rail.before_model_call(ctx)

    attachments = await rail.attachment_manager.collect_for_session("evolution-refresh")
    content = attachments[0].content or ""
    assert "Use the updated experience." in content
    assert "Use the first experience." not in content


@pytest.mark.asyncio
async def test_skill_rail_attaches_deleted_skill_change(tmp_path: Path):
    """Deleting a baseline Skill is reported through the session attachment."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    deleted_skill_dir = _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    skill_rail.set_sys_operation(sys_operation)
    skill_rail.system_prompt_builder = SystemPromptBuilder()
    skill_rail.attachment_manager = PromptAttachmentManager()

    session = _SessionState("session-delete")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await skill_rail.before_invoke(ctx)

    deleted_skill_dir.joinpath("SKILL.md").unlink()
    await skill_rail.before_model_call(ctx)

    attachments = await skill_rail.attachment_manager.collect_for_session("session-delete")
    assert len(attachments) == 1
    assert "已移除、当前不可用的 Skill" in (attachments[0].content or "")
    assert "invoice-parser" in (attachments[0].content or "")


@pytest.mark.asyncio
async def test_skill_rail_new_session_promotes_installed_skill_to_baseline(tmp_path: Path):
    """A newly installed skill becomes baseline only when a new session starts."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    session = _SessionState("session-b")
    await rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=session))

    assert [item["name"] for item in session.get_state("skill_use")["baseline_skills"]] == [
        "invoice-parser",
        "xlsx-writer",
    ]


@pytest.mark.asyncio
async def test_skill_rail_rehydrates_baseline_after_rail_restart(tmp_path: Path):
    """Restoring a Session does not require the Rail to retain the baseline in memory."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")

    restored_state = {
        "skill_use": {
            "schema_version": 1,
            "baseline_skills": [
                {
                    "name": "invoice-parser",
                    "description": "Persisted description",
                    "directory": str(skills_root / "invoice-parser"),
                }
            ],
        }
    }
    session = _SessionState("restored", restored_state)
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    await rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=session))

    baseline = rail._get_session_baseline(AgentCallbackContext(agent=None, inputs=None, session=session))
    assert [skill.name for skill in baseline] == ["invoice-parser"]
    assert baseline[0].description == "Persisted description"
    assert [skill.name for skill in rail.get_skills_for_session(session)] == [
        "invoice-parser",
        "xlsx-writer",
    ]
    # A legacy state carries neither "provisional" nor "language": it is left
    # exactly as it was persisted, with no new keys written back.
    assert session.get_state("skill_use") == restored_state["skill_use"]


@pytest.mark.asyncio
async def test_skill_rail_keeps_empty_baseline_distinct_from_missing_baseline(tmp_path: Path):
    """An empty first-session baseline still makes later installations dynamic."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    session = _SessionState("empty-first")
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)

    await rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=session))
    assert session.get_state("skill_use")["baseline_skills"] == []

    _write_skill(skills_root, "new-skill", "Installed after the session started")
    await rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=session))
    assert [skill.name for skill in rail.get_skills_for_session(session)] == ["new-skill"]
    assert session.get_state("skill_use")["baseline_skills"] == []


@pytest.mark.asyncio
async def test_skill_rail_skips_nonexistent_directories(tmp_path: Path):
    """SkillUseRail should silently skip directories that do not exist."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root, "my-skill", "Real skill")

    nonexistent = tmp_path / "does_not_exist"
    another_nonexistent = tmp_path / "also_missing"

    skill_rail = SkillUseRail(
        skills_dir=[str(nonexistent), str(skills_root), str(another_nonexistent)],
        skill_mode="all",
        include_tools=False,
    )

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    assert _sorted_skill_names(skill_rail.skills) == ["my-skill"]


@pytest.mark.asyncio
async def test_skill_rail_all_dirs_nonexistent_produces_empty_skills(tmp_path: Path):
    """SkillUseRail should produce empty skills when all directories are missing."""
    nonexistent_a = tmp_path / "missing_a"
    nonexistent_b = tmp_path / "missing_b"

    skill_rail = SkillUseRail(
        skills_dir=[str(nonexistent_a), str(nonexistent_b)],
        skill_mode="all",
        include_tools=False,
    )

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    assert skill_rail.skills == []


@pytest.mark.asyncio
async def test_skill_rail_priority_dedup_first_dir_wins(tmp_path: Path):
    """When multiple dirs contain a skill with the same name, the first dir wins."""
    high_prio = tmp_path / "high"
    low_prio = tmp_path / "low"

    _write_skill(high_prio, "shared-skill", "High priority version")
    _write_skill(low_prio, "shared-skill", "Low priority version")
    _write_skill(low_prio, "unique-skill", "Only in low")

    sys_operation = _make_sys_operation(tmp_path)
    skill_rail = SkillUseRail(
        skills_dir=[str(high_prio), str(low_prio)],
        skill_mode="all",
        include_tools=False,
    )
    skill_rail.set_sys_operation(sys_operation)

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    names = _sorted_skill_names(skill_rail.skills)
    assert names == ["shared-skill", "unique-skill"]

    # Verify the "shared-skill" comes from high_prio directory
    shared = [s for s in skill_rail.skills if s.name == "shared-skill"][0]
    assert str(high_prio.resolve()) in str(shared.directory.resolve())
    assert shared.description == "High priority version"


@pytest.mark.asyncio
async def test_skill_rail_multi_dir_with_missing_dirs(tmp_path: Path):
    """SkillUseRail loads skills from existing dirs and skips missing ones."""
    existing_a = tmp_path / "dir_a"
    missing_b = tmp_path / "dir_b"
    existing_c = tmp_path / "dir_c"

    _write_skill(existing_a, "skill-a", "From dir A")
    # dir_b does not exist
    _write_skill(existing_c, "skill-c", "From dir C")

    skill_rail = SkillUseRail(
        skills_dir=[str(existing_a), str(missing_b), str(existing_c)],
        skill_mode="all",
        include_tools=False,
    )

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await skill_rail.before_invoke(ctx)

    assert _sorted_skill_names(skill_rail.skills) == ["skill-a", "skill-c"]


@pytest.mark.asyncio
async def test_skill_rail_refresh_ttl_failure_does_not_advance_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A failing refresh must not stamp the TTL, or the whole window skips retry.

    Regression: the timestamp used to be written in ``finally``, so one
    transient ``refresh_skill_prompt`` error made subsequent ``before_invoke``
    calls hit the cache path with stale skills until the TTL elapsed.
    """
    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "30")
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    session = _SessionState("session-ttl-fail")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=session)

    calls = {"n": 0}

    async def _flaky_refresh(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient scan failure")

    rail.refresh_skill_prompt = _flaky_refresh

    with pytest.raises(RuntimeError):
        await rail.before_invoke(ctx)

    assert calls["n"] == 1
    assert rail._last_full_refresh_ts is None

    # Within the 30s window the retry must still run a real refresh instead of
    # taking the cache path with whatever stale state the failure left behind.
    del rail.refresh_skill_prompt
    await rail.before_invoke(ctx)
    assert [skill.name for skill in rail.skills] == ["invoice-parser"]


@pytest.mark.asyncio
async def test_skill_rail_refresh_ttl_window_skips_scan_until_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Within the TTL window before_invoke reuses the last refresh; expiry rescans."""
    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "30")
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    session = _SessionState("session-ttl-window")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=session)

    calls = {"n": 0}
    original_refresh = rail.refresh_skill_prompt

    async def _counting_refresh(ctx):
        calls["n"] += 1
        return await original_refresh(ctx)

    rail.refresh_skill_prompt = _counting_refresh

    await rail.before_invoke(ctx)
    await rail.before_invoke(ctx)
    assert calls["n"] == 1
    assert [skill.name for skill in rail.skills] == ["invoice-parser"]

    # Force the window open without sleeping: a stamp older than the TTL
    # rescans. Use a relative offset so runners with short uptime still pass.
    rail._last_full_refresh_ts = time.monotonic() - rail._skill_refresh_ttl_s - 1.0
    await rail.before_invoke(ctx)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_skill_rail_refresh_ttl_disabled_when_enable_cache_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """enable_cache=False must force a full refresh even inside the TTL window."""
    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "30")
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    rail = SkillUseRail(
        skills_dir=str(skills_root),
        skill_mode="all",
        include_tools=False,
        enable_cache=False,
    )
    ctx = AgentCallbackContext(agent=None, inputs=None, session=_SessionState("session-ttl-no-cache"))

    calls = {"n": 0}
    original_refresh = rail.refresh_skill_prompt

    async def _counting_refresh(ctx):
        calls["n"] += 1
        return await original_refresh(ctx)

    rail.refresh_skill_prompt = _counting_refresh

    await rail.before_invoke(ctx)
    await rail.before_invoke(ctx)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_load_skills_from_dir_reads_yaml_description_without_sys_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Standalone load_skills_from_dir reads the real YAML descriptions.

    Regression: the helper built a rail with ``sys_operation=None`` and every
    read crashed with ``'NoneType' object has no attribute 'fs'``, which was
    swallowed into a warning and replaced by ``"Skill located in <dir>"``.
    """
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    _write_long_front_matter_skill(skills_root, "long-meta", "Front matter longer than the 64-line probe")

    recording = _RecordingLogger()
    monkeypatch.setattr(skill_use_rail_module, "logger", recording)

    skills = await SkillUseRail.load_skills_from_dir(str(skills_root))

    assert {skill.name: skill.description for skill in skills} == {
        "invoice-parser": "Parse invoice pdf files",
        "long-meta": "Front matter longer than the 64-line probe",
    }
    assert [message for level, message in recording.messages if level == "warning"] == []
    assert not any("'NoneType' object has no attribute 'fs'" in message for _, message in recording.messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("mounted", [False, True])
async def test_skill_rail_reads_long_front_matter_with_and_without_sys_operation(
    tmp_path: Path, mounted: bool
):
    """A front matter longer than the probe window falls back to a full read in both modes.

    Mounted, both reads go through the backend: it serves its own text (so a
    local-file fallback would be caught) and the recorded heads are ``[64,
    None]`` — the bounded probe, then the full read.
    """
    skills_root = tmp_path / "skills"
    _write_long_front_matter_skill(skills_root, "long-meta", "Front matter longer than the 64-line probe")
    skill_md = skills_root / "long-meta" / "SKILL.md"
    assert len(skill_md.read_text(encoding="utf-8").split("---", 2)[1].splitlines()) > 64

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    backend = None
    expected = "Front matter longer than the 64-line probe"
    if mounted:
        # Distinct text proves the mounted rail never fell back to the local file.
        expected = "Served by the bound backend"
        backend = _CannedBackend(_long_front_matter_text("long-meta", expected))
        rail.set_sys_operation(backend)

    await rail.reload_skills()

    assert [skill.description for skill in rail.skills] == [expected]
    if backend is not None:
        assert _resolved(path for path, _ in backend.reads) == _resolved([skill_md, skill_md])
        assert [head for _, head in backend.reads] == [64, None]


@pytest.mark.asyncio
async def test_unmounted_reload_skills_reads_description_and_set_sys_operation_invalidates_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A pre-mount reload must not poison the cache once a sys_operation is bound."""
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    skill_md = skills_root / "invoice-parser" / "SKILL.md"
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    assert rail.sys_operation is None

    # Prewarm before mounting: the real description is read straight from disk.
    await rail.reload_skills()
    assert [skill.description for skill in rail.skills] == ["Parse invoice pdf files"]
    assert len(rail._skill_cache) == 1

    # Bind a sandbox that does not contain skills_root: the cache is dropped and
    # the re-read goes through sys_operation, which rejects the path instead of
    # serving the stale pre-mount result.
    rejecting, rejecting_spy = _make_sandboxed_sys_operation(
        tmp_path, "reject", tmp_path / "sandbox", monkeypatch
    )
    rail.set_sys_operation(rejecting)
    assert rail._skill_cache == {}
    assert rail.skills == []

    await rail.before_invoke(ctx)
    assert _resolved(path for path, _ in rejecting_spy.reads) == _resolved([skill_md])
    assert [skill.description for skill in rail.skills] == [f"Skill located in {skill_md.parent}"]
    rejected = await rejecting.fs().read_file(str(skill_md), mode="text", encoding="utf-8", only_read=True)
    assert rejected.code != 0
    assert "outside sandbox" in rejected.message

    # A backend that may read skills_root is consulted as well; the description
    # now comes from sys_operation.fs().read_file, not from the local cache.
    allowing, allowing_spy = _make_sandboxed_sys_operation(tmp_path, "allow", skills_root, monkeypatch)
    rail.set_sys_operation(allowing)
    assert rail._skill_cache == {}

    await rail.before_invoke(ctx)
    assert [skill.description for skill in rail.skills] == ["Parse invoice pdf files"]
    assert _resolved(path for path, _ in allowing_spy.reads) == _resolved([skill_md])


@pytest.mark.asyncio
async def test_prewarm_before_init_refreshes_localized_description_when_language_changes(tmp_path: Path):
    """A prewarm in one prompt language must not leak into a mount in another."""
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "localized-skill",
        "Default skill description",
        description_cn="中文技能描述",
        description_en="English localized description",
    )
    sys_operation = _make_sys_operation(tmp_path)

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    # Unmounted prewarm: no prompt builder yet, so the default ("cn") text is used.
    await rail.reload_skills()
    assert [skill.description for skill in rail.skills] == ["中文技能描述"]

    agent = _make_agent(sys_operation, skills_root, language="en")
    await agent.register_rail(rail)
    assert rail.system_prompt_builder.language == "en"
    assert rail._skill_cache == {}

    ctx = AgentCallbackContext(agent=agent, inputs=ModelCallInputs(tools=[]), session=None)
    await rail.before_invoke(ctx)
    assert [skill.description for skill in rail.skills] == ["English localized description"]

    # Same backend, different language: set_sys_operation() keeps the cache
    # because the instance did not change, and register_rail() assigns the
    # English builder *before* init() runs. The switch is therefore caught at
    # the next refresh, by comparing against the language the cache was
    # built for.
    same_backend_rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    same_backend_rail.set_sys_operation(sys_operation)
    await same_backend_rail.reload_skills()
    assert [skill.description for skill in same_backend_rail.skills] == ["中文技能描述"]

    await agent.register_rail(same_backend_rail)
    assert same_backend_rail.system_prompt_builder.language == "en"
    assert same_backend_rail._cached_language == "cn"
    assert same_backend_rail._skill_refresh_within_ttl() is False
    await same_backend_rail.before_invoke(ctx)
    assert [skill.description for skill in same_backend_rail.skills] == ["English localized description"]
    assert same_backend_rail._cached_language == "en"


@pytest.mark.asyncio
async def test_set_sys_operation_same_instance_preserves_cache(tmp_path: Path):
    """Re-binding the same sys_operation must not wipe the skill cache."""
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    sys_operation = _make_sys_operation(tmp_path)
    rail = _TrackingSkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.set_sys_operation(sys_operation)
    await rail.reload_skills()
    assert rail.load_calls == ["invoice-parser"]
    cached = dict(rail._skill_cache)
    assert len(cached) == 1

    rail.set_sys_operation(sys_operation)
    assert rail._skill_cache == cached
    assert [skill.description for skill in rail.skills_meta] == ["Parse invoice pdf files"]

    await rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))
    assert rail.load_calls == ["invoice-parser"]


@pytest.mark.asyncio
async def test_read_local_skill_text_streams_head_and_matches_local_fs_for_negative_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A positive head reads only the leading lines; a negative head mirrors LocalFS.read_file.

    Regression: the 64-line probe read and split the whole file (8 MB to return
    128 chars), and a negative head decoded the file before returning "" — so
    invalid UTF-8 raised where LocalFS returns empty text.
    """
    skill_dir = tmp_path / "big-skill"
    skill_dir.mkdir()
    lines = ["---", "description: Only the leading lines are probed", "---"]
    lines.extend(f"padding-{index:06d} " + "x" * 87 for index in range(80_000))
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    full_text = skill_md.read_text(encoding="utf-8")
    assert len(full_text) > 8_000_000

    counting = _CountingOpen()
    monkeypatch.setattr(skill_use_rail_module, "open", counting, raising=False)

    probed = await SkillUseRail._read_local_skill_text(skill_md, head=64)
    assert probed == "".join(f"{line}\n" for line in lines[:64])
    # Bounded: only the probed lines were pulled from the handle, not the body.
    assert counting.consumed_chars == len(probed) < len(full_text)

    # Zero and None keep their "no limit" meaning.
    for head in (0, None):
        counting.handles.clear()
        assert await SkillUseRail._read_local_skill_text(skill_md, head=head) == full_text
        assert counting.consumed_chars == len(full_text)

    # Negative head: the path is validated, then "" is returned before any
    # byte is decoded. Missing files and directories still fail.
    binary_md = tmp_path / "binary-skill" / "SKILL.md"
    binary_md.parent.mkdir()
    binary_md.write_bytes(b"---\ndescription: not valid utf-8\n---\n\xff\xfe\xfa\n")
    missing_md = tmp_path / "missing-skill" / "SKILL.md"

    counting.handles.clear()
    assert await SkillUseRail._read_local_skill_text(binary_md, head=-1) == ""
    assert counting.handles == []

    with pytest.raises(FileNotFoundError):
        await SkillUseRail._read_local_skill_text(missing_md, head=-1)
    with pytest.raises(FileNotFoundError):
        await SkillUseRail._read_local_skill_text(tmp_path, head=-1)
    with pytest.raises(FileNotFoundError):
        await SkillUseRail._read_local_skill_text(binary_md, head=1)

    # The same calls against the real LocalFS.read_file.
    local_fs = _make_sys_operation(tmp_path).fs()

    async def local_fs_outcome(path: Path, head: Optional[int]) -> Tuple[int, str]:
        result = await local_fs.read_file(str(path), mode="text", encoding="utf-8", head=head, only_read=True)
        return result.code, getattr(result.data, "content", "")

    assert await local_fs_outcome(binary_md, -1) == (0, "")
    assert (await local_fs_outcome(missing_md, -1))[0] != 0
    assert (await local_fs_outcome(tmp_path, -1))[0] != 0
    assert (await local_fs_outcome(binary_md, 1))[0] != 0


@pytest.mark.asyncio
async def test_queued_rail_prewarmed_in_cn_refreshes_to_english_inside_ttl_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A rail queued with add_rail must not keep its Chinese prewarm on an English agent.

    Regression: lazy initialization pushes system_prompt_builder onto pending
    rails *before* calling init(), so init() compared English with English and
    kept the cache; inside a live TTL window the Chinese description was then
    served. The language the cache was built for is tracked instead.
    """
    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "30")
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "localized-skill",
        "Default skill description",
        description_cn="中文技能描述",
        description_en="English localized description",
    )
    sys_operation = _make_sys_operation(tmp_path)
    agent = _make_agent(sys_operation, tmp_path, language="en")

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    # Prewarm on the very backend the agent will bind (so the bind itself is
    # not a change), in Chinese, with the TTL window open.
    rail.set_sys_operation(sys_operation)
    await rail.before_invoke(AgentCallbackContext(agent=None, inputs=None, session=None))
    assert [skill.description for skill in rail.skills] == ["中文技能描述"]
    assert rail._skill_refresh_within_ttl() is True

    # Queued registration: the builder reaches the rail before init() runs.
    agent.add_rail(rail)
    await agent.ensure_initialized()
    assert rail.system_prompt_builder is agent.system_prompt_builder
    assert rail.system_prompt_builder.language == "en"

    # The cached language no longer matches, so the window is not reused.
    assert rail._skill_refresh_within_ttl() is False
    ctx = AgentCallbackContext(agent=agent, inputs=ModelCallInputs(tools=[]), session=None)
    await rail.before_invoke(ctx)
    assert [skill.description for skill in rail.skills] == ["English localized description"]
    prompt = agent.system_prompt_builder.build()
    assert "English localized description" in prompt
    assert "中文技能描述" not in prompt

    # Hot reconfiguration swaps the builder without calling init() again.
    rail.system_prompt_builder = SystemPromptBuilder(language="cn")
    assert rail._skill_refresh_within_ttl() is False
    await rail.before_invoke(ctx)
    assert [skill.description for skill in rail.skills] == ["中文技能描述"]
    assert "中文技能描述" in rail.system_prompt_builder.build()


@pytest.mark.asyncio
async def test_inflight_refresh_from_previous_backend_is_discarded_after_rebind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A read that finishes after the backend was rebound must not repopulate the cache.

    Regression: backend A's read was parked inside read_file while the rail
    was rebound to backend B (which clears the cache). A's result then arrived
    and was written back under its unchanged mtime, so even the next reload
    kept serving A's description.
    """
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")
    skill_md = skills_root / "invoice-parser" / "SKILL.md"
    started = asyncio.Event()
    gate = asyncio.Event()
    parked_backend = _CannedBackend(
        "---\ndescription: Served by the previous backend\n---\n\n# invoice-parser\n",
        started=started,
        gate=gate,
    )

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.set_sys_operation(parked_backend)

    inflight = asyncio.create_task(rail.reload_skills())
    await asyncio.wait_for(started.wait(), timeout=5.0)
    assert len(parked_backend.reads) == 1

    # Rebind while the read is parked: the cache is cleared and the generation
    # bumps; A's result then arrives and has to be dropped.
    rebound, rebound_spy = _make_sandboxed_sys_operation(tmp_path, "rebound", skills_root, monkeypatch)
    rail.set_sys_operation(rebound)
    gate.set()
    await asyncio.wait_for(inflight, timeout=5.0)

    # Nothing A produced reached the cache, the public list, the signature or
    # the TTL stamp.
    assert rail._skill_cache == {}
    assert rail.skills == []
    assert rail._skills_snapshot_signature is None
    assert rail._last_full_refresh_ts is None

    # The next reload reads B, not A.
    await rail.reload_skills()
    assert [skill.description for skill in rail.skills] == ["Parse invoice pdf files"]
    assert _resolved(path for path, _ in rebound_spy.reads) == _resolved([skill_md])
    assert len(parked_backend.reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bound_backend", ["english", "sandbox"])
async def test_provisional_session_baseline_is_replaced_once_a_backend_is_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound_backend: str
):
    """A baseline saved while unbound must not survive the first bound invoke.

    Regression: an unbound before_invoke persisted the local (cn) description
    in the session. Binding a backend cleared the rail cache, but the session
    kept serving its first description to the tools and the rendered prompt —
    including a pre-mount read that the newly bound sandbox now rejects.
    """
    skills_root = tmp_path / "skills"
    skill_dir = _write_skill(
        skills_root,
        "localized-skill",
        "Default skill description",
        description_cn="中文技能描述",
        description_en="English localized description",
    )

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    session = _SessionState("provisional-baseline")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    # Unbound: the local, default-language view is persisted as provisional.
    await rail.before_invoke(ctx)
    state = session.get_state("skill_use")
    assert state["provisional"] is True
    assert state["language"] == "cn"
    assert [item["description"] for item in state["baseline_skills"]] == ["中文技能描述"]

    rejecting_spy = None
    if bound_backend == "english":
        rail.set_sys_operation(_make_sys_operation(tmp_path))
        rail.system_prompt_builder = SystemPromptBuilder(language="en")
        expected = "English localized description"
    else:
        rejecting, rejecting_spy = _make_sandboxed_sys_operation(
            tmp_path, "reject", tmp_path / "sandbox", monkeypatch
        )
        rail.set_sys_operation(rejecting)
        rail.system_prompt_builder = SystemPromptBuilder()
        expected = f"Skill located in {skill_dir}"

    # Same session, now bound: the provisional baseline is replaced.
    await rail.before_invoke(ctx)

    state = session.get_state("skill_use")
    assert state["provisional"] is False
    assert state["language"] == rail.system_prompt_builder.language
    assert [item["description"] for item in state["baseline_skills"]] == [expected]
    assert [skill.description for skill in rail.get_skills_for_session(session)] == [expected]
    prompt = rail.system_prompt_builder.build()
    assert expected in prompt
    assert "中文技能描述" not in prompt
    if rejecting_spy is not None:
        assert _resolved(path for path, _ in rejecting_spy.reads) == _resolved([skill_dir / "SKILL.md"])


@pytest.mark.asyncio
async def test_session_baseline_descriptions_follow_a_language_switch(tmp_path: Path):
    """A bound baseline keeps its membership and order; only descriptions re-localize."""
    skills_root = tmp_path / "skills"
    for name in ("alpha-skill", "beta-skill"):
        _write_skill(
            skills_root,
            name,
            f"Default {name}",
            description_cn=f"中文 {name}",
            description_en=f"English {name}",
        )

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.set_sys_operation(_make_sys_operation(tmp_path))
    rail.system_prompt_builder = SystemPromptBuilder(language="cn")
    session = _SessionState("baseline-language")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    await rail.before_invoke(ctx)
    assert [item["description"] for item in session.get_state("skill_use")["baseline_skills"]] == [
        "中文 alpha-skill",
        "中文 beta-skill",
    ]

    # Installed after the session started: a runtime addition, never baseline.
    _write_skill(skills_root, "gamma-skill", "Default gamma-skill", description_en="English gamma-skill")

    rail.system_prompt_builder = SystemPromptBuilder(language="en")
    await rail.before_invoke(ctx)

    state = session.get_state("skill_use")
    assert state["provisional"] is False
    assert state["language"] == "en"
    assert [item["name"] for item in state["baseline_skills"]] == ["alpha-skill", "beta-skill"]
    assert [item["description"] for item in state["baseline_skills"]] == [
        "English alpha-skill",
        "English beta-skill",
    ]
    assert [(skill.name, skill.description) for skill in rail.get_skills_for_session(session)] == [
        ("alpha-skill", "English alpha-skill"),
        ("beta-skill", "English beta-skill"),
        ("gamma-skill", "English gamma-skill"),
    ]


@pytest.mark.asyncio
async def test_session_getter_reconciles_after_validate_and_backend_switch(tmp_path: Path):
    """Every session read reconciles the baseline with the source read from now.

    Regression: the baseline was only reconciled inside the callbacks, so a
    backend switch that happened without one in between kept answering
    through the previous source. After an unbound invoke, binding backend A
    and refreshing through ``validate_skill_source`` left the tool-facing
    getter serving the local pre-mount description while the rail held A;
    and after an invoke promoted A's baseline, switching to B and invoking
    again still served A. Ordinary restart rehydration must keep the
    persisted baseline as-is.
    """
    skills_root = tmp_path / "skills"
    skill_dir = _write_skill(skills_root, "alpha", "Local")
    backend_a = _CannedBackend("---\ndescription: Backend A\n---\n\n# alpha\n")
    backend_b = _CannedBackend("---\ndescription: Backend B\n---\n\n# alpha\n")
    session = _SessionState("source-switch")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)

    # (a) Unbound invoke, then backend A is bound and refreshed by
    # validate_skill_source itself: no callback runs in between.
    await rail.before_invoke(ctx)
    assert [item["description"] for item in session.get_state("skill_use")["baseline_skills"]] == ["Local"]

    rail.set_sys_operation(backend_a)
    await rail.validate_skill_source("alpha", skill_dir, session, refresh=True)
    # The rail refreshed from A through validate_skill_source alone, so the
    # tool-facing getter must answer with A as well, not the pre-mount text.
    assert [skill.description for skill in rail.skills] == ["Backend A"]
    assert [skill.description for skill in rail.get_skills_for_session(session)] == ["Backend A"]
    state = session.get_state("skill_use")
    assert state["provisional"] is False
    assert [item["description"] for item in state["baseline_skills"]] == ["Backend A"]

    # (b) A's baseline is promoted by a bound invoke. Switching to B: before
    # the next refresh neither A's baseline nor the cleared list is served
    # or persisted; after it, the getter answers with B.
    await rail.before_invoke(ctx)
    assert [skill.description for skill in rail.get_skills_for_session(session)] == ["Backend A"]
    promoted = session.get_state("skill_use")

    rail.set_sys_operation(backend_b)
    assert rail.get_skills_for_session(session) == []
    assert session.get_state("skill_use") == promoted

    await rail.before_invoke(ctx)
    assert [skill.description for skill in rail.skills] == ["Backend B"]
    assert [skill.description for skill in rail.get_skills_for_session(session)] == ["Backend B"]
    state = session.get_state("skill_use")
    assert state["provisional"] is False
    assert [item["description"] for item in state["baseline_skills"]] == ["Backend B"]

    # (c) Ordinary restart rehydration: a new rail bound to a fresh SysOperation
    # serves the persisted bound baseline untouched even after the library changes.
    _write_skill(skills_root, "alpha", "Parse invoice pdf files")
    card = SysOperationCard(
        id=f"test_skill_rail_sysop_{tmp_path.name}_restart",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(card)
    first = Runner.resource_mgr.get_sys_operation(card.id)
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.set_sys_operation(first)
    await rail.before_invoke(ctx)
    persisted = session.get_state("skill_use")
    assert persisted["provisional"] is False
    assert [item["name"] for item in persisted["baseline_skills"]] == ["alpha"]

    _write_skill(skills_root, "beta", "Installed after the baseline")
    restored_session = _SessionState("restored", {"skill_use": persisted})
    restored_ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=restored_session)
    restarted = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    restarted.set_sys_operation(SysOperation(card))
    assert restarted.sys_operation is not first

    await restarted.before_invoke(restored_ctx)
    assert restored_session.get_state("skill_use") == persisted
    assert [skill.name for skill in restarted._get_session_baseline(restored_ctx)] == ["alpha"]
    assert [skill.name for skill in restarted.get_skills_for_session(restored_session)] == ["alpha", "beta"]

    # Explicit live rebind from one bound SysOperation to another reconciles the baseline.
    other_card = SysOperationCard(
        id=f"test_skill_rail_sysop_{tmp_path.name}_other",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(other_card)
    restarted.set_sys_operation(Runner.resource_mgr.get_sys_operation(other_card.id))
    await restarted.before_invoke(restored_ctx)
    assert [item["name"] for item in restored_session.get_state("skill_use")["baseline_skills"]] == [
        "alpha",
        "beta",
    ]


@pytest.mark.asyncio
async def test_rebind_during_evolution_fetch_does_not_stamp_stale_signature(tmp_path: Path):
    """A refresh must still be current after its evolution await to stamp the signature.

    Regression: ``reload_skills`` checked the generation only before awaiting
    the evolution store. Pausing that await and rebinding let the obsolete
    refresh resume and stamp the unchanged filesystem signature, so the next
    model call compared equal, skipped discovery and showed zero skills.
    """
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "alpha", "Local")
    started = asyncio.Event()
    gate = asyncio.Event()

    class _PausingStore:
        async def format_desc_experience_text(self, skill_name: str) -> str:
            started.set()
            await gate.wait()
            return ""

    backend_a = _CannedBackend("---\ndescription: Backend A\n---\n\n# alpha\n")
    backend_b = _CannedBackend("---\ndescription: Backend B\n---\n\n# alpha\n")
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.set_sys_operation(backend_a)
    rail.evolution_store = _PausingStore()
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=None)

    inflight = asyncio.create_task(rail.reload_skills())
    await asyncio.wait_for(started.wait(), timeout=5.0)
    # The skills were read and published; the refresh is parked on its last await.
    assert [skill.description for skill in rail.skills] == ["Backend A"]

    rail.set_sys_operation(backend_b)
    gate.set()
    await asyncio.wait_for(inflight, timeout=5.0)

    # The obsolete refresh did not stamp the cleared state.
    assert rail._skills_snapshot_signature is None
    assert rail.skills == []
    assert rail._skill_cache == {}

    # The next model call discovers backend B instead of skipping on a stale
    # signature and showing nothing.
    await rail.before_model_call(ctx)
    assert [skill.description for skill in rail.skills] == ["Backend B"]
    assert "Backend B" in rail.system_prompt_builder.build()
    assert rail._skills_snapshot_signature is not None


@pytest.mark.asyncio
async def test_aborted_before_invoke_does_not_freeze_empty_session_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An invoke whose refresh a rebind aborted must not publish its cleared view.

    Regression: the aborted refresh suppressed only the TTL stamp. The
    session baseline still ran and recorded the cleared, empty list as a
    non-provisional baseline for a brand-new session; the next successful
    scan restored the rail's skills but never the session's baseline.
    """
    skills_root = tmp_path / "skills"
    skill_md = _write_skill(skills_root, "alpha", "Local") / "SKILL.md"
    started = asyncio.Event()
    gate = asyncio.Event()
    parked_backend = _CannedBackend(
        "---\ndescription: Backend A text (stale)\n---\n\n# alpha\n",
        started=started,
        gate=gate,
    )
    backend_b, backend_b_spy = _make_sandboxed_sys_operation(
        tmp_path, "aborted", skills_root, monkeypatch
    )
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.set_sys_operation(parked_backend)
    session = _SessionState("aborted-invoke")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    inflight = asyncio.create_task(rail.before_invoke(ctx))
    await asyncio.wait_for(started.wait(), timeout=5.0)
    rail.set_sys_operation(backend_b)
    gate.set()
    await asyncio.wait_for(inflight, timeout=5.0)

    # The new session has no baseline at all: not the cleared list frozen as
    # its baseline, and nothing stamped from the aborted generation.
    assert session.get_state("skill_use") is None
    assert rail.skills == []
    assert rail._skill_cache == {}
    assert rail._last_full_refresh_ts is None
    assert rail._skills_snapshot_signature is None

    # The next invoke reads backend B and establishes B's real baseline.
    await rail.before_invoke(ctx)
    assert _resolved(path for path, _ in backend_b_spy.reads) == _resolved([skill_md])
    assert [skill.description for skill in rail.skills] == ["Local"]
    state = session.get_state("skill_use")
    assert state["provisional"] is False
    assert [item["description"] for item in state["baseline_skills"]] == ["Local"]
    assert [skill.description for skill in rail.get_skills_for_session(session)] == ["Local"]
    assert "Local" in rail.system_prompt_builder.build()


@pytest.mark.asyncio
async def test_language_switch_mid_scan_does_not_publish_mixed_language_descriptions(tmp_path: Path):
    """A language switch during a multi-skill scan must not publish a mixed set.

    Regression: each skill was localized with the live builder language as
    it was read and written to the cache at once. With the language switched
    while the second read was suspended, the first skill kept its Chinese
    text while the second came back in English; the set was published and the
    session saved it under the new language. A later scan repaired the rail,
    but the session's matching language label prevented rebasing it, so the
    Chinese text stayed in the prompt baseline.
    """
    skills_root = tmp_path / "skills"
    for name in ("alpha-skill", "beta-skill"):
        _write_skill(
            skills_root,
            name,
            f"Default {name}",
            description_cn=f"中文 {name}",
            description_en=f"English {name}",
        )
    started = asyncio.Event()
    gate = asyncio.Event()
    backend = _LocalLibraryBackend(skills_root, pause_on="beta-skill", started=started, gate=gate)
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder(language="cn")
    rail.set_sys_operation(backend)
    session = _SessionState("mid-scan-language")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    inflight = asyncio.create_task(rail.before_invoke(ctx))
    await asyncio.wait_for(started.wait(), timeout=5.0)
    # The first skill was read in Chinese; the scan is parked on the second.
    assert "alpha-skill" in backend.reads[0]

    rail.system_prompt_builder = SystemPromptBuilder(language="en")
    gate.set()
    await asyncio.wait_for(inflight, timeout=5.0)

    # Nothing was published under "en": no mixed or Chinese baseline reached
    # the session, and nothing reached the cache or the TTL stamp.
    assert session.get_state("skill_use") is None
    assert rail.skills == []
    assert rail._skill_cache == {}
    assert rail._cached_language is None
    assert rail._last_full_refresh_ts is None

    # The next invoke loads English cleanly into both rail and session.
    await rail.before_invoke(ctx)
    assert [(skill.name, skill.description) for skill in rail.skills] == [
        ("alpha-skill", "English alpha-skill"),
        ("beta-skill", "English beta-skill"),
    ]
    assert rail._cached_language == "en"
    state = session.get_state("skill_use")
    assert state["language"] == "en"
    assert [item["description"] for item in state["baseline_skills"]] == [
        "English alpha-skill",
        "English beta-skill",
    ]
    prompt = rail.system_prompt_builder.build()
    assert "English alpha-skill" in prompt
    assert "English beta-skill" in prompt
    assert "中文" not in prompt


@pytest.mark.asyncio
async def test_stale_model_callback_does_not_overwrite_prompt_after_rebind(tmp_path: Path):
    """A model callback resuming after a rebind must not publish the view it captured before.

    Regression: callback A captured its session baseline and paused on the
    evolution fetch. The rail was rebound and callback B rendered backend B's
    prompt for the same session and builder. A then resumed and replaced the
    section with A's captured baseline (and wrote its late evolution text
    into the cache and the attachment), although the rail and the session
    getter already answered with B.
    """
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "alpha", "Local")
    started = asyncio.Event()
    gate = asyncio.Event()

    class _PausingStore:
        """Parks the next call once and answers it with *late_text*; other calls read *texts*."""

        def __init__(self):
            self.texts = {"alpha": "- Current experience"}
            self.late_text = "- Late text from an obsolete callback"
            self.park = False

        async def format_desc_experience_text(self, skill_name: str) -> str:
            if self.park:
                self.park = False
                started.set()
                await gate.wait()
                return self.late_text
            return self.texts.get(skill_name, "")

    store = _PausingStore()
    backend_a = _CannedBackend("---\ndescription: Backend A\n---\n\n# alpha\n")
    backend_b = _CannedBackend("---\ndescription: Backend B\n---\n\n# alpha\n")
    rail = SkillUseRail(
        skills_dir=str(skills_root), skill_mode="all", include_tools=False, evolution_store=store
    )
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.attachment_manager = PromptAttachmentManager()
    rail.set_sys_operation(backend_a)
    session = _SessionState("stale-callback")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
    await rail.before_invoke(ctx)
    assert "Backend A" in rail.system_prompt_builder.build()

    # Callback A parks on the evolution fetch, after capturing A's baseline.
    store.park = True
    stale = asyncio.create_task(rail.before_model_call(ctx))
    await asyncio.wait_for(started.wait(), timeout=5.0)

    # The rail is rebound and callback B completes for the same session.
    rail.set_sys_operation(backend_b)
    await rail.before_invoke(ctx)
    assert "Backend B" in rail.system_prompt_builder.build()

    gate.set()
    await asyncio.wait_for(stale, timeout=5.0)

    prompt = rail.system_prompt_builder.build()
    assert "Backend B" in prompt
    assert "Backend A" not in prompt
    assert [skill.description for skill in rail.get_skills_for_session(session)] == ["Backend B"]
    assert rail._evolution_texts["alpha"] == "- Current experience"
    attachments = await rail.attachment_manager.collect_for_session("stale-callback")
    assert len(attachments) == 1
    assert "- Current experience" in (attachments[0].content or "")
    assert "Late text" not in (attachments[0].content or "")


@pytest.mark.asyncio
async def test_subdirectory_cwd_change_preserves_stable_session_baseline(tmp_path: Path):
    """Moving CWD into a subdirectory must not rewrite a stable session baseline."""
    from openjiuwen.core.sys_operation.cwd import _cwd_state, init_cwd, set_cwd

    root = tmp_path / "workspace"
    subdir = root / "nested"
    subdir.mkdir(parents=True)
    skills_root = root / "skills"
    _write_skill(skills_root, "alpha", "Original description")

    card = SysOperationCard(
        id=f"test_skill_rail_subdir_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(restrict_to_sandbox=True),
    )
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.set_sys_operation(SysOperation(card))
    session = _SessionState("subdir-session")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    prev_cwd = _cwd_state.get()
    try:
        init_cwd(str(root), workspace=str(root), project_root=str(root))
        await rail.before_invoke(ctx)
        persisted = session.get_state("skill_use")
        assert [item["description"] for item in persisted["baseline_skills"]] == ["Original description"]

        _write_skill(skills_root, "alpha", "Edited description")
        _write_skill(skills_root, "beta", "Installed after baseline")
        set_cwd(str(subdir))
        await rail.before_invoke(ctx)

        assert session.get_state("skill_use") == persisted
        assert [(s.name, s.description) for s in rail.get_skills_for_session(session)] == [
            ("alpha", "Original description"),
            ("beta", "Installed after baseline"),
        ]
    finally:
        _cwd_state.set(prev_cwd)


@pytest.mark.asyncio
async def test_overlapping_scans_do_not_commit_stale_or_mixed_language_results(tmp_path: Path):
    """A scan that resumes after a newer one started must not commit over the newer result.

    Regression: an older scan parked in one read while a newer scan loaded an
    edited description; the older one then resumed, replaced the cache entry
    with the bytes it had read and stamped the *current* disk signature, so
    the next model call skipped discovery and persisted the old text. With a
    language switch in between, the older scan reused entries the newer one
    had committed in the other language and persisted a mixed set.
    """
    # (a) The older scan lands after a newer one loaded the edited text.
    skills_root = tmp_path / "skills"
    skill_md = _write_skill(skills_root, "alpha", "First version") / "SKILL.md"
    started = asyncio.Event()
    gate = asyncio.Event()
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.set_sys_operation(_ParkingDiskBackend("alpha", started, gate))
    session = _SessionState("overlapping-scans")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    older = asyncio.create_task(rail.reload_skills())
    await asyncio.wait_for(started.wait(), timeout=5.0)
    edited = "Edited while the older scan was parked"
    _rewrite_skill(skill_md, edited, mtime=skill_md.stat().st_mtime + 10.0)
    await rail.reload_skills()
    gate.set()
    await asyncio.wait_for(older, timeout=5.0)

    assert [skill.description for skill in rail.skills] == [edited]
    assert rail._skills_snapshot_signature == rail._committed_snapshot_signature
    assert rail._skills_snapshot_signature == rail._build_skills_snapshot_signature()
    # The next model call skips discovery on that signature, as it may: the
    # text it keeps — and persists — is the edited one.
    await rail.before_model_call(ctx)
    assert [item["description"] for item in session.get_state("skill_use")["baseline_skills"]] == [edited]
    assert edited in rail.system_prompt_builder.build()

    # (b) An English scan resumes after a completed Chinese scan, with the
    # builder back on English.
    localized_root = tmp_path / "localized"
    for name in ("alpha-skill", "beta-skill"):
        _write_skill(
            localized_root,
            name,
            f"Default {name}",
            description_cn=f"中文 {name}",
            description_en=f"English {name}",
        )
    started = asyncio.Event()
    gate = asyncio.Event()
    rail = SkillUseRail(skills_dir=str(localized_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder(language="en")
    rail.set_sys_operation(_ParkingDiskBackend("alpha-skill", started, gate))
    session = _SessionState("overlapping-languages")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    older = asyncio.create_task(rail.before_invoke(ctx))
    await asyncio.wait_for(started.wait(), timeout=5.0)
    rail.system_prompt_builder = SystemPromptBuilder(language="cn")
    await rail.before_invoke(ctx)
    rail.system_prompt_builder = SystemPromptBuilder(language="en")
    gate.set()
    await asyncio.wait_for(older, timeout=5.0)

    # The newer result is preserved exactly as it was committed: one
    # language, and nothing mixed in under the builder's current one.
    assert [(skill.name, skill.description) for skill in rail.skills] == [
        ("alpha-skill", "中文 alpha-skill"),
        ("beta-skill", "中文 beta-skill"),
    ]
    assert rail._cached_language == "cn"
    state = session.get_state("skill_use")
    assert state["language"] == "cn"
    assert [item["description"] for item in state["baseline_skills"]] == [
        "中文 alpha-skill",
        "中文 beta-skill",
    ]

    # The builder is English again: the next invoke reloads English cleanly.
    await rail.before_invoke(ctx)
    assert [skill.description for skill in rail.skills] == ["English alpha-skill", "English beta-skill"]
    assert [item["description"] for item in session.get_state("skill_use")["baseline_skills"]] == [
        "English alpha-skill",
        "English beta-skill",
    ]
    assert "中文" not in rail.system_prompt_builder.build()


@pytest.mark.asyncio
async def test_unbound_prewarm_does_not_overwrite_restored_bound_session_baseline(tmp_path: Path):
    """A prewarm before the backend is bound must leave a restored bound baseline alone.

    Regression: a fresh rail ran ``before_invoke`` on a restored session
    before its original card was bound. The unbound (local) view was taken
    for a backend switch and written over the durable baseline as
    provisional, so rebinding the original card then promoted the *current*
    library — an edited description and a skill installed after the session
    started — as original session entries.
    """
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "alpha", "Original description")
    card = SysOperationCard(
        id=f"test_skill_rail_sysop_{tmp_path.name}_prewarm",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    session = _SessionState("bound-before-restart")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.set_sys_operation(SysOperation(card))
    await rail.before_invoke(ctx)
    persisted = session.get_state("skill_use")
    assert persisted["provisional"] is False
    assert [item["description"] for item in persisted["baseline_skills"]] == ["Original description"]

    # The library changes, as it may before a process comes back.
    _write_skill(skills_root, "alpha", "Edited on disk")
    _write_skill(skills_root, "beta", "Installed after the baseline")
    restored_session = _SessionState("restored", {"skill_use": persisted})
    restored_ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=restored_session)

    # The restarted rail is prewarmed before its card is bound: it serves its
    # provisional view and leaves the durable baseline untouched.
    restarted = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    restarted.system_prompt_builder = SystemPromptBuilder()
    await restarted.before_invoke(restored_ctx)
    assert restarted.sys_operation is None
    assert restored_session.get_state("skill_use") == persisted
    assert [skill.name for skill in restarted.get_skills_for_session(restored_session)] == ["alpha", "beta"]

    # The original card is bound: the durable baseline rehydrates untouched,
    # and the later installation stays a runtime addition.
    restarted.set_sys_operation(SysOperation(card))
    await restarted.before_invoke(restored_ctx)
    assert restored_session.get_state("skill_use") == persisted
    assert [(skill.name, skill.description) for skill in restarted.get_skills_for_session(restored_session)] == [
        ("alpha", "Original description"),
        ("beta", "Installed after the baseline"),
    ]
    prompt = restarted.system_prompt_builder.build()
    assert "Original description" in prompt
    assert "Edited on disk" not in prompt


@pytest.mark.asyncio
async def test_subclass_overriding_refresh_skill_prompt_returning_none_preserves_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A delegating override that keeps the public ``-> None`` contract loses no callback work.

    Regression: the base refresh returned ``bool`` and its callers read
    ``None`` as an aborted refresh, so a subclass following the public
    contract — await ``super()``, return None — loaded the skills but skipped
    the session baseline, the prompt and the TTL stamp in ``before_invoke``.
    """
    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "30")
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "invoice-parser", "Parse invoice pdf files")

    class _DelegatingRail(SkillUseRail):
        async def refresh_skill_prompt(self, ctx) -> None:
            await super().refresh_skill_prompt(ctx)

    rail = _DelegatingRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    session = _SessionState("delegating-override")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    assert await rail.refresh_skill_prompt(ctx) is None
    await rail.before_invoke(ctx)
    assert [skill.name for skill in rail.skills] == ["invoice-parser"]
    assert [item["name"] for item in session.get_state("skill_use")["baseline_skills"]] == ["invoice-parser"]
    assert "invoice-parser" in rail.system_prompt_builder.build()
    assert rail._last_full_refresh_ts is not None
    assert rail._skill_refresh_within_ttl() is True

    # Inside the window the second turn runs no scan, so a later installation
    # is not seen yet.
    _write_skill(skills_root, "xlsx-writer", "Write xlsx reports")
    await rail.before_invoke(ctx)
    assert [skill.name for skill in rail.get_skills_for_session(session)] == ["invoice-parser"]

    # The window expires and a model call finds the change: the refresh
    # through the override commits and re-opens the window.
    rail._last_full_refresh_ts = time.monotonic() - rail._skill_refresh_ttl_s - 1.0
    await rail.before_model_call(ctx)
    assert [skill.name for skill in rail.get_skills_for_session(session)] == ["invoice-parser", "xlsx-writer"]
    assert "xlsx-writer" not in rail.system_prompt_builder.build()
    assert rail._skill_refresh_within_ttl() is True


@pytest.mark.asyncio
async def test_subclass_visibility_and_refresh_override_edge_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Cover subclass filter ordering, post-super override waits, and custom non-delegating refreshes."""
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "alpha", "Alpha skill")
    _write_skill(skills_root, "beta", "Beta skill")

    # (a) Subclass _filter_skills ordering is never discarded even if visibility shifts during signature build.
    class _OrderedVisibilityRail(SkillUseRail):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._sig_calls = 0

        def _filter_skills(self, skills):
            return list(reversed(super()._filter_skills(skills)))

        def _build_skills_snapshot_signature(self):
            self._sig_calls += 1
            if self._sig_calls == 1:
                # Simulate a visibility provider flipping during the signature walk.
                self.disabled_skills = {"beta"}
            return super()._build_skills_snapshot_signature()

    ordered_rail = _OrderedVisibilityRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    await ordered_rail.reload_skills()
    assert [s.name for s in ordered_rail.skills] == ["beta", "alpha"]
    assert ordered_rail._skills_snapshot_signature is None

    # (b) Delegating override that awaits extra work after super() must not stamp TTL if superseded.
    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "30")
    after_super_started = asyncio.Event()
    after_super_gate = asyncio.Event()

    class _PostSuperAwaitRail(SkillUseRail):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.pause_next = False

        async def refresh_skill_prompt(self, ctx) -> None:
            await super().refresh_skill_prompt(ctx)
            if self.pause_next:
                self.pause_next = False
                after_super_started.set()
                await after_super_gate.wait()

    p_rail = _PostSuperAwaitRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    p_rail.system_prompt_builder = SystemPromptBuilder()
    p_rail.pause_next = True
    session_p = _SessionState("post-super")
    ctx_p = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session_p)

    t1 = asyncio.create_task(p_rail.before_invoke(ctx_p))
    await after_super_started.wait()
    _write_skill(skills_root, "gamma", "Gamma skill")
    await p_rail.reload_skills()
    after_super_gate.set()
    await t1

    # Superseded callback must not stamp TTL or freeze [alpha, beta] without gamma.
    assert p_rail._last_full_refresh_ts is None
    assert session_p.get_state("skill_use") is None
    await p_rail.before_invoke(ctx_p)
    assert [item["name"] for item in session_p.get_state("skill_use")["baseline_skills"]] == [
        "alpha",
        "beta",
        "gamma",
    ]

    # (c) Custom non-delegating refresh_skill_prompt override still persists its session baseline.
    from openjiuwen.core.single_agent.skills.skill_manager import Skill

    class _CustomRefreshRail(SkillUseRail):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.version = "v1"

        async def refresh_skill_prompt(self, ctx) -> None:
            _ = ctx
            self.skills = [Skill(name="custom", description=f"Desc {self.version}", directory=skills_root / "alpha")]

    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "0")
    c_rail = _CustomRefreshRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    c_rail.system_prompt_builder = SystemPromptBuilder()
    session_c = _SessionState("custom-refresh")
    ctx_c = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session_c)

    await c_rail.before_invoke(ctx_c)
    assert [item["description"] for item in session_c.get_state("skill_use")["baseline_skills"]] == ["Desc v1"]
    c_rail.version = "v2"
    await c_rail.before_invoke(ctx_c)
    # Persisted baseline keeps v1 across invokes just like base.
    assert [s.description for s in c_rail.get_skills_for_session(session_c)] == ["Desc v1"]
    assert "Desc v1" in c_rail.system_prompt_builder.build()


@pytest.mark.asyncio
async def test_scan_does_not_stamp_skill_installed_mid_read(tmp_path: Path):
    """A skill installed while an earlier read is pending must not enter the stamped signature."""
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "alpha", "Alpha skill")

    started = asyncio.Event()
    gate = asyncio.Event()
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.set_sys_operation(_LocalLibraryBackend(skills_root, pause_on="alpha", started=started, gate=gate))

    session = _SessionState("mid-install")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    task = asyncio.create_task(rail.reload_skills())
    await started.wait()
    # Install zulu while alpha's read is suspended.
    _write_skill(skills_root, "zulu", "Zulu skill")
    rail.sys_operation._texts["zulu"] = "---\ndescription: Zulu skill\n---\n# zulu\n"
    gate.set()
    await task

    assert [s.name for s in rail.skills] == ["alpha"]
    assert all("zulu" not in entry[0] for entry in (rail._skills_snapshot_signature or ()))
    # The stamped signature must not include zulu, so the next model call discovers it.
    await rail.before_model_call(ctx)
    assert [s.name for s in rail.skills] == ["alpha", "zulu"]
    assert [s.name for s in rail.get_skills_for_session(session)] == ["alpha", "zulu"]
    assert "Zulu skill" in rail.system_prompt_builder.build()


@pytest.mark.asyncio
async def test_superseded_refresh_does_not_stamp_ttl_or_freeze_stale_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A superseded refresh must not accept an older commit token in before_invoke or before_model_call."""
    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "30")
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "alpha", "Alpha skill")

    # Initial committed scan in generation 0.
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    await rail.reload_skills()
    assert [s.name for s in rail.skills] == ["alpha"]

    # Install beta before the new session starts; pause scan #2 while scan #3 supersedes it.
    _write_skill(skills_root, "beta", "Beta skill")
    started_2 = asyncio.Event()
    gate_2 = asyncio.Event()
    started_3 = asyncio.Event()
    gate_3 = asyncio.Event()
    call_count = {"beta": 0}
    orig_load = rail._load_skill

    async def _paced_load(skill_dir: Path, update_at: float):
        if skill_dir.name == "beta":
            call_count["beta"] += 1
            idx = call_count["beta"]
            if idx == 1:
                started_2.set()
                await gate_2.wait()
            elif idx == 2:
                started_3.set()
                await gate_3.wait()
        return await orig_load(skill_dir, update_at)

    monkeypatch.setattr(rail, "_load_skill", _paced_load)

    # (a) Superseded before_invoke on a brand-new session must not stamp TTL or freeze [alpha].
    session_a = _SessionState("superseded-invoke")
    ctx_a = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session_a)
    rail._last_full_refresh_ts = None
    t2 = asyncio.create_task(rail.before_invoke(ctx_a))
    await started_2.wait()

    t3 = asyncio.create_task(rail.reload_skills())
    await started_3.wait()

    # Scan #2 resumes while scan #3 is still pending -> must abort without freezing stale [alpha] or TTL.
    gate_2.set()
    await t2
    assert session_a.get_state("skill_use") is None
    assert rail._last_full_refresh_ts is None

    # Finish scan #3; next before_invoke establishes [alpha, beta] cleanly.
    gate_3.set()
    await t3
    await rail.before_invoke(ctx_a)
    assert [item["name"] for item in session_a.get_state("skill_use")["baseline_skills"]] == ["alpha", "beta"]
    assert "beta" in rail.system_prompt_builder.build()

    # (b) The model callback waits for the replacement without freezing its old view.
    _write_skill(skills_root, "gamma", "Gamma skill")
    started_m1 = asyncio.Event()
    gate_m1 = asyncio.Event()
    finished_m1_read = asyncio.Event()
    started_m2 = asyncio.Event()
    gate_m2 = asyncio.Event()
    call_count["gamma"] = 0

    async def _paced_gamma(skill_dir: Path, update_at: float):
        if skill_dir.name != "gamma":
            return await orig_load(skill_dir, update_at)
        call_count["gamma"] += 1
        idx = call_count["gamma"]
        if idx == 1:
            started_m1.set()
            await gate_m1.wait()
            loaded = await orig_load(skill_dir, update_at)
            finished_m1_read.set()
            return loaded
        if idx == 2:
            started_m2.set()
            await gate_m2.wait()
        return await orig_load(skill_dir, update_at)

    monkeypatch.setattr(rail, "_load_skill", _paced_gamma)
    rail._last_full_refresh_ts = None
    session_b = _SessionState("superseded-model-call")
    ctx_b = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session_b)

    tm1 = asyncio.create_task(rail.before_model_call(ctx_b))
    await asyncio.wait_for(started_m1.wait(), timeout=5)
    tm2 = asyncio.create_task(rail.reload_skills())
    try:
        await asyncio.wait_for(started_m2.wait(), timeout=5)
        gate_m1.set()
        await asyncio.wait_for(finished_m1_read.wait(), timeout=5)
        assert not tm1.done(), "the model callback needs the replacement scan before it can return"
        assert session_b.get_state("skill_use") is None
        assert rail._last_full_refresh_ts is None
    finally:
        gate_m1.set()
        gate_m2.set()
        await asyncio.wait_for(asyncio.gather(tm1, tm2), timeout=5)

    assert [item["name"] for item in session_b.get_state("skill_use")["baseline_skills"]] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert "gamma" in rail.system_prompt_builder.build()


@pytest.mark.asyncio
@pytest.mark.parametrize("producer_fails", [False, True], ids=["both-requests", "producer-failed"])
async def test_overlapping_skill_refreshes_gate_final_model_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, producer_fails: bool
):
    """Inspect completed requests and verify that a failed refresh stops its request."""
    from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
    from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig
    from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
    from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response

    skills_root = tmp_path / "skills"
    description = "Summarize open invoices by client"
    _write_skill(skills_root, "invoice-parser", description)
    backend = _TwoPausedReadsBackend(description)
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.set_sys_operation(backend)
    if producer_fails:
        _fail_second_skill_load(rail, backend, monkeypatch)
    model = MockLLMModel()
    model.set_responses([create_text_response("Unexpected model call")])
    agent = ReActAgent(card=AgentCard(name="concurrent-skill-model")).configure(
        ReActAgentConfig(
            model_config_obj=ModelRequestConfig(model="mock-model"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="mock-key", api_base="https://example.com/v1", verify_ssl=False
            ),
            prompt_template=[{"role": "system", "content": "You are a test assistant."}],
        )
    )
    monkeypatch.setattr(agent, "_get_llm", lambda: model)
    rail.system_prompt_builder = agent.prompt_builder
    await agent.register_callback(AgentCallbackEvent.BEFORE_MODEL_CALL, rail.before_model_call)
    seen_requests = []
    seen_baselines = []

    async def record_final_request(ctx):
        seen_requests.append(
            next(message.content for message in ctx.inputs.messages if isinstance(message, SystemMessage))
        )
        seen_baselines.append(ctx.session.get_state("skill_use"))
        if producer_fails and ctx.session.get_session_id() == "second-skill-call":
            return None
        return {"result_type": "stopped_before_model", "output": ""}

    agent.set_final_request_check(record_final_request)
    tasks = [asyncio.create_task(agent.invoke({"query": "First request", "conversation_id": "first-skill-call"}))]
    try:
        await asyncio.wait_for(backend.started[0].wait(), timeout=5)
        tasks.append(
            asyncio.create_task(agent.invoke({"query": "Second request", "conversation_id": "second-skill-call"}))
        )
        await asyncio.wait_for(backend.started[1].wait(), timeout=5)
        backend.resume[0].set()
        await asyncio.wait_for(backend.first_read_finished.wait(), timeout=5)
        assert not seen_requests, "the first request cannot reach the final model boundary before a skill scan commits"
    finally:
        backend.resume[0].set()
        backend.resume[1].set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)

    assert results[0]["result_type"] == "stopped_before_model"
    if producer_fails:
        assert isinstance(results[1], RuntimeError)
        assert str(results[1]) == "skill scan failed"
        assert len(seen_requests) == 1
    else:
        assert results[1]["result_type"] == "stopped_before_model"
        assert len(seen_requests) == 2
    assert all(description in prompt for prompt in seen_requests)
    assert all(state["baseline_skills"][0]["description"] == description for state in seen_baselines)
    assert model.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["waiter-cancelled", "producer-cancelled", "producer-failed"])
async def test_skill_refresh_interruption_preserves_the_other_model_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: str
):
    """The remaining callback can use a live producer or start a replacement scan."""
    from openjiuwen.core.runner.callback import AbortError

    skills_root = tmp_path / "skills"
    description = "Group invoices by customer"
    _write_skill(skills_root, "invoice-parser", description)
    backend = _TwoPausedReadsBackend(description)
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.set_sys_operation(backend)
    sessions = [_SessionState("first-callback"), _SessionState("second-callback")]
    contexts = [AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=s) for s in sessions]
    interrupted_index = 0 if interruption == "waiter-cancelled" else 1
    if interruption == "producer-failed":
        _fail_second_skill_load(rail, backend, monkeypatch)

    tasks = [asyncio.create_task(rail.before_model_call(contexts[0]))]
    try:
        await asyncio.wait_for(backend.started[0].wait(), timeout=5)
        tasks.append(asyncio.create_task(rail.before_model_call(contexts[1])))
        await asyncio.wait_for(backend.started[1].wait(), timeout=5)
        backend.resume[0].set()
        await asyncio.wait_for(backend.first_read_finished.wait(), timeout=5)
        assert not tasks[0].done(), "the first callback is waiting for the producer"
        if interruption == "producer-failed":
            backend.resume[1].set()
            with pytest.raises(AbortError) as error:
                await asyncio.wait_for(tasks[interrupted_index], timeout=5)
            assert isinstance(error.value.cause, RuntimeError)
            assert str(error.value.cause) == "skill scan failed"
        else:
            tasks[interrupted_index].cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(tasks[interrupted_index], timeout=5)

        backend.resume[1].set()
        await asyncio.wait_for(tasks[1 - interrupted_index], timeout=5)
    finally:
        backend.resume[0].set()
        backend.resume[1].set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)

    assert sessions[interrupted_index].get_state("skill_use") is None
    surviving_baseline = sessions[1 - interrupted_index].get_state("skill_use")["baseline_skills"]
    assert [skill["description"] for skill in surviving_baseline] == [description]
    assert description in rail.system_prompt_builder.build()
    assert len(backend.reads) == (2 if interrupted_index == 0 else 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("new_language", [None, "en"], ids=["backend-rebind", "language-switch"])
async def test_skill_refresh_waiter_uses_current_view_after_invalidation(tmp_path: Path, new_language: str | None):
    """A model callback can finish with the new view while the obsolete read remains parked."""
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "invoice-parser", "On-disk description")
    current_description = "Current skill description"
    previous = _TwoPausedReadsBackend("Previous skill description", description_en=current_description)
    current = _CannedBackend(f"---\ndescription: {current_description}\n---\n")
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder(language="cn")
    rail.set_sys_operation(previous)
    session = _SessionState("waiting-during-rebind")
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)

    tasks = [asyncio.create_task(rail.before_model_call(ctx))]
    try:
        await asyncio.wait_for(previous.started[0].wait(), timeout=5)
        tasks.append(asyncio.create_task(rail.reload_skills()))
        await asyncio.wait_for(previous.started[1].wait(), timeout=5)
        previous.resume[0].set()
        await asyncio.wait_for(previous.first_read_finished.wait(), timeout=5)
        assert not tasks[0].done()
        assert session.get_state("skill_use") is None

        if new_language is None:
            rail.set_sys_operation(current)
        else:
            rail.system_prompt_builder = SystemPromptBuilder(language=new_language)
            await asyncio.wait_for(rail.reload_skills(), timeout=5)
        await asyncio.wait_for(tasks[0], timeout=5)
        assert not tasks[1].done(), "the obsolete read is still parked"
        state = session.get_state("skill_use")
        assert state["language"] == (new_language or "cn")
        assert [skill["description"] for skill in state["baseline_skills"]] == [current_description]
    finally:
        previous.resume[0].set()
        previous.resume[1].set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    assert current_description in rail.system_prompt_builder.build()
    assert [skill.description for skill in rail.skills] == [current_description]


@pytest.mark.asyncio
async def test_overlapping_model_callbacks_refresh_in_their_own_read_scope(tmp_path: Path):
    """A pending scan in another task's filesystem scope cannot supply a model's skills."""
    from openjiuwen.core.sys_operation.cwd import get_cwd, init_cwd

    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "invoice-parser", "On-disk description")

    class _ScopedBackend(_TwoPausedReadsBackend):
        async def read_file(self, path: str, **kwargs: Any):
            result = await super().read_file(path, **kwargs)
            scope = Path(get_cwd()).name
            result.data.content = f"---\ndescription: Skill from {scope}\n---\n"
            return result

    backend = _ScopedBackend("Unused description")
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    rail.set_sys_operation(backend)
    sessions = [_SessionState("first-scope"), _SessionState("second-scope")]

    async def call_in_scope(session: _SessionState):
        root = tmp_path / session.get_session_id()
        root.mkdir()
        init_cwd(str(root), project_root=str(root), workspace=str(root))
        ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=session)
        await rail.before_model_call(ctx)
        return rail.system_prompt_builder.build()

    tasks = [asyncio.create_task(call_in_scope(sessions[0]))]
    try:
        await asyncio.wait_for(backend.started[0].wait(), timeout=5)
        tasks.append(asyncio.create_task(call_in_scope(sessions[1])))
        await asyncio.wait_for(backend.started[1].wait(), timeout=5)
        backend.resume[0].set()
        first_prompt = await asyncio.wait_for(tasks[0], timeout=5)
        assert "Skill from first-scope" in first_prompt
        assert "Skill from second-scope" not in first_prompt
        assert not tasks[1].done(), "the first request can retry without waiting for another filesystem scope"
    finally:
        backend.resume[0].set()
        backend.resume[1].set()
        prompts = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    assert "Skill from second-scope" in prompts[1]
    assert "Skill from first-scope" not in prompts[1]
    for session in sessions:
        baseline = session.get_state("skill_use")["baseline_skills"]
        assert [skill["description"] for skill in baseline] == [f"Skill from {session.get_session_id()}"]


@pytest.mark.asyncio
async def test_rebind_clears_prompt_section_preserves_legacy_state_and_avoids_epoch_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Cover prompt clearing on rebind, legacy preservation after A->B->None, TTL skip on visibility flip, and GC."""
    skills_root = tmp_path / "skills"
    alpha_dir = _write_skill(skills_root, "alpha", "Local prewarm description")

    # (a) Rendering locally then binding backend B clears the previous SKILLS section immediately.
    rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    s1 = _SessionState("clear-section")
    ctx1 = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=s1)
    await rail.before_invoke(ctx1)
    assert "Local prewarm description" in rail.system_prompt_builder.build()

    rail.set_sys_operation(_CannedBackend("---\ndescription: Bound description\n---\n# alpha\n"))
    assert "Local prewarm description" not in rail.system_prompt_builder.build()

    # (b) After A -> B -> None, an unbound invoke must preserve a legacy schema-v1 session untouched.
    rail.set_sys_operation(_CannedBackend("---\ndescription: Backend B\n---\n# alpha\n"))
    rail.set_sys_operation(None)
    legacy_payload = {
        "schema_version": 1,
        "baseline_skills": [
            {
                "name": "alpha",
                "description": "Legacy persisted description",
                "directory": str(alpha_dir),
            }
        ],
    }
    legacy_session = _SessionState("legacy-after-unbind", {"skill_use": dict(legacy_payload)})
    legacy_ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=legacy_session)
    await rail.before_invoke(legacy_ctx)
    assert legacy_session.get_state("skill_use") == legacy_payload
    assert [s.description for s in rail.get_skills_for_session(legacy_session)] == ["Legacy persisted description"]

    # (c) Visibility shift during reload_skills even inside an open 30s TTL must
    # invalidate the TTL window so the next model callback immediately polls revocation.
    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "30")
    _write_skill(skills_root, "beta", "Beta skill")

    class _FlippingVisibilityRail(SkillUseRail):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._revoked = False
            self.arm_flip = False

        def _filter_skills(self, skills):
            self.disabled_skills = {"beta"} if self._revoked else set()
            return super()._filter_skills(skills)

        def _build_skills_snapshot_signature(self):
            if self.arm_flip:
                self.arm_flip = False
                self._revoked = True
                self.disabled_skills = {"beta"}
            sig = super()._build_skills_snapshot_signature()
            return (*sig, ("vis.deny:beta", 0.0)) if self._revoked else sig

    v_rail = _FlippingVisibilityRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    v_rail.system_prompt_builder = SystemPromptBuilder()
    v_ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=_SessionState("vis-ttl"))
    await v_rail.before_invoke(v_ctx)
    assert v_rail._skill_refresh_within_ttl() is True

    # Open TTL window; reload_skills observes revocation during signature build -> must clear TTL!
    v_rail.arm_flip = True
    await v_rail.reload_skills()
    assert v_rail._last_full_refresh_ts is None
    assert v_rail._skill_refresh_within_ttl() is False

    await v_rail.before_model_call(v_ctx)
    assert [s.name for s in v_rail.skills] == ["alpha"]
    assert v_rail._last_full_refresh_ts is not None

    # (d) Pending runtime attachment cleared on superseded bound callback, wrapper
    # recreation preserves baseline after A -> B, and custom refresh preserves 30s TTL.
    from openjiuwen.core.single_agent.skills.skill_manager import Skill
    from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager

    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "0")
    att_rail = SkillUseRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    att_rail.system_prompt_builder = SystemPromptBuilder()
    att_rail.attachment_manager = PromptAttachmentManager()
    att_session = _SessionState("att-clear")
    att_ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=att_session)
    await att_rail.before_invoke(att_ctx)
    _write_skill(skills_root, "extra", "Local extra description")
    await att_rail.before_model_call(att_ctx)
    before_atts = await att_rail.attachment_manager.collect_for_session("att-clear")
    assert any("Local extra description" in (item.content or "") for item in before_atts)

    # Rebind while a runtime attachment is present. The superseded callback clears it before waiting.
    backend = _TwoPausedReadsBackend("Bound text")
    att_rail.set_sys_operation(backend)
    t_cb = asyncio.create_task(att_rail.before_model_call(att_ctx))
    await asyncio.wait_for(backend.started[0].wait(), timeout=5)
    t_reload = asyncio.create_task(att_rail.reload_skills())
    try:
        await asyncio.wait_for(backend.started[1].wait(), timeout=5)
        backend.resume[0].set()
        await asyncio.wait_for(backend.first_read_finished.wait(), timeout=5)
        assert not t_cb.done()
        after_atts = await att_rail.attachment_manager.collect_for_session("att-clear")
        assert all("Local extra description" not in (item.content or "") for item in after_atts)
    finally:
        backend.resume[0].set()
        backend.resume[1].set()
        await asyncio.wait_for(asyncio.gather(t_cb, t_reload), timeout=5)

    # After A -> B reconciliation, restoring persisted state into a fresh wrapper preserves baseline.
    att_rail.set_sys_operation(_CannedBackend("---\ndescription: Backend C\n---\n# s\n"))
    w1 = _SessionState("recreated-wrapper")
    await att_rail.before_invoke(AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=w1))
    saved_w1 = dict(w1.get_state("skill_use"))
    _write_skill(skills_root, "delta", "Delta installed later")
    w2 = _SessionState("recreated-wrapper", {"skill_use": saved_w1})
    await att_rail.before_invoke(AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=w2))
    assert w2.get_state("skill_use") == saved_w1
    assert "delta" not in [item["name"] for item in w2.get_state("skill_use")["baseline_skills"]]
    assert "delta" in [s.name for s in att_rail.get_skills_for_session(w2)]

    # Custom non-delegating refresh_skill_prompt preserves 30s TTL reuse.
    monkeypatch.setenv("SKILL_REFRESH_TTL_SECONDS", "30")
    class _TtlCustomRail(SkillUseRail):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls = 0

        async def refresh_skill_prompt(self, ctx) -> None:
            _ = ctx
            self.calls += 1
            self.skills = [Skill(name="custom", description="Custom TTL", directory=alpha_dir)]

    ttl_custom = _TtlCustomRail(skills_dir=str(skills_root), skill_mode="all", include_tools=False)
    ttl_custom.system_prompt_builder = SystemPromptBuilder()
    tc_ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=_SessionState("ttl-custom"))
    await ttl_custom.before_invoke(tc_ctx)
    await ttl_custom.before_invoke(tc_ctx)
    await ttl_custom.before_model_call(tc_ctx)
    assert ttl_custom.calls == 1


def test_skill_use_rail_apply_skill_budget_no_budget():
    """When no budget is set, all skills are returned unchanged."""
    rail = SkillUseRail(skills_dir="./", skill_mode="all", include_tools=False)
    skills = [
        _MockSkill(name="alpha", description="Alpha does alpha things."),
        _MockSkill(name="beta", description="Beta handles beta tasks."),
    ]
    result = rail._apply_skill_budget(skills, query="do alpha")
    assert [s.name for s in result] == ["alpha", "beta"]


def test_skill_use_rail_apply_skill_budget_max_skills():
    """max_skills drops lowest-ranked whole skills."""
    rail = SkillUseRail(
        skills_dir="./", skill_mode="all", include_tools=False, max_skills=2
    )
    skills = [
        _MockSkill(name="alpha", description="Alpha does alpha things."),
        _MockSkill(name="beta", description="Beta handles beta tasks."),
        _MockSkill(name="gamma", description="Gamma is for gamma work."),
    ]
    result = rail._apply_skill_budget(skills, query="alpha tasks")
    assert len(result) == 2
    assert result[0].name == "alpha"  # highest keyword overlap


def test_skill_use_rail_apply_skill_budget_max_total_chars():
    """max_total_chars is a soft cap: when the single highest-ranked skill alone
    exceeds the budget, it is still retained (never an empty skill list)."""
    rail = SkillUseRail(
        skills_dir="./",
        skill_mode="all",
        include_tools=False,
        max_total_chars=50,
    )
    skills = [
        _MockSkill(name="alpha", description="Short."),
        _MockSkill(name="beta", description="This is a much longer description that exceeds budget."),
    ]
    result = rail._apply_skill_budget(skills, query="beta")
    # beta ranks highest (query match) and alone exceeds the 50-char budget;
    # dropping it too would leave the model with no skills at all.
    assert result, "highest-ranked skill must be retained even when over budget"
    assert [s.name for s in result] == ["beta"]


def test_skill_use_rail_apply_skill_budget_max_total_chars_drops_low_ranked():
    """max_total_chars drops whole lowest-ranked skills until back under budget."""
    rail = SkillUseRail(
        skills_dir="./",
        skill_mode="all",
        include_tools=False,
        max_total_chars=50,
    )
    skills = [
        _MockSkill(name="alpha", description="Short."),
        _MockSkill(name="beta", description="Beta handles beta tasks."),
        _MockSkill(name="gamma", description="Gamma is for gamma work."),
    ]
    result = rail._apply_skill_budget(skills, query="beta")
    # beta ranks highest; gamma ties with alpha at the bottom and is dropped
    # first, leaving beta + alpha comfortably under budget.
    assert [s.name for s in result] == ["beta", "alpha"]
    total = sum(len(s.description) for s in result)
    assert total <= 50


def test_skill_use_rail_extract_keywords():
    """Keyword extraction removes stop words and short tokens."""
    text = "The quick brown fox does something with Python code."
    keywords = SkillUseRail._extract_keywords(text)
    assert "quick" in keywords
    assert "brown" in keywords
    assert "fox" in keywords
    assert "python" in keywords
    assert "code" in keywords
    assert "the" not in keywords  # stop word
    assert "does" not in keywords  # stop word


class _MockSkill:
    """Minimal mock for Skill objects in budget tests."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
