# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeepAgent runtime ExpertHarness loading E2E tests.

Test matrix
-----------
| Class | Scenario | External pkg / live model |
|-------|----------|---------------------------|
| TestDeepAgentSpecBuild | Cold ``DeepAgentSpec.build()``: custom tool+rail mount and invoke | No |
| TestHotLoadExpertHarness | Hot load via ``load_expert_harness``: tool/rail/skill/prompt | No (in-test mini packages) |
| TestRunnerHotLoadSmoke | ``Runner.run_agent`` + enqueue harness_config hot-load | No (in-test mini AH package) |

All ExpertHarness packages used here are generated under ``tmp_path``:

- AutoHarness-shaped office mini package (``resources.type=package`` tools/rails + ``skills.dirs``)
- AutoCoordinatingHarness-shaped frontend mini package (``identity.md`` / ``soul.md`` / ``prompt_sections``)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import pytest
import yaml
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
    ToolCallInputs,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperation,
    SysOperationCard,
)
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.resources.expert_harness_parts import ResourceKind
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.schema.deep_agent_spec import (
    BuiltinToolSpec,
    DeepAgentSpec,
    RailSpec as ColdRailSpec,
    SysOperationSpec,
    WorkspaceSpec,
)
from openjiuwen.harness.workspace.workspace import Workspace

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IDENTITY_SECTION_NAME = "identity"
_SOUL_SECTION_NAME = "soul"
_ROLE_PLAYBOOK_SECTION_NAME = "team_skill_role_playbook"
_FRONTEND_IDENTITY_MARKERS = (
    "frontend-developer",
    "card-battle-game-swarm",
    "index.html, styles.css, game.js",
)
_FRONTEND_SOUL_MARKERS = (
    "Team Skill workflow",
    "frontend-developer",
    "responsibility boundary",
)
_FRONTEND_ROLE_MARKERS = (
    "Role: Frontend Developer",
    "index.html, styles.css, game.js",
    "vanilla HTML/CSS/JS",
)
_FRONTEND_PACKAGE_NAME = "frontend_developer_mini"

_EXPECTED_SKILL_NAMES = frozenset({"docx", "pptx", "xlsx"})
_SKILL_DESCRIPTION_MARKERS = {
    "docx": "Word documents",
    "pptx": "presentation",
    "xlsx": "spreadsheet file",
}

_FROM_SPEC_RAIL_ANSWER = "from_spec rail observed tool result"
_FROM_SPEC_SKILL_BODY_MARKER = "Use this skill only to prove static spec skill loading."
_FROM_SPEC_SKILL_DESCRIPTION_MARKER = "DeepAgentSpec static ExpertHarnessSpec"
_HOT_LOAD_IDENTITY_MARKER = "hot-load-identity-section-marker"
_HOT_LOAD_SOUL_MARKER = "hot-load-soul-section-marker"
_MINI_SIDECAR_TOOL_CLASS = "MiniSidecarTool"
_MINI_SIDECAR_TOOL_MODULE_LEAF = "mini_sidecar_tool"
_MINI_SIDECAR_RAIL_CLASS = "FilenameGuardRail"
_MINI_SIDECAR_RAIL_MODULE_LEAF = "filename_guard_rail"
_MINI_SIDECAR_PACKAGE_NAME = "office_document_generator_mini"
_MINI_SIDECAR_SKILL_NAME = "xlsx"
_MINI_SIDECAR_SKILL_DESCRIPTION_MARKER = "spreadsheet file"
_MINI_SIDECAR_SKILL_BODY_MARKER = "Mini sidecar xlsx skill body for hot-load invoke."
_MINI_SIDECAR_TOOL_SENTINEL = "mini_sidecar_tool_used"
_MINI_SIDECAR_SKILL_SPECS = (
    ("docx", "Word documents", "Mini AH-shaped docx skill body."),
    ("pptx", "presentation", "Mini AH-shaped pptx skill body."),
    ("xlsx", _MINI_SIDECAR_SKILL_DESCRIPTION_MARKER, _MINI_SIDECAR_SKILL_BODY_MARKER),
)


# ---------------------------------------------------------------------------
# Runner isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test a fresh Runner so global resource_mgr state does not leak."""
    import openjiuwen.core.runner.runner as runner_module
    from openjiuwen.core.runner.runner_config import DEFAULT_RUNNER_CONFIG

    monkeypatch.setattr(
        runner_module,
        "GLOBAL_RUNNER",
        runner_module._RunnerImpl(config=DEFAULT_RUNNER_CONFIG),
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _parse_tool_args(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


class _ForceFinishAfterNamedToolRail(AgentRail):
    """Terminate the agent loop after a named tool runs; capture tool_result."""

    priority = 20

    def __init__(self, tool_name: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.tool_results: list[Any] = []

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        if ctx.inputs.tool_name != self.tool_name:
            return
        self.tool_results.append(ctx.inputs.tool_result)
        ctx.request_force_finish(
            {
                "result_type": "answer",
                "output": f"{self.tool_name} completed",
                "tool_result": ctx.inputs.tool_result,
            }
        )


class _DeterministicToolCallModel:
    """Fake model that deterministically emits one tool call."""

    model_config = None

    def __init__(self, *, tool_name: str, tool_args: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.tool_args = dict(tool_args)
        self.call_history: list[dict[str, Any]] = []
        from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig

        self.model_client_config = ModelClientConfig(
            client_provider="openai",
            api_key="fake-key-for-e2e-test",
            api_base="http://localhost:0",
        )

    async def invoke(
        self,
        messages: Any,
        *,
        tools: Any = None,
        **kwargs: object,
    ) -> AssistantMessage:
        _ = kwargs
        self.call_history.append(
            {
                "messages": list(messages) if isinstance(messages, list) else [messages],
                "tools": list(tools or []),
            }
        )
        return AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    id="from_spec_static_tool_call",
                    type="function",
                    name=self.tool_name,
                    arguments=json.dumps(self.tool_args),
                )
            ],
            finish_reason="tool_calls",
        )

    async def stream(self, *args: object, **kwargs: object) -> None:
        _ = args, kwargs
        raise AssertionError("deterministic fake model should not use stream()")

    @property
    def call_count(self) -> int:
        return len(self.call_history)

    def last_system_prompt(self) -> str:
        if not self.call_history:
            return ""
        for message in self.call_history[-1]["messages"]:
            role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
            if role == "system" and isinstance(content, str):
                return content
        return ""

    def last_tool_names(self) -> set[str]:
        if not self.call_history:
            return set()
        names: set[str] = set()
        for tool in self.call_history[-1]["tools"]:
            name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
            if isinstance(name, str):
                names.add(name)
        return names


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ToolRailFixture:
    tool_id: str
    tool_name: str
    tool_path: Path
    rail_path: Path
    skill_root: Path | None = None


def _write_from_spec_tool_file(path: Path, *, tool_id: str, tool_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "from typing import Any, AsyncIterator",
                "from openjiuwen.core.foundation.tool import Tool, ToolCard",
                "",
                "class FromSpecStaticTool(Tool):",
                "    def __init__(self) -> None:",
                (
                    "        super().__init__(ToolCard("
                    f"id={tool_id!r}, name={tool_name!r}, description='from_spec static tool'))"
                ),
                "",
                "    async def invoke(",
                "        self,",
                "        inputs: dict[str, Any],",
                "        **kwargs: object,",
                "    ) -> dict[str, Any]:",
                "        return inputs",
                "",
                "    async def stream(",
                "        self,",
                "        inputs: dict[str, Any],",
                "        **kwargs: object,",
                "    ) -> AsyncIterator[dict[str, Any]]:",
                "        if False:",
                "            yield inputs",
            ]
        ),
        encoding="utf-8",
    )


