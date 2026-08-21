# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeepAgent runtime Plugin/AgentTemplate hot-load E2E tests.

Test matrix
-----------
| Class | Scenario | External pkg / live model |
|-------|----------|---------------------------|
| TestDeepAgentSpecBuild | Cold ``DeepAgentSpec.build()``: custom tool+rail mount and invoke | No |
| TestExtensionLoadE2E | legacy YAML Plugin, new Plugin manifest, AgentTemplate, failure rollback | No |
| TestRunnerHotLoadSmoke | ``Runner.run_agent`` + enqueue legacy AH ``harness_config`` hot-load | No |
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import pytest
import yaml
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.resources_manager.base import Ok
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
from openjiuwen.harness.resources.extension_resolver import ResolvedSkill, ResourceKind
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

_EXPECTED_SKILL_NAMES = frozenset({"docx", "pptx", "xlsx"})
_SKILL_DESCRIPTION_MARKERS = {
    "docx": "Word documents",
    "pptx": "presentation",
    "xlsx": "spreadsheet file",
}

_FROM_SPEC_RAIL_ANSWER = "from_spec rail observed tool result"
_FROM_SPEC_SKILL_BODY_MARKER = "Use this skill only to prove static spec skill loading."
_FROM_SPEC_SKILL_DESCRIPTION_MARKER = "DeepAgentSpec static from-spec fixture"
_MINI_SIDECAR_TOOL_CLASS = "MiniSidecarTool"
_MINI_SIDECAR_RAIL_CLASS = "FilenameGuardRail"
_MINI_SIDECAR_SKILL_NAME = "xlsx"
_MINI_SIDECAR_SKILL_DESCRIPTION_MARKER = "spreadsheet file"
_MINI_SIDECAR_SKILL_BODY_MARKER = "Mini sidecar xlsx skill body for hot-load invoke."
_MINI_SIDECAR_SKILL_SPECS = (
    ("docx", "Word documents", "Mini AH-shaped docx skill body."),
    ("pptx", "presentation", "Mini AH-shaped pptx skill body."),
    ("xlsx", _MINI_SIDECAR_SKILL_DESCRIPTION_MARKER, _MINI_SIDECAR_SKILL_BODY_MARKER),
)
_FROM_SPEC_TOOL_CLASS = "FromSpecStaticTool"
_RUNNER_HOT_TOOL_SENTINEL = "runner_hot_tool_call"

# ---------------------------------------------------------------------------
# E2E 1-4 (TestExtensionLoadE2E) constants
# ---------------------------------------------------------------------------

_E2E1_IDENTITY_MARKER = "e2e1-legacy-yaml-identity-marker"
_E2E1_SOUL_MARKER = "e2e1-legacy-yaml-soul-marker"
_E2E1_PACKAGE_NAME = "e2e1_legacy_plugin_mini"

_E2E2_PLUGIN_PROMPT_SECTION_NAME = "e2e2_plugin_capability_brief"
_E2E2_PROMPT_MARKER = "e2e2-new-plugin-manifest-prompt-marker"

_E2E3_ROOT_IDENTITY_MARKER = "e2e3-agent-template-root-identity-marker"
_E2E3_CHILD_AGENT_NAME = "e2e3_child_specialist"
_E2E3_HOST_MODEL_NAME = "e2e3-host-model"
_E2E3_CHILD_MODEL_NAME = "e2e3-child-model"

_E2E4_ROOT_IDENTITY_MARKER = "e2e4-agent-template-root-identity-marker"


def _mcp_mock_entry(*, server_name: str) -> dict[str, Any]:
    """Mock MCP entry (no live process). Shape works for legacy YAML and new manifests.

    Legacy ``harness_config.yaml`` ``resources.mcps`` entries are validated
    directly against ``McpServerSpec`` (canonical field names only), while new
    ``manifest.json`` ``mcps`` entries pass through
    ``_normalize_mcp_server_entry`` first (which also accepts ``name``).
    Using ``server_name``/``type`` here keeps one shape valid for both paths.
    ``add_mcp_server`` is mocked in ``_mock_mcp_resource_mgr`` so the URL is unused.
    """
    return {
        "type": "streamable_http",
        "server_name": server_name,
        "url": "http://127.0.0.1:9/mcp",
    }


def _inert_model(*, model_name: str = "test-model") -> Model:
    """A ``Model`` that is never actually invoked (only used for non-None-ness

    checks, e.g. ``resolve_agent_template_parts``'s ``BuildContext.extras
    ['_parent_model']`` requirement when an AgentTemplate declares subagents).
    """
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="openai",
            api_key="fake-key-for-e2e-test",
            api_base="http://localhost:0",
        ),
        model_config=ModelRequestConfig(model=model_name),
    )


def _write_model_json(path: Path, *, model_name: str = "test-model") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "model": {
                    "model_client_config": {
                        "client_provider": "openai",
                        "api_key": "fake-key-for-e2e-test",
                        "api_base": "http://localhost:0",
                    },
                    "model_request_config": {"model": model_name},
                }
            }
        ),
        encoding="utf-8",
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


