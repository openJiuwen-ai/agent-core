"""Read-only `design_read_file` scoped to experiments/<run_id>/design/.

The coding agent's workspace `read_file` cannot reach the living summary
(`experiment_design.md`). This rail is the same pattern as
OpenJiuwenReferenceRail: a separate SysOperation with restrict_to_sandbox,
and a distinct tool name so it does not collide with workspace `read_file`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperation,
    SysOperationCard,
)
from openjiuwen.harness.prompts.tools import (
    ToolCardBuildOptions,
    build_tool_card,
    register_tool_provider,
)
from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider
from openjiuwen.harness.prompts.tools.filesystem import (
    READ_FILE_DESCRIPTION,
    get_read_file_input_params,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.filesystem import ReadFileTool

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import to_project_relative

_READ_NAME = "design_read_file"

_SCOPE_SUFFIX = {
    "cn": "（只读，范围限定于本 run 的 design/ 目录，用于读取 experiment_design.md）",
    "en": (
        " (read-only, scoped to this run's design/ directory — use this to "
        "read experiment_design.md; workspace read_file cannot reach it)"
    ),
}


def _scoped_description(base: dict[str, str], language: str) -> str:
    text = base.get(language, base["cn"])
    return text + _SCOPE_SUFFIX.get(language, _SCOPE_SUFFIX["en"])


class _DesignReadFileProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return _READ_NAME

    def get_description(self, language: str = "cn") -> str:
        return _scoped_description(READ_FILE_DESCRIPTION, language)

    def get_input_params(self, language: str = "cn") -> dict[str, Any]:
        return get_read_file_input_params(language)


register_tool_provider(_DesignReadFileProvider())


class DesignPathError(ValueError):
    """Raised when a design-tool path escapes experiments/<run_id>/design/."""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _virtual_prefixes(design_root: Path) -> tuple[str, ...]:
    prefixes = ["design"]
    try:
        prefixes.append(to_project_relative(design_root))
    except ValueError:
        pass
    return tuple(prefixes)


def _strip_virtual_prefix(posix: str, prefixes: tuple[str, ...]) -> str:
    text = posix.replace("\\", "/").lstrip("/")
    lowered = text.lower()
    for prefix in sorted(prefixes, key=len, reverse=True):
        marker = prefix.replace("\\", "/").strip("/").lower()
        if not marker:
            continue
        if lowered == marker:
            return ""
        if lowered.startswith(marker + "/"):
            return text[len(marker) + 1:]
    return text


def normalize_design_path(raw: str | None, design_root: Path) -> Path:
    """Map a project-relative or design-relative path onto the design sandbox.

    Accepts ``experiment_design.md``, ``design/experiment_design.md``,
    ``experiments/<run_id>/design/experiment_design.md``, and absolute paths
    already inside the design root. Rejects traversal and anything outside.
    """
    text = (raw or "").strip()
    if not text:
        raise DesignPathError("Access denied: empty design path")
    root = design_root.resolve()
    candidate = Path(text)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if not _is_relative_to(resolved, root):
            raise DesignPathError(
                f"Access denied: Path {resolved} outside sandbox [{str(root)!r}]"
            )
        return resolved

    posix = text.replace("\\", "/")
    rel = _strip_virtual_prefix(posix, _virtual_prefixes(root))
    parts: list[str] = []
    for part in Path(rel).parts if rel else ():
        if part in (".", ""):
            continue
        if part == "..":
            raise DesignPathError("Access denied: path traversal is not allowed")
        parts.append(part)
    resolved = (root.joinpath(*parts)).resolve()
    if not _is_relative_to(resolved, root):
        raise DesignPathError(
            f"Access denied: Path {resolved} outside sandbox [{str(root)!r}]"
        )
    return resolved


class _DesignReadFileTool(ReadFileTool):
    def __init__(
        self,
        operation: SysOperation,
        language: str,
        agent_id: str | None,
        design_root: Path,
    ):
        Tool.__init__(
            self,
            build_tool_card(
                _READ_NAME,
                "DesignReadFileTool",
                language,
                agent_id=agent_id,
                options=ToolCardBuildOptions(parallel_safe=True),
            ),
        )
        self.operation = operation
        self.enable_image_multimodal = False
        self._design_root = design_root

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        rewritten = dict(inputs or {})
        try:
            rewritten["file_path"] = str(
                normalize_design_path(str(rewritten.get("file_path") or ""), self._design_root)
            )
        except DesignPathError as exc:
            return ToolOutput(success=False, data=None, error=str(exc))
        return await super().invoke(rewritten, **kwargs)


class DesignReferenceRail(DeepAgentRail):
    """Gives the coding agent read-only access to this run's design/ folder."""

    priority = 100

    def __init__(self, *, design_root: Path) -> None:
        super().__init__()
        self._design_root = design_root.resolve()
        self.tools: list[Any] | None = None

    def init(self, agent) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        card = SysOperationCard(
            id=f"design_ref_{agent_id or 'default'}",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(
                sandbox_root=[str(self._design_root)],
                restrict_to_sandbox=True,
            ),
        )
        operation = SysOperation(card)
        tool = _DesignReadFileTool(operation, lang, agent_id, self._design_root)
        self.tools = [tool]
        agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        if self.tools:
            for tool in self.tools:
                name = getattr(tool.card, "name", None)
                if name and hasattr(agent, "ability_manager"):
                    agent.ability_manager.remove_ability(name)


__all__ = [
    "DesignPathError",
    "DesignReferenceRail",
    "normalize_design_path",
]