def _write_from_spec_rail_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import json",
                "from typing import Any",
                "from openjiuwen.core.single_agent.rail.base import (",
                "    AgentCallbackContext,",
                "    AgentRail,",
                "    ToolCallInputs,",
                ")",
                "",
                "def _parse_args(raw: Any) -> Any:",
                "    if isinstance(raw, str) and raw.strip():",
                "        try:",
                "            return json.loads(raw)",
                "        except json.JSONDecodeError:",
                "            return raw",
                "    return raw",
                "",
                "class FromSpecStaticRail(AgentRail):",
                "    priority = 40",
                "",
                "    def __init__(self, source_marker: str = '') -> None:",
                "        super().__init__()",
                "        self.source_marker = source_marker",
                "        self.before_tool_calls: list[dict[str, Any]] = []",
                "        self.after_tool_calls: list[dict[str, Any]] = []",
                "",
                "    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:",
                "        if not isinstance(ctx.inputs, ToolCallInputs):",
                "            return",
                "        self.before_tool_calls.append({",
                "            'name': ctx.inputs.tool_name,",
                "            'args': _parse_args(ctx.inputs.tool_args),",
                "        })",
                "",
                "    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:",
                "        if not isinstance(ctx.inputs, ToolCallInputs):",
                "            return",
                "        result = ctx.inputs.tool_result",
                "        self.after_tool_calls.append({",
                "            'name': ctx.inputs.tool_name,",
                "            'args': _parse_args(ctx.inputs.tool_args),",
                "            'result': result,",
                "        })",
                "        ctx.request_force_finish({",
                "            'result_type': 'answer',",
                f"            'output': {_FROM_SPEC_RAIL_ANSWER!r},",
                "            'tool_result': result,",
                "        })",
            ]
        ),
        encoding="utf-8",
    )


def _write_from_spec_skill(root: Path) -> Path:
    skill_dir = root / "from_spec_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: from_spec_skill",
                f"description: Loaded from {_FROM_SPEC_SKILL_DESCRIPTION_MARKER}",
                "---",
                "",
                "# From Spec Skill",
                "",
                _FROM_SPEC_SKILL_BODY_MARKER,
            ]
        ),
        encoding="utf-8",
    )
    return root


def _setup_tool_rail_fixture(
    tmp_path: Path,
    *,
    prefix: str,
    with_skill: bool = False,
) -> _ToolRailFixture:
    tool_id = f"{prefix}_tool_{tmp_path.name}"
    tool_name = f"{prefix}_tool_name_{tmp_path.name}"
    base = tmp_path / "expert_harness"
    tool_path = base / "tools" / f"{prefix}_tool.py"
    rail_path = base / "rails" / f"{prefix}_rail.py"
    _write_from_spec_tool_file(tool_path, tool_id=tool_id, tool_name=tool_name)
    _write_from_spec_rail_file(rail_path)
    skill_root = _write_from_spec_skill(base / "skills") if with_skill else None
    return _ToolRailFixture(
        tool_id=tool_id,
        tool_name=tool_name,
        tool_path=tool_path,
        rail_path=rail_path,
        skill_root=skill_root,
    )