@pytest.fixture(autouse=True)
def _mock_mcp_resource_mgr(_isolated_runner: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip live MCP connect/list_tools; keep Extension bind/unbind plumbing."""

    async def _add_mcp_server(
        server_config: McpServerConfig | list[McpServerConfig],
        **_kwargs: Any,
    ) -> Any:
        configs = [server_config] if isinstance(server_config, McpServerConfig) else server_config
        results = [Ok(cfg.server_id) for cfg in configs]
        return results if isinstance(server_config, list) else results[0]

    monkeypatch.setattr(Runner.resource_mgr, "add_mcp_server", _add_mcp_server)


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


class _CaptureOnlyModel:
    """Fake model that answers once without tool calls; records visible tools/prompt."""

    model_config = None

    def __init__(self) -> None:
        self.call_history: list[dict[str, Any]] = []
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
        return AssistantMessage(content="ok", finish_reason="stop")

    async def stream(self, *args: object, **kwargs: object) -> None:
        _ = args, kwargs
        raise AssertionError("capture-only fake model should not use stream()")

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


def _write_stub_tool_file(
    path: Path,
    *,
    tool_id: str,
    tool_name: str,
    class_name: str,
    description: str = "stub tool",
    relative_helper_module: str | None = None,
) -> None:
    """Write a deterministic stub Tool used by cold-build and hot-load packages.

    When ``relative_helper_module`` is set (e.g. ``\"e2e1_helper\"``), the tool
    imports ``TOOL_DESCRIPTION`` from that sibling module so legacy
    package-relative imports are exercised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "from __future__ import annotations",
        "from typing import Any, AsyncIterator",
        "from openjiuwen.core.foundation.tool import Tool, ToolCard",
    ]
    if relative_helper_module:
        lines.append(f"from .{relative_helper_module} import TOOL_DESCRIPTION")
        description_expr = "TOOL_DESCRIPTION"
    else:
        description_expr = repr(description)
    lines.extend(
        [
            "",
            f"class {class_name}(Tool):",
            "    def __init__(self) -> None:",
            (
                "        super().__init__(ToolCard("
                f"id={tool_id!r}, name={tool_name!r}, "
                f"description={description_expr}))"
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
    )
    path.write_text("\n".join(lines), encoding="utf-8")


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
    """Write a stub skill under ``root/from_spec_skill/`` and return that leaf dir."""
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
    return skill_dir


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
    _write_stub_tool_file(
        tool_path,
        tool_id=tool_id,
        tool_name=tool_name,
        class_name=_FROM_SPEC_TOOL_CLASS,
        description="from_spec static tool",
    )
    _write_from_spec_rail_file(rail_path)
    skill_root = _write_from_spec_skill(base / "skills") if with_skill else None
    return _ToolRailFixture(
        tool_id=tool_id,
        tool_name=tool_name,
        tool_path=tool_path,
        rail_path=rail_path,
        skill_root=skill_root,
    )


def _setup_tool_rail_fixture_in_package(
    package: Path,
    *,
    prefix: str,
    with_skill: bool = False,
) -> _ToolRailFixture:
    """Like ``_setup_tool_rail_fixture``, but the tool/rail/skill files live

    inside ``package`` so new ``manifest.json`` ``file``/``dir`` entries
    (which must be package-relative and stay within the package root, see
    ``loader._resolve_new_manifest_path``) can reference them.
    """
    tool_id = f"{prefix}_tool_{package.name}"
    tool_name = f"{prefix}_tool_name_{package.name}"
    tool_path = package / "tools" / f"{prefix}_tool.py"
    rail_path = package / "rails" / f"{prefix}_rail.py"
    _write_stub_tool_file(
        tool_path,
        tool_id=tool_id,
        tool_name=tool_name,
        class_name=_FROM_SPEC_TOOL_CLASS,
        description="from_spec static tool",
    )
    _write_from_spec_rail_file(rail_path)
    skill_root = _write_from_spec_skill(package / "skills") if with_skill else None
    return _ToolRailFixture(
        tool_id=tool_id,
        tool_name=tool_name,
        tool_path=tool_path,
        rail_path=rail_path,
        skill_root=skill_root,
    )


def _package_relative(package: Path, path: Path) -> str:
    return str(path.relative_to(package))


@dataclass(frozen=True)
class _E2E1Package:
    """Legacy AH-shaped ``harness_config.yaml`` Plugin package.

    Package-relative Python import tool/rail (``resources.type=package``),
    one skill mount, ``identity``/``soul`` prompt sidecars, and a mock MCP
    entry. Shared by E2E1 and Runner hot-load smoke.
    """

    package: Path
    tool_name: str
    identity_text: str
    soul_text: str
    mcp_server_name: str


def _write_yaml(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _write_empty_init(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _extension_module(package_name: str, *parts: str) -> str:
    return ".".join(("openjiuwen.extensions.harness", package_name, *parts))


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


def _write_e2e1_helper_file(path: Path) -> None:
    """Sibling module imported by the legacy AH tool via an intra-package relative import.

    Proves that ``load_plugin`` preserves the legacy ``load_harness_config``
    contract for package-relative imports (``from .e2e1_helper import ...``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "TOOL_DESCRIPTION = 'mini AH-shaped hot-load tool'\n",
        encoding="utf-8",
    )


def _write_e2e1_legacy_yaml_package(tmp_path: Path) -> _E2E1Package:
    package_name = _E2E1_PACKAGE_NAME
    package = tmp_path / package_name
    tool_id = f"e2e1_tool_{tmp_path.name}"
    tool_name = f"e2e1_tool_name_{tmp_path.name}"
    tool_path = package / "tools" / "e2e1_tool.py"
    rail_path = package / "rails" / "e2e1_rail.py"
    mcp_server_name = f"e2e1_mcp_{tmp_path.name}"

    _write_empty_init(package / "__init__.py")
    _write_empty_init(package / "tools" / "__init__.py")
    _write_empty_init(package / "rails" / "__init__.py")
    _write_e2e1_helper_file(package / "tools" / "e2e1_helper.py")
    _write_stub_tool_file(
        tool_path,
        tool_id=tool_id,
        tool_name=tool_name,
        class_name=_MINI_SIDECAR_TOOL_CLASS,
        relative_helper_module="e2e1_helper",
    )
    _write_mini_sidecar_filename_guard_rail(rail_path)
    _write_mini_sidecar_skills(package / "skills")

    identity_text = f"# Identity\n\n{_E2E1_IDENTITY_MARKER}\n"
    soul_text = f"# Soul\n\n{_E2E1_SOUL_MARKER}\n"
    (package / "identity.md").write_text(identity_text, encoding="utf-8")
    (package / "soul.md").write_text(soul_text, encoding="utf-8")

    _write_yaml(
        package / "harness_config.yaml",
        {
            "schema_version": "harness_config.v0.1",
            "name": package_name,
            "resources": {
                "tools": [
                    {
                        "type": "package",
                        "module": _extension_module(package_name, "tools", "e2e1_tool"),
                        "class": _MINI_SIDECAR_TOOL_CLASS,
                    }
                ],
                "rails": [
                    {
                        "type": "package",
                        "module": _extension_module(package_name, "rails", "e2e1_rail"),
                        "class": _MINI_SIDECAR_RAIL_CLASS,
                    }
                ],
                "skills": {"dirs": ["skills/"]},
                "mcps": [_mcp_mock_entry(server_name=mcp_server_name)],
            },
        },
    )
    return _E2E1Package(
        package=package,
        tool_name=tool_name,
        identity_text=identity_text,
        soul_text=soul_text,
        mcp_server_name=mcp_server_name,
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@dataclass(frozen=True)
class _E2E2Package:
    """E2E 2: new ``manifest.json`` Plugin package (``package_type=plugin``).

    Shape aligned with ``test_reference/my_plugin/wellness-life-steward``
    (never loaded directly): stub tool/rail (``tools[].file`` /
    ``rails[].file``), one skill mount, a plugin-only ``prompt_sections``
    entry, and a mock MCP entry.
    """

    package: Path
    fixture: _ToolRailFixture
    mcp_server_name: str


def _write_e2e2_plugin_manifest_package(tmp_path: Path) -> _E2E2Package:
    package = tmp_path / "e2e2_new_plugin_mini"
    fixture = _setup_tool_rail_fixture_in_package(package, prefix="e2e2_plugin", with_skill=True)
    mcp_server_name = f"e2e2_mcp_{tmp_path.name}"
    _write_json(
        package / "manifest.json",
        {
            "package_type": "plugin",
            "id": f"e2e2_new_plugin_{tmp_path.name}",
            "name": "e2e2 new plugin manifest mini",
            "description": "E2E 2 mini plugin manifest package.",
            "prompt_sections": [
                {
                    "name": _E2E2_PLUGIN_PROMPT_SECTION_NAME,
                    "content": {"en": _E2E2_PROMPT_MARKER},
                    "priority": 30,
                }
            ],
            "tools": [{"file": _package_relative(package, fixture.tool_path), "class": _FROM_SPEC_TOOL_CLASS}],
            "rails": [{"file": _package_relative(package, fixture.rail_path), "class": "FromSpecStaticRail"}],
            "skills": [
                {
                    "dir": _package_relative(package, fixture.skill_root),
                    "mode": "all",
                }
            ],
            "mcps": [_mcp_mock_entry(server_name=mcp_server_name)],
        },
    )
    return _E2E2Package(package=package, fixture=fixture, mcp_server_name=mcp_server_name)


@dataclass(frozen=True)
class _E2E3Package:
    """E2E 3: new ``manifest.json`` AgentTemplate package (``package_type=agent_template``).

    Shape aligned with ``test_reference/my_expert/workplace-slim-coach``
    (never loaded directly): root persona/tool/rail/skill + one direct
    ``.subagent.json`` child (own runtime ``agent_card`` + model, no grandchildren),
    and a mock MCP entry.
    """

    package: Path
    fixture: _ToolRailFixture
    mcp_server_name: str


def _write_e2e3_agent_template_package(
    tmp_path: Path,
    *,
    identity_marker: str = _E2E3_ROOT_IDENTITY_MARKER,
    with_subagent: bool = True,
) -> _E2E3Package:
    package = tmp_path / "e2e3_agent_template_mini"
    fixture = _setup_tool_rail_fixture_in_package(package, prefix="e2e3_root", with_skill=True)
    mcp_server_name = f"e2e3_mcp_{tmp_path.name}"

    persona_dir = package / "persona"
    persona_dir.mkdir(parents=True, exist_ok=True)
    (persona_dir / "identity.md").write_text(f"# Identity\n\n{identity_marker}\n", encoding="utf-8")

    manifest: dict[str, Any] = {
        "package_type": "agent_template",
        "name": "e2e3_root_template",
        "description": "E2E 3 root agent template.",
        "persona": {"dir": "persona"},
        "tools": [{"file": _package_relative(package, fixture.tool_path), "class": _FROM_SPEC_TOOL_CLASS}],
        "rails": [{"file": _package_relative(package, fixture.rail_path), "class": "FromSpecStaticRail"}],
        "skills": [
            {
                "dir": _package_relative(package, fixture.skill_root),
                "mode": "all",
            }
        ],
        "mcps": [_mcp_mock_entry(server_name=mcp_server_name)],
    }
    if with_subagent:
        child_dir = package / "subagents" / "child"
        child_dir.mkdir(parents=True, exist_ok=True)
        child_tool_name = f"e2e3_child_tool_name_{tmp_path.name}"
        child_tool_path = child_dir / "tools" / "child_tool.py"
        _write_stub_tool_file(
            child_tool_path,
            tool_id=f"e2e3_child_tool_{tmp_path.name}",
            tool_name=child_tool_name,
            class_name=_FROM_SPEC_TOOL_CLASS,
        )
        _write_model_json(child_dir / "model.json", model_name=_E2E3_CHILD_MODEL_NAME)
        _write_json(
            child_dir / ".subagent.json",
            {
                "agent_name": _E2E3_CHILD_AGENT_NAME,
                "display_description": {"en": "E2E 3 direct child specialist subagent."},
                "model": {"file": "model.json"},
                "tools": [
                    {
                        "file": "tools/child_tool.py",
                        "class": _FROM_SPEC_TOOL_CLASS,
                    }
                ],
            },
        )
        manifest["subagents"] = [{"dir": "subagents/child"}]

    _write_json(package / "manifest.json", manifest)
    return _E2E3Package(package=package, fixture=fixture, mcp_server_name=mcp_server_name)


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
    # ``_bind_skill`` requires an existing SkillUseRail (no auto-create on hot
    # load); every caller in this module hot-loads at least one skill mount.
    config_overrides.setdefault("enable_skill_discovery", True)
    config_overrides.setdefault(
        "rails",
        [SkillUseRail(skills_dir=[], skill_mode="all", include_tools=False)],
    )
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


def _assert_from_spec_static_rail_bound(
    agent: DeepAgent,
    *,
    rail_path: Path | None = None,
) -> Any:
    """Static FromSpecStaticRail is registered (not pending).

    ``source_marker`` is only checked when ``rail_path`` is given: the cold
    ``DeepAgentSpec.build()`` / legacy ``harness.rail.file`` params path can
    set it explicitly, but new ``manifest.json`` ``rails[]`` entries only
    forward ``file``/``class`` and never construct with extra kwargs.
    """
    registered = _find_rails(agent, "FromSpecStaticRail")
    assert len(registered) == 1
    assert not _find_pending_rails(agent, "FromSpecStaticRail")
    rail = registered[0]
    if rail_path is not None:
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
            "class_name": _FROM_SPEC_TOOL_CLASS,
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


def _assert_hot_loaded_legacy_ah_package(agent: DeepAgent, pkg: _E2E1Package) -> None:
    record = _hot_load_record(agent, pkg.package)
    kinds = [ref.kind for ref in record.refs]
    if kinds.count(ResourceKind.TOOL) != 1:
        raise AssertionError("expected one hot-loaded tool")
    if kinds.count(ResourceKind.RAIL) != 1:
        raise AssertionError("expected one hot-loaded rail")
    if kinds.count(ResourceKind.SKILL) != 1:
        raise AssertionError("expected one hot-loaded skill mount")
    if kinds.count(ResourceKind.PROMPT_SECTION) != 2:
        raise AssertionError("expected identity/soul prompt sections")
    if kinds.count(ResourceKind.MCP) != 1:
        raise AssertionError("expected one hot-loaded MCP server")
    if agent._pending_harness_configs:
        raise AssertionError("pending harness configs were not drained")
    _assert_tool_registered_and_resolvable(agent, tool_name=pkg.tool_name)
    _assert_filename_guard_rail_wired(agent)
    _assert_mini_skills_loaded(agent)


def _assert_mcp_bound(agent: DeepAgent, *, server_name: str) -> None:
    """Mock MCP: present in deep_config and ability_manager after bind."""
    if not any(item.server_name == server_name for item in (agent.deep_config.mcps or [])):
        raise AssertionError(f"MCP server not in deep_config: {server_name}")
    if agent.ability_manager.get(server_name) is None:
        raise AssertionError(f"MCP ability not registered: {server_name}")


def _assert_mcp_unbound(agent: DeepAgent, *, server_name: str) -> None:
    if any(item.server_name == server_name for item in (agent.deep_config.mcps or [])):
        raise AssertionError(f"MCP server still in deep_config: {server_name}")
    if agent.ability_manager.get(server_name) is not None:
        raise AssertionError(f"MCP ability still registered after unload: {server_name}")


async def _skill_tool_result(agent: DeepAgent, *, skill_name: str, conversation_id: str) -> dict[str, Any]:
    agent.react_agent.set_llm(
        _DeterministicToolCallModel(tool_name="skill_tool", tool_args={"skill_name": skill_name})
    )
    return await agent.invoke(
        {"query": f"Call skill_tool once for {skill_name}.", "conversation_id": conversation_id}
    )


async def _invoke_probe_extension_gone(
    agent: DeepAgent,
    *,
    tool_name: str,
    conversation_id: str,
    prompt_markers: tuple[str, ...] = (),
    static_rail: Any | None = None,
) -> None:
    """Post-unload / failed-load probe: one invoke proves package tool/prompt are gone."""
    before_tool = len(getattr(static_rail, "before_tool_calls", []) or []) if static_rail is not None else 0
    after_tool = len(getattr(static_rail, "after_tool_calls", []) or []) if static_rail is not None else 0
    capture = _CaptureOnlyModel()
    agent.react_agent.set_llm(capture)
    result = await agent.invoke({"query": "Probe unbound extension.", "conversation_id": conversation_id})
    assert isinstance(result, dict)
    assert tool_name not in capture.last_tool_names()
    prompt = capture.last_system_prompt()
    for marker in prompt_markers:
        assert marker not in prompt
    if static_rail is not None:
        assert len(static_rail.before_tool_calls) == before_tool
        assert len(static_rail.after_tool_calls) == after_tool


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
# TestExtensionLoadE2E — E2E 1-4: Plugin / AgentTemplate hot load
# ---------------------------------------------------------------------------


class TestExtensionLoadE2E:
    """E2E 1-4 from the ``plugin-agent-template-hotload`` plan.

    E2E1: legacy ``harness_config.yaml`` Plugin (package-relative imports).
    E2E2: new ``manifest.json`` Plugin (``package_type=plugin``).
    E2E3: new ``manifest.json`` AgentTemplate (root + one direct subagent).
    E2E4: AgentTemplate load fails mid-batch -> all partial binds roll back.

    Success cases: load → invoke → unload → invoke. E2E4: fail → bind check → invoke probe.
    """

    @pytest.mark.asyncio
    async def test_e2e1_legacy_yaml_plugin_full_capability_matrix_and_unload(
        self,
        tmp_path: Path,
    ) -> None:
        """Legacy AH-shaped harness_config.yaml Plugin: bind + invoke every capability, then unload."""
        pkg = _write_e2e1_legacy_yaml_package(tmp_path)
        agent = await _create_initialized_agent(
            tmp_path,
            language="en",
            auto_create_workspace=True,
            max_iterations=3,
            completion_timeout=12.0,
        )
        _clear_default_identity_section(agent)

        record = await agent.load_plugin(str(pkg.package))

        assert record.source_uri == str((pkg.package / "harness_config.yaml").resolve())
        kinds = [ref.kind for ref in record.refs]
        assert kinds.count(ResourceKind.TOOL) == 1
        assert kinds.count(ResourceKind.RAIL) == 1
        assert kinds.count(ResourceKind.SKILL) == 1
        assert kinds.count(ResourceKind.PROMPT_SECTION) == 2
        assert kinds.count(ResourceKind.MCP) == 1

        _assert_tool_registered_and_resolvable(agent, tool_name=pkg.tool_name)
        guard = _filename_guard_rail(agent)
        _assert_filename_guard_rail_wired(agent)
        _assert_mini_skills_loaded(agent)
        _assert_prompt_section_matches_file(
            agent,
            section_name=_IDENTITY_SECTION_NAME,
            file_text=pkg.identity_text,
            markers=(_E2E1_IDENTITY_MARKER,),
        )
        _assert_prompt_section_matches_file(
            agent,
            section_name=_SOUL_SECTION_NAME,
            file_text=pkg.soul_text,
            markers=(_E2E1_SOUL_MARKER,),
        )
        _assert_mcp_bound(agent, server_name=pkg.mcp_server_name)

        # Tool: deterministic tool_call actually executed via invoke(); prompt visible to the model.
        finish_rail = _ForceFinishAfterNamedToolRail(pkg.tool_name)
        await agent.register_rail(finish_rail)
        tool_sentinel = "e2e1_tool_call"
        tool_model = _DeterministicToolCallModel(tool_name=pkg.tool_name, tool_args={"sentinel": tool_sentinel})
        agent.react_agent.set_llm(tool_model)
        tool_result = await agent.invoke(
            {"query": "Call the e2e1 tool once.", "conversation_id": f"e2e1_tool_{tmp_path.name}"}
        )
        assert isinstance(tool_result, dict)
        assert tool_result.get("result_type") == "answer"
        assert finish_rail.tool_results and finish_rail.tool_results[0] == {"sentinel": tool_sentinel}
        assert tool_model.call_count == 1
        assert pkg.tool_name in tool_model.last_tool_names()
        assert _E2E1_IDENTITY_MARKER in tool_model.last_system_prompt()
        assert _E2E1_SOUL_MARKER in tool_model.last_system_prompt()

        # Rail: before_tool_call spy trace proves the hot-loaded FilenameGuardRail ran.
        guard._strict_mode = True
        guard_calls, abort_reasons = await _instrument_filename_guard_before_tool_call(agent, guard)
        agent.react_agent.set_llm(
            _DeterministicToolCallModel(tool_name="bash", tool_args={"command": "echo blocked > payload.exe"})
        )
        await agent.invoke(
            {"query": "Write payload.exe via bash once.", "conversation_id": f"e2e1_rail_{tmp_path.name}"}
        )
        _assert_filename_guard_used_on_bash_exe(calls=guard_calls, abort_reasons=abort_reasons)

        # Skill: skill_tool call surfaces the target SKILL.md body/description.
        # Reuse finish_rail (it force-finishes after any named tool call) so the
        # deterministic fake model's repeat tool_call does not exhaust max_iterations.
        finish_rail.tool_name = "skill_tool"
        finish_rail.tool_results.clear()
        skill_result = await _skill_tool_result(
            agent,
            skill_name=_MINI_SIDECAR_SKILL_NAME,
            conversation_id=f"e2e1_skill_{tmp_path.name}",
        )
        assert skill_result.get("result_type") == "answer"
        skill_tool_text = str(skill_result.get("tool_result")).lower()
        assert (
            _MINI_SIDECAR_SKILL_BODY_MARKER.lower() in skill_tool_text
            or _MINI_SIDECAR_SKILL_DESCRIPTION_MARKER in skill_tool_text
        )

        # Unload: every ref from this record is undone, then invoke proves gone.
        unloaded = await agent.unload_extension(record)
        assert len(unloaded) == len(record.refs)
        assert agent.ability_manager.get(pkg.tool_name) is None
        assert not _find_rails(agent, "FilenameGuardRail")
        assert agent.system_prompt_builder.get_section(_IDENTITY_SECTION_NAME) is None
        assert agent.system_prompt_builder.get_section(_SOUL_SECTION_NAME) is None
        _assert_mcp_unbound(agent, server_name=pkg.mcp_server_name)

        await _invoke_probe_extension_gone(
            agent,
            tool_name=pkg.tool_name,
            conversation_id=f"e2e1_after_unload_{tmp_path.name}",
            prompt_markers=(_E2E1_IDENTITY_MARKER, _E2E1_SOUL_MARKER),
        )
        finish_rail.tool_name = "skill_tool"
        finish_rail.tool_results.clear()
        skill_after = await _skill_tool_result(
            agent,
            skill_name=_MINI_SIDECAR_SKILL_NAME,
            conversation_id=f"e2e1_skill_after_unload_{tmp_path.name}",
        )
        skill_after_text = str(skill_after.get("tool_result")).lower()
        assert _MINI_SIDECAR_SKILL_BODY_MARKER.lower() not in skill_after_text

    @pytest.mark.asyncio
    async def test_e2e2_new_plugin_manifest_full_capability_matrix(
        self,
        tmp_path: Path,
    ) -> None:
        """New manifest.json Plugin: bind + invoke tool/rail/skill/prompt/mcp."""
        pkg = _write_e2e2_plugin_manifest_package(tmp_path)
        agent = await _create_initialized_agent(
            tmp_path,
            language="en",
            auto_create_workspace=True,
            max_iterations=3,
            completion_timeout=12.0,
        )

        record = await agent.load_plugin(str(pkg.package))

        assert record.source_uri == str((pkg.package / "manifest.json").resolve())
        kinds = [ref.kind for ref in record.refs]
        assert kinds.count(ResourceKind.TOOL) == 1
        assert kinds.count(ResourceKind.RAIL) == 1
        assert kinds.count(ResourceKind.SKILL) == 1
        assert kinds.count(ResourceKind.PROMPT_SECTION) == 1
        assert kinds.count(ResourceKind.MCP) == 1

        _assert_tool_registered_and_resolvable(agent, tool_name=pkg.fixture.tool_name)
        static_rail = _assert_from_spec_static_rail_bound(agent)
        assert agent.ability_manager.get("skill_tool") is not None
        section = agent.system_prompt_builder.get_section(_E2E2_PLUGIN_PROMPT_SECTION_NAME)
        assert section is not None
        assert _E2E2_PROMPT_MARKER in section.render("en")
        assert _E2E2_PROMPT_MARKER in agent.system_prompt_builder.build()
        _assert_mcp_bound(agent, server_name=pkg.mcp_server_name)

        await _invoke_and_assert_static_tool_rail_used(
            agent,
            fixture=pkg.fixture,
            static_rail=static_rail,
            sentinel="e2e2_tool_call",
            conversation_id=f"e2e2_tool_{tmp_path.name}",
            query="Call the e2e2 plugin tool once.",
        )

        skill_result = await _skill_tool_result(
            agent,
            skill_name="from_spec_skill",
            conversation_id=f"e2e2_skill_{tmp_path.name}",
        )
        assert skill_result.get("result_type") == "answer"
        assert _FROM_SPEC_SKILL_BODY_MARKER in str(skill_result.get("tool_result"))

        unloaded = await agent.unload_extension(record)
        assert len(unloaded) == len(record.refs)
        assert agent.ability_manager.get(pkg.fixture.tool_name) is None
        assert not _find_rails(agent, "FromSpecStaticRail")
        assert agent.system_prompt_builder.get_section(_E2E2_PLUGIN_PROMPT_SECTION_NAME) is None
        _assert_mcp_unbound(agent, server_name=pkg.mcp_server_name)

        # Bind-phase invoke bakes the full prompt into identity; drop that
        # residue so the post-unload probe checks the plugin section itself.
        _clear_default_identity_section(agent)
        agent.apply_prompt_builder_to_react_agent()

        await _invoke_probe_extension_gone(
            agent,
            tool_name=pkg.fixture.tool_name,
            conversation_id=f"e2e2_after_unload_{tmp_path.name}",
            prompt_markers=(_E2E2_PROMPT_MARKER,),
            static_rail=static_rail,
        )
        finish_rail = _ForceFinishAfterNamedToolRail("skill_tool")
        await agent.register_rail(finish_rail)
        skill_after = await _skill_tool_result(
            agent,
            skill_name="from_spec_skill",
            conversation_id=f"e2e2_skill_after_unload_{tmp_path.name}",
        )
        assert _FROM_SPEC_SKILL_BODY_MARKER not in str(skill_after.get("tool_result"))

    @pytest.mark.asyncio
    async def test_e2e3_agent_template_root_and_subagent_materialization(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """New manifest.json AgentTemplate: root capability overlay + direct child subagent."""
        pkg = _write_e2e3_agent_template_package(tmp_path)
        agent = await _create_initialized_agent(
            tmp_path,
            language="en",
            auto_create_workspace=True,
            max_iterations=3,
            completion_timeout=12.0,
            model=_inert_model(model_name=_E2E3_HOST_MODEL_NAME),
        )
        host_card = agent.card
        host_model = agent.deep_config.model
        assert host_model is not None
        assert host_model.model_config.model_name == _E2E3_HOST_MODEL_NAME

        record = await agent.load_agent_template(str(pkg.package))

        assert record.source_uri == str((pkg.package / "manifest.json").resolve())
        kinds = [ref.kind for ref in record.refs]
        assert kinds.count(ResourceKind.TOOL) == 1
        # FromSpecStaticRail (root) + bootstrap SubagentRail.
        assert kinds.count(ResourceKind.RAIL) == 2
        assert kinds.count(ResourceKind.SKILL) == 1
        assert kinds.count(ResourceKind.PROMPT_SECTION) == 1
        assert kinds.count(ResourceKind.MCP) == 1
        assert kinds.count(ResourceKind.SUBAGENT) == 1

        # Host identity / model are untouched by an AgentTemplate load.
        assert agent.card is host_card
        assert agent.deep_config.model is host_model
        _assert_tool_registered_and_resolvable(agent, tool_name=pkg.fixture.tool_name)
        static_rail = _assert_from_spec_static_rail_bound(agent)
        assert _find_rails(agent, "SubagentRail")
        assert agent.ability_manager.get("task_tool") is not None
        assert agent.ability_manager.get("skill_tool") is not None
        _assert_mcp_bound(agent, server_name=pkg.mcp_server_name)
        identity_section = agent.system_prompt_builder.get_section(_IDENTITY_SECTION_NAME)
        assert identity_section is not None
        assert _E2E3_ROOT_IDENTITY_MARKER in identity_section.render("en")

        await _invoke_and_assert_static_tool_rail_used(
            agent,
            fixture=pkg.fixture,
            static_rail=static_rail,
            sentinel="e2e3_root_tool_call",
            conversation_id=f"e2e3_tool_{tmp_path.name}",
            query="Call the e2e3 root tool once.",
        )

        # Direct child subagent materialized as its own SubAgentConfig (own card+model).
        subagent_config = next(
            (
                item
                for item in agent.deep_config.subagents or []
                if item.agent_card.name == _E2E3_CHILD_AGENT_NAME
            ),
            None,
        )
        assert subagent_config is not None
        assert subagent_config.model is not None
        assert subagent_config.model is not host_model
        assert subagent_config.model.model_config.model_name == _E2E3_CHILD_MODEL_NAME

        child_tool_name = f"e2e3_child_tool_name_{tmp_path.name}"
        child_tool_sentinel = "e2e3_child_tool_call"
        created_children: list[DeepAgent] = []
        child_finish_rails: list[_ForceFinishAfterNamedToolRail] = []
        child_models: list[_DeterministicToolCallModel] = []
        original_create_subagent = agent.create_subagent

        def _create_subagent_for_delegation(
            subagent_type: str,
            subsession_id: str,
            **kwargs: Any,
        ) -> DeepAgent:
            child = original_create_subagent(subagent_type, subsession_id, **kwargs)
            finish_rail = _ForceFinishAfterNamedToolRail(child_tool_name)
            child_model = _DeterministicToolCallModel(
                tool_name=child_tool_name,
                tool_args={"sentinel": child_tool_sentinel},
            )
            child.add_rail(finish_rail)
            child.react_agent.set_llm(child_model)
            created_children.append(child)
            child_finish_rails.append(finish_rail)
            child_models.append(child_model)
            return child

        monkeypatch.setattr(agent, "create_subagent", _create_subagent_for_delegation)
        task_finish_rail = _ForceFinishAfterNamedToolRail("task_tool")
        await agent.register_rail(task_finish_rail)
        host_delegation_model = _DeterministicToolCallModel(
            tool_name="task_tool",
            tool_args={
                "subagent_type": _E2E3_CHILD_AGENT_NAME,
                "task_description": "Call the e2e3 child-owned tool once.",
            },
        )
        agent.react_agent.set_llm(host_delegation_model)

        delegation_result = await agent.invoke(
            {
                "query": "Delegate this task to the e2e3 child specialist.",
                "conversation_id": f"e2e3_delegate_{tmp_path.name}",
            }
        )

        assert isinstance(delegation_result, dict)
        assert delegation_result.get("result_type") == "answer"
        assert len(created_children) == 1
        child = created_children[0]
        assert child is not agent
        assert child.card.name == _E2E3_CHILD_AGENT_NAME
        assert child.deep_config is not None
        assert child.deep_config.model is subagent_config.model
        assert child.deep_config.model.model_config.model_name == _E2E3_CHILD_MODEL_NAME
        assert child.react_config.model_name == _E2E3_CHILD_MODEL_NAME
        assert child.react_config.model_config_obj is child.deep_config.model.model_config
        assert not (child.deep_config.subagents or [])

        _assert_tool_registered_and_resolvable(child, tool_name=child_tool_name)
        assert task_finish_rail.tool_results
        assert host_delegation_model.call_count == 1
        assert "task_tool" in host_delegation_model.last_tool_names()
        assert child_finish_rails[0].tool_results == [{"sentinel": child_tool_sentinel}]
        assert child_models[0].call_count == 1
        assert child_tool_name in child_models[0].last_tool_names()

        unloaded = await agent.unload_extension(record)
        assert len(unloaded) == len(record.refs)
        assert agent.card is host_card
        assert agent.deep_config.model is host_model
        assert agent.ability_manager.get(pkg.fixture.tool_name) is None
        assert not _find_rails(agent, "FromSpecStaticRail")
        assert not any(
            item.agent_card.name == _E2E3_CHILD_AGENT_NAME for item in (agent.deep_config.subagents or [])
        )
        _assert_mcp_unbound(agent, server_name=pkg.mcp_server_name)

        await _invoke_probe_extension_gone(
            agent,
            tool_name=pkg.fixture.tool_name,
            conversation_id=f"e2e3_after_unload_{tmp_path.name}",
            prompt_markers=(_E2E3_ROOT_IDENTITY_MARKER,),
            static_rail=static_rail,
        )

    @pytest.mark.asyncio
    async def test_e2e4_agent_template_load_failure_rolls_back_partial_bindings(
        self,
        tmp_path: Path,
    ) -> None:
        """A late-stage skill bind conflict fails the whole batch; earlier binds roll back."""
        pkg = _write_e2e3_agent_template_package(
            tmp_path,
            identity_marker=_E2E4_ROOT_IDENTITY_MARKER,
            with_subagent=False,
        )
        agent = await _create_initialized_agent(
            tmp_path,
            language="en",
            auto_create_workspace=True,
            max_iterations=3,
            completion_timeout=12.0,
        )
        _clear_default_identity_section(agent)

        # Pre-bind a skill at the exact directory the template also mounts, so
        # `_bind_skill` raises "Skill already bound" only after tool/mcp/rail/prompt
        # have already bound in this batch -- forcing all four to roll back.
        await agent.load_plugin_ability(
            skills=[ResolvedSkill(directory=str(pkg.fixture.skill_root), mode="all")]
        )

        with pytest.raises(BaseError) as exc_info:
            await agent.load_agent_template(str(pkg.package))
        assert exc_info.value.status == StatusCode.DEEPAGENT_LOAD_AGENT_TEMPLATE_ERROR

        # Batch failed: none of this load's tool/rail/prompt/mcp remain bound.
        assert agent.ability_manager.get(pkg.fixture.tool_name) is None
        assert not _find_rails(agent, "FromSpecStaticRail")
        assert agent.system_prompt_builder.get_section(_IDENTITY_SECTION_NAME) is None
        _assert_mcp_unbound(agent, server_name=pkg.mcp_server_name)
        assert not (agent.deep_config.subagents or [])

        # The pre-existing skill mount (bound before the failed batch) is untouched.
        skill_rails = _registered_skill_rails(agent)
        assert len(skill_rails) == 1
        assert {skill.name for skill in skill_rails[0].skills} == {"from_spec_skill"}

        await _invoke_probe_extension_gone(
            agent,
            tool_name=pkg.fixture.tool_name,
            conversation_id=f"e2e4_after_rollback_{tmp_path.name}",
            prompt_markers=(_E2E4_ROOT_IDENTITY_MARKER,),
        )


# ---------------------------------------------------------------------------
# TestRunnerHotLoadSmoke — Runner.run_agent + enqueue harness_config
# ---------------------------------------------------------------------------


class TestRunnerHotLoadSmoke:
    """Runner.run_agent smoke with enqueue/drain of the legacy AH-shaped package."""

    @pytest.mark.asyncio
    async def test_runner_uses_hot_loaded_legacy_ah_tool(
        self,
        tmp_path: Path,
    ) -> None:
        """Enqueue legacy AH package, then Runner.run_agent invokes the hot-loaded tool."""
        pkg = _write_e2e1_legacy_yaml_package(tmp_path)
        workspace_path = tmp_path / "workspace"
        sys_operation = _make_sys_operation(tmp_path)
        finish_rail = _ForceFinishAfterNamedToolRail(pkg.tool_name)
        config = DeepAgentConfig(
            card=AgentCard(
                id="deep_agent_load_runner_e2e",
                name="deep_agent_load_runner_e2e",
            ),
            workspace=Workspace(root_path=str(workspace_path)),
            sys_operation=sys_operation,
            rails=[
                finish_rail,
                SkillUseRail(skills_dir=[], skill_mode="all", include_tools=False),
            ],
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
            _clear_default_identity_section(agent)
            await _hot_load_harness_config(agent, pkg.package)
            _assert_hot_loaded_legacy_ah_package(agent, pkg)

            agent.react_agent.set_llm(
                _DeterministicToolCallModel(
                    tool_name=pkg.tool_name,
                    tool_args={"sentinel": _RUNNER_HOT_TOOL_SENTINEL},
                )
            )
            result = await Runner.run_agent(
                agent,
                {
                    "query": "Call the legacy AH-shaped tool once.",
                    "conversation_id": f"runner_hot_ah_{tmp_path.name}",
                },
                session=f"deep_agent_load_runner_e2e_{tmp_path.name}",
            )

            assert isinstance(result, dict)
            assert result.get("result_type") == "answer"
            assert finish_rail.tool_results, "legacy AH tool did not run via Runner.run_agent"
            assert finish_rail.tool_results[0] == {"sentinel": _RUNNER_HOT_TOOL_SENTINEL}
            _assert_hot_loaded_legacy_ah_package(agent, pkg)
        finally:
            await Runner.stop()