def _write_hot_load_package(
    tmp_path: Path,
    *,
    package_id: str,
    prefix: str,
) -> tuple[Path, _ToolRailFixture, str, str]:
    """Write an on-disk ExpertHarness package for ``load_expert_harness`` (tool/rail/skill/prompt)."""
    fixture = _setup_tool_rail_fixture(tmp_path, prefix=prefix, with_skill=True)
    package = tmp_path / "expert_harness"
    identity_text = f"# Identity\n\n{_HOT_LOAD_IDENTITY_MARKER}\n"
    soul_text = f"# Soul\n\n{_HOT_LOAD_SOUL_MARKER}\n"
    manifest = {
        "schema_version": "expert_harness.v1",
        "id": package_id,
        "tools": [
            {
                "type": "harness.tool.file",
                "params": {
                    "file_path": str(fixture.tool_path),
                    "class_name": "FromSpecStaticTool",
                },
            }
        ],
        "rails": [
            {
                "type": "harness.rail.file",
                "params": {
                    "file_path": str(fixture.rail_path),
                    "class_name": "FromSpecStaticRail",
                    "source_marker": str(fixture.rail_path),
                },
            }
        ],
        "prompt_sections": [
            {
                "name": _IDENTITY_SECTION_NAME,
                "content": {"en": identity_text},
                "priority": 10,
            },
            {
                "name": _SOUL_SECTION_NAME,
                "content": {"en": soul_text},
                "priority": 20,
            },
        ],
        "skills": [
            {
                "dir": "skills",
                "enabled_skills": ["from_spec_skill"],
            }
        ],
    }
    (package / "expert_harness.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return package, fixture, identity_text, soul_text


@dataclass(frozen=True)
class _MiniSidecarPackage:
    """In-test auto_harness-shaped office package (resources.type=package)."""

    package: Path
    tool_id: str
    tool_name: str
    tool_path: Path
    rail_path: Path


def _write_yaml(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _write_empty_init(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _extension_module(package_name: str, *parts: str) -> str:
    return ".".join(("openjiuwen.extensions.harness", package_name, *parts))


def _write_mini_sidecar_tool_file(path: Path, *, tool_id: str, tool_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "from typing import Any, AsyncIterator",
                "from openjiuwen.core.foundation.tool import Tool, ToolCard",
                "",
                f"class {_MINI_SIDECAR_TOOL_CLASS}(Tool):",
                "    def __init__(self) -> None:",
                (
                    "        super().__init__(ToolCard("
                    f"id={tool_id!r}, name={tool_name!r}, "
                    "description='mini AH-shaped hot-load tool'))"
                ),
                "",
                "    async def invoke(",
                "        self,",
                "        inputs: dict[str, Any],",
                "        **kwargs: object,",
                "    ) -> dict[str, Any]:",
                "        return inputs",
                "",
                "    async def stream(",
                "        self,",
                "        inputs: dict[str, Any],",
                "        **kwargs: object,",
                "    ) -> AsyncIterator[dict[str, Any]]:",
                "        if False:",
                "            yield inputs",
            ]
        ),
        encoding="utf-8",
    )


def _write_mini_sidecar_filename_guard_rail(path: Path) -> None:
    """AH-shaped FilenameGuardRail stub: strict mode blocks bash/*.exe paths."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "from typing import Any, Optional, Set",
                "from openjiuwen.core.runner.callback import AbortError",
                "from openjiuwen.core.single_agent.rail.base import AgentCallbackContext",
                "from openjiuwen.harness.rails.base import DeepAgentRail",
                "",
                f"class {_MINI_SIDECAR_RAIL_CLASS}(DeepAgentRail):",
                "    priority = 85",
                "    FILE_OPERATION_TOOLS = {'write_file', 'edit_file', 'read_file', 'bash', 'powershell'}",
                "    BLOCKED_EXTENSIONS = {'.exe', '.bat', '.cmd', '.ps1', '.sh'}",
                "",
                "    def __init__(",
                "        self,",
                "        blocked_extensions: Optional[Set[str]] = None,",
                "        allowed_paths: Optional[Set[str]] = None,",
                "        strict_mode: bool = False,",
                "    ) -> None:",
                "        super().__init__()",
                "        self._blocked_extensions = set(self.BLOCKED_EXTENSIONS)",
                "        if blocked_extensions:",
                "            self._blocked_extensions.update(blocked_extensions)",
                "        self._allowed_paths = allowed_paths or set()",
                "        self._strict_mode = strict_mode",
                "",
                "    def init(self, agent) -> None:",
                "        if not agent.deep_config:",
                "            return",
                "        if not self.sys_operation:",
                "            self.set_sys_operation(agent.deep_config.sys_operation)",
                "        if not self.workspace:",
                "            self.set_workspace(agent.deep_config.workspace)",
                "",
                "    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:",
                "        if ctx.inputs is None:",
                "            return",
                "        tool_name = ctx.inputs.tool_name",
                "        if tool_name not in self.FILE_OPERATION_TOOLS:",
                "            return",
                "        tool_args = ctx.inputs.tool_args",
                "        if not isinstance(tool_args, dict):",
                "            return",
                "        paths: list[str] = []",
                "        for key in ('file_path', 'path', 'output_path'):",
                "            if key in tool_args:",
                "                paths.append(str(tool_args[key]))",
                "        if tool_name in {'bash', 'powershell'}:",
                "            command = tool_args.get('command', tool_args.get('cmd', ''))",
                "            if command:",
                "                paths.append(str(command))",
                "        for file_path in paths:",
                "            lowered = file_path.lower()",
                "            blocked = next(",
                "                (ext for ext in self._blocked_extensions if lowered.endswith(ext) or ext in lowered),",
                "                None,",
                "            )",
                "            if blocked is None:",
                "                continue",
                "            error_msg = f'blocked file extension: {blocked}'",
                "            if self._strict_mode:",
                "                raise AbortError(",
                "                    f'Filename validation failed: {error_msg}. File path: {file_path}'",
                "                )",
            ]
        ),
        encoding="utf-8",
    )


def _write_mini_sidecar_skills(skills_root: Path) -> None:
    for skill_name, description_marker, body in _MINI_SIDECAR_SKILL_SPECS:
        skill_dir = skills_root / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "\n".join(
                [
                    "---",
                    f"name: {skill_name}",
                    f'description: "Use this skill when a {description_marker} is the primary deliverable."',
                    "---",
                    "",
                    f"# {skill_name}",
                    "",
                    body,
                ]
            ),
            encoding="utf-8",
        )


def _write_mini_sidecar_package(tmp_path: Path) -> _MiniSidecarPackage:
    """Write an auto_harness-shaped office package under tmp_path (resources.type=package)."""
    package_name = _MINI_SIDECAR_PACKAGE_NAME
    package = tmp_path / package_name
    tool_id = f"mini_sidecar_tool_{tmp_path.name}"
    tool_name = f"mini_sidecar_tool_name_{tmp_path.name}"
    tool_path = package / "tools" / f"{_MINI_SIDECAR_TOOL_MODULE_LEAF}.py"
    rail_path = package / "rails" / f"{_MINI_SIDECAR_RAIL_MODULE_LEAF}.py"

    _write_empty_init(package / "__init__.py")
    _write_empty_init(package / "tools" / "__init__.py")
    _write_empty_init(package / "rails" / "__init__.py")
    _write_mini_sidecar_tool_file(tool_path, tool_id=tool_id, tool_name=tool_name)
    _write_mini_sidecar_filename_guard_rail(rail_path)
    _write_mini_sidecar_skills(package / "skills")
    _write_yaml(
        package / "harness_config.yaml",
        {
            "schema_version": "harness_config.v0.1",
            "name": package_name,
            "resources": {
                "tools": [
                    {
                        "type": "package",
                        "module": _extension_module(
                            package_name,
                            "tools",
                            _MINI_SIDECAR_TOOL_MODULE_LEAF,
                        ),
                        "class": _MINI_SIDECAR_TOOL_CLASS,
                    }
                ],
                "rails": [
                    {
                        "type": "package",
                        "module": _extension_module(
                            package_name,
                            "rails",
                            _MINI_SIDECAR_RAIL_MODULE_LEAF,
                        ),
                        "class": _MINI_SIDECAR_RAIL_CLASS,
                    }
                ],
                "skills": {"dirs": ["skills/"]},
            },
        },
    )
    return _MiniSidecarPackage(
        package=package,
        tool_id=tool_id,
        tool_name=tool_name,
        tool_path=tool_path,
        rail_path=rail_path,
    )


def _marker_joined_markdown(title: str, markers: tuple[str, ...]) -> str:
    body = "\n".join(f"- {marker}" for marker in markers)
    return f"# {title}\n\n{body}\n"


def _write_mini_frontend_prompt_package(tmp_path: Path) -> tuple[Path, str, str, str]:
    """Write an ACH-shaped frontend prompt package (identity/soul/role sidecars)."""
    package = tmp_path / _FRONTEND_PACKAGE_NAME
    package.mkdir(parents=True, exist_ok=True)
    identity_text = _marker_joined_markdown("Identity", _FRONTEND_IDENTITY_MARKERS)
    soul_text = _marker_joined_markdown("Soul", _FRONTEND_SOUL_MARKERS)
    role_text = _marker_joined_markdown("Role Playbook", _FRONTEND_ROLE_MARKERS)
    (package / "identity.md").write_text(identity_text, encoding="utf-8")
    (package / "soul.md").write_text(soul_text, encoding="utf-8")
    role_path = package / "prompt_sections" / "files" / "role.md"
    role_path.parent.mkdir(parents=True, exist_ok=True)
    role_path.write_text(role_text, encoding="utf-8")
    _write_yaml(
        package / "prompt_sections" / "sections.yaml",
        {
            "sections": [
                {
                    "name": _ROLE_PLAYBOOK_SECTION_NAME,
                    "file": "role.md",
                    "priority": 30,
                }
            ]
        },
    )
    _write_yaml(
        package / "harness_config.yaml",
        {
            "schema_version": "harness_config.v0.1",
            "name": _FRONTEND_PACKAGE_NAME,
        },
    )
    return package, identity_text, soul_text, role_text


# ---------------------------------------------------------------------------
# Spec / agent factories
# ---------------------------------------------------------------------------


def _sys_operation_spec(tmp_path: Path, *, suffix: str) -> SysOperationSpec:
    return SysOperationSpec(
        id=f"{suffix}_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )


def _make_sys_operation(tmp_path: Path) -> SysOperation:
    card = SysOperationCard(
        id=f"deep_agent_load_e2e_sysop_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(card)
    sys_operation = Runner.resource_mgr.get_sys_operation(card.id)
    if sys_operation is None:
        raise RuntimeError(f"Failed to register SysOperation: {card.id}")
    return sys_operation


async def _create_initialized_agent(
    tmp_path: Path,
    *,
    language: str = "en",
    auto_create_workspace: bool = False,
    workspace_subdir: str = "workspace",
    **config_overrides: Any,
) -> DeepAgent:
    sys_operation = _make_sys_operation(tmp_path)
    config = DeepAgentConfig(
        workspace=Workspace(root_path=str(tmp_path / workspace_subdir)),
        sys_operation=sys_operation,
        language=language,
        auto_create_workspace=auto_create_workspace,
        **config_overrides,
    )
    agent = DeepAgent(config.card or AgentCard(name="DeepAgent"))
    agent.configure(config)
    await agent._ensure_initialized()
    return agent


async def _hot_load_harness_config(agent: DeepAgent, package: Path) -> None:
    agent.enqueue_harness_config(str(package / "harness_config.yaml"))
    await agent._drain_pending_harness_configs()


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def _resolve_tool(agent: DeepAgent, tool_id: str) -> Any:
    tool = Runner.resource_mgr.get_tool(tool_id=tool_id, tag=agent.card.id)
    if tool is None:
        tool = Runner.resource_mgr.get_tool(tool_id=tool_id)
    return tool


def _find_rails(agent: DeepAgent, class_name: str) -> list[Any]:
    return [rail for rail in agent._registered_rails if type(rail).__name__ == class_name]


def _find_pending_rails(agent: DeepAgent, class_name: str) -> list[Any]:
    return [rail for rail in agent._pending_rails if type(rail).__name__ == class_name]


def _registered_skill_rails(agent: DeepAgent) -> list[SkillUseRail]:
    return [rail for rail in (*agent._registered_rails, *agent._pending_rails) if isinstance(rail, SkillUseRail)]


def _filename_guard_rail(agent: DeepAgent) -> AgentRail:
    guard_rails = _find_rails(agent, "FilenameGuardRail")
    if len(guard_rails) != 1:
        raise AssertionError(f"expected exactly one FilenameGuardRail, got {len(guard_rails)}")
    return guard_rails[0]


async def _instrument_filename_guard_before_tool_call(
    agent: DeepAgent,
    guard: AgentRail,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Rebind FilenameGuardRail.before_tool_call so invoke-path calls are observable.

    Callbacks are snapshotted at ``register_rail`` time, so the instance method must be
    wrapped and the rail re-registered. Returns ``(calls, abort_reasons)``.

    Office FilenameGuard only inspects dict ``tool_args``; the invoke path may pass a
    JSON string, so this wrapper normalizes args before delegating.
    """
    from openjiuwen.core.runner.callback import AbortError

    await agent.unregister_rail(guard)
    original = guard.before_tool_call
    calls: list[dict[str, Any]] = []
    abort_reasons: list[str] = []

    async def tracked_before_tool_call(ctx: AgentCallbackContext) -> None:
        if isinstance(ctx.inputs, ToolCallInputs) and ctx.inputs.tool_name:
            if isinstance(ctx.inputs.tool_args, str):
                parsed = _parse_tool_args(ctx.inputs.tool_args)
                if parsed is not None:
                    ctx.inputs.tool_args = parsed
            calls.append(
                {
                    "tool_name": ctx.inputs.tool_name,
                    "tool_args": ctx.inputs.tool_args,
                }
            )
        try:
            await original(ctx)
        except AbortError as exc:
            abort_reasons.append(str(exc))
            # Convert hard abort into skip + finish so the fake-model loop can stop.
            ctx.extra["_skip_tool"] = True
            if not ctx.has_force_finish_request:
                ctx.request_force_finish(
                    {
                        "result_type": "answer",
                        "output": "FilenameGuardRail blocked tool call",
                    }
                )

    guard.before_tool_call = tracked_before_tool_call  # type: ignore[method-assign]
    await agent.register_rail(guard)
    return calls, abort_reasons


def _assert_filename_guard_used_on_bash_exe(
    *,
    calls: list[dict[str, Any]],
    abort_reasons: list[str],
) -> None:
    """Strict proof: hot-loaded FilenameGuardRail ran and blocked bash/*.exe."""
    bash_calls = [call for call in calls if call.get("tool_name") == "bash"]
    if not bash_calls:
        raise AssertionError(
            f"FilenameGuardRail.before_tool_call was not invoked for bash; calls={calls!r}"
        )
    if not any(".exe" in str(call.get("tool_args", "")) for call in bash_calls):
        raise AssertionError(
            f"FilenameGuardRail bash call missing .exe payload; calls={bash_calls!r}"
        )
    if not abort_reasons:
        raise AssertionError(
            "FilenameGuardRail did not AbortError on bash/*.exe in strict mode; "
            f"calls={bash_calls!r}"
        )
    if not any(
        "blocked file extension" in reason.lower() or ".exe" in reason.lower()
        for reason in abort_reasons
    ):
        raise AssertionError(
            f"FilenameGuardRail abort reason did not mention blocked .exe; reasons={abort_reasons!r}"
        )


def _hot_load_record(agent: DeepAgent, package: Path):
    records = [
        record
        for record in agent._load_records.values()
        if record.source_uri and str(package.resolve()) in str(record.source_uri)
    ]
    if not records:
        records = list(agent._load_records.values())
    if len(records) != 1:
        raise AssertionError(f"expected one hot load record, got {len(records)}")
    return records[0]


def _assert_tool_registered_and_resolvable(agent: DeepAgent, *, tool_name: str) -> None:
    card = agent.ability_manager.get(tool_name)
    if card is None:
        raise AssertionError(f"tool card not registered: {tool_name}")
    qualified_id = AbilityManager.qualify_tool_id(card, agent.card.id)
    assert card.id == qualified_id
    if _resolve_tool(agent, qualified_id) is None:
        raise AssertionError(f"tool instance missing in resource_mgr: {tool_name} ({qualified_id})")


def _assert_from_spec_rail_result(result: dict[str, Any], *, sentinel: str) -> None:
    assert result == {
        "result_type": "answer",
        "output": _FROM_SPEC_RAIL_ANSWER,
        "tool_result": {"sentinel": sentinel},
    }


def _assert_static_rail_tool_trace(rail: Any, *, tool_name: str, sentinel: str) -> None:
    expected = {"name": tool_name, "args": {"sentinel": sentinel}}
    assert rail.before_tool_calls == [expected]
    assert rail.after_tool_calls == [{**expected, "result": {"sentinel": sentinel}}]


def _assert_from_spec_static_rail_bound(agent: DeepAgent, *, rail_path: Path) -> Any:
    """Static FromSpecStaticRail is registered (not pending) with source_marker wired."""
    registered = _find_rails(agent, "FromSpecStaticRail")
    assert len(registered) == 1
    assert not _find_pending_rails(agent, "FromSpecStaticRail")
    rail = registered[0]
    assert getattr(rail, "source_marker", None) == str(rail_path)
    assert AgentCallbackEvent.BEFORE_TOOL_CALL in rail.get_callbacks()
    assert AgentCallbackEvent.AFTER_TOOL_CALL in rail.get_callbacks()
    react = agent.react_agent
    assert react is not None
    assert react.agent_callback_manager.has_hooks(AgentCallbackEvent.BEFORE_TOOL_CALL)
    assert react.agent_callback_manager.has_hooks(AgentCallbackEvent.AFTER_TOOL_CALL)
    return rail


def _static_tool_file_spec(fixture: _ToolRailFixture) -> BuiltinToolSpec:
    """Cold Spec BuiltinToolSpec for the shared FromSpecStaticTool fixture file."""
    return BuiltinToolSpec(
        type="harness.tool.file",
        params={
            "file_path": str(fixture.tool_path),
            "class_name": "FromSpecStaticTool",
        },
    )


def _static_rail_file_spec(fixture: _ToolRailFixture) -> ColdRailSpec:
    """Cold Spec RailSpec for the shared FromSpecStaticRail fixture file."""
    return ColdRailSpec(
        type="harness.rail.file",
        params={
            "file_path": str(fixture.rail_path),
            "class_name": "FromSpecStaticRail",
            "source_marker": str(fixture.rail_path),
        },
    )


async def _invoke_and_assert_static_tool_rail_used(
    agent: DeepAgent,
    *,
    fixture: _ToolRailFixture,
    static_rail: Any,
    sentinel: str,
    conversation_id: str,
    query: str,
) -> _DeterministicToolCallModel:
    """Drive one deterministic tool call and assert tool+rail both fired.

    Returns the fake model so callers can inspect prompt / tool exposure.
    """
    fake_model = _DeterministicToolCallModel(
        tool_name=fixture.tool_name,
        tool_args={"sentinel": sentinel},
    )
    agent.react_agent.set_llm(fake_model)
    result = await agent.invoke({"query": query, "conversation_id": conversation_id})
    _assert_from_spec_rail_result(result, sentinel=sentinel)
    assert fake_model.call_count == 1
    assert fixture.tool_name in fake_model.last_tool_names()
    _assert_static_rail_tool_trace(
        static_rail,
        tool_name=fixture.tool_name,
        sentinel=sentinel,
    )
    return fake_model


def _clear_default_identity_section(agent: DeepAgent) -> None:
    if agent.system_prompt_builder is not None:
        agent.system_prompt_builder.remove_section(SectionName.IDENTITY)


def _assert_prompt_section_matches_file(
    agent: DeepAgent,
    *,
    section_name: str,
    file_text: str,
    markers: tuple[str, ...],
) -> None:
    section = agent.system_prompt_builder.get_section(section_name)
    if section is None:
        raise AssertionError(f"prompt section not bound: {section_name}")
    rendered = section.render("en")
    if rendered.strip() != file_text.strip():
        raise AssertionError(f"prompt section '{section_name}' content diverged from package file")
    built_prompt = agent.system_prompt_builder.build()
    for marker in markers:
        if marker not in rendered:
            raise AssertionError(f"prompt section '{section_name}' missing marker: {marker}")
        if marker not in built_prompt:
            raise AssertionError(f"system prompt missing marker from '{section_name}': {marker}")


def _assert_frontend_developer_prompt_sections(
    agent: DeepAgent,
    *,
    identity_text: str,
    soul_text: str,
    role_text: str,
) -> None:
    _assert_prompt_section_matches_file(
        agent,
        section_name=_IDENTITY_SECTION_NAME,
        file_text=identity_text,
        markers=_FRONTEND_IDENTITY_MARKERS,
    )
    _assert_prompt_section_matches_file(
        agent,
        section_name=_SOUL_SECTION_NAME,
        file_text=soul_text,
        markers=_FRONTEND_SOUL_MARKERS,
    )
    _assert_prompt_section_matches_file(
        agent,
        section_name=_ROLE_PLAYBOOK_SECTION_NAME,
        file_text=role_text,
        markers=_FRONTEND_ROLE_MARKERS,
    )


def _assert_filename_guard_rail_wired(agent: DeepAgent) -> None:
    guard = _filename_guard_rail(agent)
    if guard.sys_operation is None:
        raise AssertionError("FilenameGuardRail.sys_operation was not wired")
    if guard.workspace is None:
        raise AssertionError("FilenameGuardRail.workspace was not wired")
    if AgentCallbackEvent.BEFORE_TOOL_CALL not in guard.get_callbacks():
        raise AssertionError("FilenameGuardRail missing before_tool_call callback")


def _assert_mini_skills_loaded(agent: DeepAgent) -> None:
    skill_rails = _registered_skill_rails(agent)
    if len(skill_rails) != 1:
        raise AssertionError(f"expected one SkillUseRail, got {len(skill_rails)}")
    skill_by_name = {skill.name: skill for skill in skill_rails[0].skills}
    if set(skill_by_name) != _EXPECTED_SKILL_NAMES:
        raise AssertionError(
            f"unexpected skill names: {sorted(skill_by_name)} (expected {sorted(_EXPECTED_SKILL_NAMES)})"
        )
    for skill_name, marker in _SKILL_DESCRIPTION_MARKERS.items():
        description = skill_by_name[skill_name].description
        if not description or description.startswith("Skill located in "):
            raise AssertionError(f"skill '{skill_name}' description was not loaded from SKILL.md")
        if marker.lower() not in description.lower():
            raise AssertionError(f"skill '{skill_name}' description missing marker '{marker}'")


def _assert_hot_loaded_mini_harness(agent: DeepAgent, mini: _MiniSidecarPackage) -> None:
    record = _hot_load_record(agent, mini.package)
    kinds = [ref.kind for ref in record.refs]
    if kinds.count(ResourceKind.TOOL) != 1:
        raise AssertionError("expected one hot-loaded tool")
    if kinds.count(ResourceKind.RAIL) != 1:
        raise AssertionError("expected one hot-loaded rail")
    if kinds.count(ResourceKind.SKILL) != 1:
        raise AssertionError("expected one hot-loaded skill mount")
    if agent._pending_harness_configs:
        raise AssertionError("pending harness configs were not drained")
    _assert_tool_registered_and_resolvable(agent, tool_name=mini.tool_name)
    _assert_filename_guard_rail_wired(agent)
    _assert_mini_skills_loaded(agent)


# ---------------------------------------------------------------------------
# TestDeepAgentSpecBuild — cold DeepAgentSpec.build() custom tool + rail
# ---------------------------------------------------------------------------


class TestDeepAgentSpecBuild:
    """Cold ``DeepAgentSpec.build()`` smoke: custom tool + rail mount and run on invoke.

    Workspace / sys_operation are scaffolding for agent init only. ask_user /
    SysOperationRail / leaf providers live in unit tests; hot load lives below.
    """

    @pytest.mark.asyncio
    async def test_cold_build_binds_and_runs_custom_tool_and_rail(self, tmp_path: Path) -> None:
        """Flat Spec.build + ensure_initialized mounts custom tool/rail; invoke uses both."""
        workspace_path = tmp_path / "workspace"
        fixture = _setup_tool_rail_fixture(tmp_path, prefix="cold_build_static", with_skill=False)
        sentinel = "cold_build_tool_call"

        agent = DeepAgentSpec(
            card=AgentCard(name="cold_build_tool_rail", description="cold build tool+rail smoke"),
            workspace=WorkspaceSpec(root_path=str(workspace_path), language="en"),
            sys_operation=_sys_operation_spec(tmp_path, suffix="cold_build_tool_rail"),
            language="en",
            auto_create_workspace=True,
            enable_task_loop=False,
            max_iterations=3,
            completion_timeout=12.0,
            tools=[_static_tool_file_spec(fixture)],
            rails=[_static_rail_file_spec(fixture)],
        ).build()
        await agent.ensure_initialized()

        _assert_tool_registered_and_resolvable(agent, tool_name=fixture.tool_name)
        static_rail = _assert_from_spec_static_rail_bound(agent, rail_path=fixture.rail_path)

        await _invoke_and_assert_static_tool_rail_used(
            agent,
            fixture=fixture,
            static_rail=static_rail,
            sentinel=sentinel,
            conversation_id=f"cold_build_static_{tmp_path.name}",
            query="Call the cold-build static tool once.",
        )
        assert workspace_path.is_dir()


# ---------------------------------------------------------------------------
# TestHotLoadExpertHarness — hot load via load_expert_harness
# ---------------------------------------------------------------------------


class TestHotLoadExpertHarness:
    """Hot-load ExpertHarness packages through ``DeepAgent.load_expert_harness``."""

    @pytest.mark.asyncio
    async def test_load_expert_harness_binds_and_runs_tool_rail_skill_and_prompt(
        self,
        tmp_path: Path,
    ) -> None:
        """On-disk package: tool/rail/skill/prompt bind; invoke runs tool+rail and surfaces prompt."""
        workspace_path = tmp_path / "workspace"
        package, fixture, identity_text, soul_text = _write_hot_load_package(
            tmp_path,
            package_id="hot_load_pack",
            prefix="hot_load",
        )
        agent = await _create_initialized_agent(
            tmp_path,
            language="en",
            auto_create_workspace=True,
            max_iterations=3,
            completion_timeout=12.0,
            workspace_subdir=str(workspace_path.relative_to(tmp_path)),
        )
        _clear_default_identity_section(agent)

        record = await agent.load_expert_harness(str(package))

        assert record.source_uri == str((package / "expert_harness.yaml").resolve())
        kinds = {ref.kind for ref in record.refs}
        assert ResourceKind.TOOL in kinds
        assert ResourceKind.RAIL in kinds
        assert ResourceKind.SKILL in kinds
        assert ResourceKind.PROMPT_SECTION in kinds
        _assert_tool_registered_and_resolvable(agent, tool_name=fixture.tool_name)
        static_rail = _assert_from_spec_static_rail_bound(agent, rail_path=fixture.rail_path)
        assert agent.ability_manager.get("skill_tool") is not None
        _assert_prompt_section_matches_file(
            agent,
            section_name=_IDENTITY_SECTION_NAME,
            file_text=identity_text,
            markers=(_HOT_LOAD_IDENTITY_MARKER,),
        )
        _assert_prompt_section_matches_file(
            agent,
            section_name=_SOUL_SECTION_NAME,
            file_text=soul_text,
            markers=(_HOT_LOAD_SOUL_MARKER,),
        )

        tool_model = await _invoke_and_assert_static_tool_rail_used(
            agent,
            fixture=fixture,
            static_rail=static_rail,
            sentinel="hot_load_tool_call",
            conversation_id=f"hot_load_tool_{tmp_path.name}",
            query="Call the hot-loaded package tool once.",
        )
        final_system_prompt = tool_model.last_system_prompt()
        assert _HOT_LOAD_IDENTITY_MARKER in final_system_prompt
        assert _HOT_LOAD_SOUL_MARKER in final_system_prompt

        skill_model = _DeterministicToolCallModel(
            tool_name="skill_tool",
            tool_args={"skill_name": "from_spec_skill"},
        )
        agent.react_agent.set_llm(skill_model)
        skill_result = await agent.invoke(
            {
                "query": "Call skill_tool once for from_spec_skill.",
                "conversation_id": f"hot_load_skill_{tmp_path.name}",
            }
        )
        assert isinstance(skill_result, dict)
        assert skill_result.get("result_type") == "answer"
        assert _FROM_SPEC_SKILL_BODY_MARKER in str(skill_result.get("tool_result"))

    @pytest.mark.asyncio
    async def test_load_expert_harness_binds_frontend_prompt_sections(
        self,
        tmp_path: Path,
    ) -> None:
        """Mini ACH-shaped frontend package: load_expert_harness binds identity/soul/role prompts."""
        package, identity_text, soul_text, role_text = _write_mini_frontend_prompt_package(tmp_path)
        agent = await _create_initialized_agent(tmp_path, language="en", auto_create_workspace=False)
        _clear_default_identity_section(agent)

        record = await agent.load_expert_harness(str(package))

        assert record.source_uri == str((package / "harness_config.yaml").resolve())
        assert [ref.kind for ref in record.refs] == [ResourceKind.PROMPT_SECTION] * 3
        _assert_frontend_developer_prompt_sections(
            agent,
            identity_text=identity_text,
            soul_text=soul_text,
            role_text=role_text,
        )

    @pytest.mark.asyncio
    async def test_office_package_binds_and_runs_tool_rail_skill(
        self,
        tmp_path: Path,
    ) -> None:
        """Mini AH-shaped package: tool/rail/skill mount and all three are used on invoke."""
        mini = _write_mini_sidecar_package(tmp_path)
        finish_rail = _ForceFinishAfterNamedToolRail(mini.tool_name)
        agent = await _create_initialized_agent(
            tmp_path,
            language="en",
            auto_create_workspace=True,
            max_iterations=3,
            completion_timeout=12.0,
            rails=[finish_rail],
        )

        record = await agent.load_expert_harness(str(mini.package))

        assert record.source_uri == str((mini.package / "harness_config.yaml").resolve())
        kinds = [ref.kind for ref in record.refs]
        assert kinds.count(ResourceKind.TOOL) == 1
        assert kinds.count(ResourceKind.RAIL) == 1
        assert kinds.count(ResourceKind.SKILL) == 1

        _assert_tool_registered_and_resolvable(agent, tool_name=mini.tool_name)
        guard = _filename_guard_rail(agent)
        _assert_filename_guard_rail_wired(agent)
        assert AgentCallbackEvent.BEFORE_TOOL_CALL in guard.get_callbacks()
        assert agent.ability_manager.get("bash") is not None, (
            "expected SkillUseRail to register bash after mini sidecar skill hot-load"
        )
        assert agent.ability_manager.get("skill_tool") is not None
        skill_rails = _registered_skill_rails(agent)
        assert len(skill_rails) == 1
        skill_by_name = {skill.name: skill for skill in skill_rails[0].skills}
        assert set(skill_by_name) == _EXPECTED_SKILL_NAMES
        assert _MINI_SIDECAR_SKILL_NAME in skill_by_name
        skill_description = skill_by_name[_MINI_SIDECAR_SKILL_NAME].description or ""
        assert _MINI_SIDECAR_SKILL_DESCRIPTION_MARKER in skill_description.lower()

        # Tool mounted and used.
        finish_rail.tool_name = mini.tool_name
        finish_rail.tool_results.clear()
        tool_model = _DeterministicToolCallModel(
            tool_name=mini.tool_name,
            tool_args={"sentinel": _MINI_SIDECAR_TOOL_SENTINEL},
        )
        agent.react_agent.set_llm(tool_model)
        tool_result = await agent.invoke(
            {
                "query": "Call the mini sidecar tool once.",
                "conversation_id": f"hot_mini_tool_{tmp_path.name}",
            }
        )
        assert isinstance(tool_result, dict)
        assert tool_result.get("result_type") == "answer"
        assert finish_rail.tool_results, "mini sidecar tool did not run on invoke"
        assert finish_rail.tool_results[0] == {"sentinel": _MINI_SIDECAR_TOOL_SENTINEL}
        assert tool_model.call_count == 1
        assert mini.tool_name in tool_model.last_tool_names()

        # Rail mounted and used (blocks bash writing *.exe).
        guard._strict_mode = True
        guard_calls, abort_reasons = await _instrument_filename_guard_before_tool_call(agent, guard)
        agent.react_agent.set_llm(
            _DeterministicToolCallModel(
                tool_name="bash",
                tool_args={"command": "echo blocked > payload.exe"},
            )
        )
        await agent.invoke(
            {
                "query": "Write payload.exe via bash once.",
                "conversation_id": f"hot_mini_rail_{tmp_path.name}",
            }
        )
        _assert_filename_guard_used_on_bash_exe(calls=guard_calls, abort_reasons=abort_reasons)
        assert not (tmp_path / "payload.exe").exists()
        assert not (tmp_path / "workspace" / "payload.exe").exists()

        # Skill mounted and used.
        finish_rail.tool_name = "skill_tool"
        finish_rail.tool_results.clear()
        agent.react_agent.set_llm(
            _DeterministicToolCallModel(
                tool_name="skill_tool",
                tool_args={"skill_name": _MINI_SIDECAR_SKILL_NAME},
            )
        )
        skill_result = await agent.invoke(
            {
                "query": f"Call skill_tool once for the {_MINI_SIDECAR_SKILL_NAME} skill.",
                "conversation_id": f"hot_mini_skill_{tmp_path.name}",
            }
        )
        assert isinstance(skill_result, dict)
        assert skill_result.get("result_type") == "answer"
        assert finish_rail.tool_results, "mini sidecar skill_tool did not run on invoke"
        tool_result_text = str(finish_rail.tool_results[0]).lower()
        assert (
            _MINI_SIDECAR_SKILL_DESCRIPTION_MARKER in tool_result_text
            or _MINI_SIDECAR_SKILL_NAME in tool_result_text
            or _MINI_SIDECAR_SKILL_BODY_MARKER.lower() in tool_result_text
        )


# ---------------------------------------------------------------------------
# TestRunnerHotLoadSmoke — Runner.run_agent + enqueue harness_config
# ---------------------------------------------------------------------------


class TestRunnerHotLoadSmoke:
    """Runner.run_agent smoke with enqueue/drain of an in-test AH-shaped mini package."""

    @pytest.mark.asyncio
    async def test_runner_uses_hot_loaded_mini_harness_tool(
        self,
        tmp_path: Path,
    ) -> None:
        """Enqueue mini AH package, then Runner.run_agent invokes the hot-loaded tool."""
        mini = _write_mini_sidecar_package(tmp_path)
        workspace_path = tmp_path / "workspace"
        sys_operation = _make_sys_operation(tmp_path)
        finish_rail = _ForceFinishAfterNamedToolRail(mini.tool_name)
        config = DeepAgentConfig(
            card=AgentCard(
                id="deep_agent_load_runner_e2e",
                name="deep_agent_load_runner_e2e",
            ),
            workspace=Workspace(root_path=str(workspace_path)),
            sys_operation=sys_operation,
            rails=[finish_rail],
            language="en",
            auto_create_workspace=True,
            enable_task_loop=False,
            max_iterations=3,
            completion_timeout=12.0,
        )
        agent = DeepAgent(config.card)
        agent.configure(config)
        await Runner.start()
        try:
            await agent._ensure_initialized()
            await _hot_load_harness_config(agent, mini.package)
            _assert_hot_loaded_mini_harness(agent, mini)

            agent.react_agent.set_llm(
                _DeterministicToolCallModel(
                    tool_name=mini.tool_name,
                    tool_args={"sentinel": _MINI_SIDECAR_TOOL_SENTINEL},
                )
            )
            result = await Runner.run_agent(
                agent,
                {
                    "query": "Call the mini AH-shaped tool once.",
                    "conversation_id": f"runner_hot_mini_{tmp_path.name}",
                },
                session=f"deep_agent_load_runner_e2e_{tmp_path.name}",
            )

            assert isinstance(result, dict)
            assert result.get("result_type") == "answer"
            assert finish_rail.tool_results, "mini tool did not run via Runner.run_agent"
            assert finish_rail.tool_results[0] == {"sentinel": _MINI_SIDECAR_TOOL_SENTINEL}
            _assert_hot_loaded_mini_harness(agent, mini)
        finally:
            await Runner.stop()
