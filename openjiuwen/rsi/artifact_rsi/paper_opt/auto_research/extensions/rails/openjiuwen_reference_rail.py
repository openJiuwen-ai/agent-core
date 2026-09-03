"""Read-only browsing rail scoped to agent-core-rsi's own docs/ (the SDK's
real, always-current documentation — see _DEFAULT_ASSETS_ROOT) — separate
from the coding agent's main read-write toolset, which is scoped to its own
workspace and cannot reach the reference docs at
all. Lets the agent read/list/glob SDK reference material on demand, mid-task,
as it discovers what it actually needs — instead of only ever seeing the
fixed set of excerpts code_implementation/grounding.py guessed at upfront and
injected into the initial prompt.

Why a whole new rail instead of reusing SysOperationRail(read_only=True):

1. DeepAgent overwrites every rail's self.sys_operation with the single
   agent-wide instance right before init() runs (see
   openjiuwen/harness/deep_agent.py — set_sys_operation() then init_rail()).
   A second, differently-scoped SysOperation has to be built and held by this
   rail itself; it can't rely on what the framework hands it.
2. ReadFileTool/GlobTool/ListDirTool hardcode their tool-card name
   ("read_file", "glob", "list_files"). Reusing them unmodified alongside
   GuardedSysOperationRail's identically-named tools would collide in
   ability_manager — one set silently overwrites the other (same
   f"{name}_{agent_id}" id, registered with refresh semantics). So every tool
   here is a thin subclass that builds its ToolCard under a distinct
   "openjiuwen_ref_*" name via the SDK's own documented runtime-registration
   hook, register_tool_provider() (openjiuwen/harness/prompts/tools/__init__.py),
   instead of the built-in names.

A bare, unregistered SysOperation is fully self-contained — SysOperation.__init__
only reads its own SysOperationCard, no dependency on Runner.resource_mgr —
so this rail owns its SysOperation's lifetime entirely via init()/uninit(),
with no need to register it for cross-callsite lookup the way the framework's
own workspace-scoped SysOperation does.

No grep tool, deliberately: GrepTool shells out (rg / PowerShell
Select-String) rather than doing an in-process fs() scan, and under
restrict_to_sandbox=True the installed SDK's own path-safety check
(core/sys_operation/local/shell_operation.py::_extract_abs_paths) has a real
bug on Windows — it runs an unquoted-path regex unconditionally alongside the
quoted-path one, and the unquoted pattern's exclusion set doesn't exclude
quote characters, so it double-matches an already-quoted absolute path and
drags the trailing quote into the extracted string. That mismatched string
then fails the sandbox-containment check the tool call is supposed to pass,
for any absolute path — verified directly against the installed package, not
a guess. Can't patch installed OpenJiuwen (see docs/openjiuwen_conventions.md
"only add, never modify"), and won't drop restrict_to_sandbox just to route
around it — that flag is the actual security boundary this rail exists to
enforce. Revisit once the SDK fixes it; read/glob/list cover most of the need
in the meantime (find candidate files by name, then read their content).
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
    GLOB_DESCRIPTION,
    LIST_DIR_DESCRIPTION,
    READ_FILE_DESCRIPTION,
    get_glob_input_params,
    get_list_dir_input_params,
    get_read_file_input_params,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.filesystem import GlobTool, ListDirTool, ReadFileTool

# File-relative (like reporting/agent.py's _SKILLS_DIR), not CWD-relative --
# points at agent-core-rsi's own real docs/ directory, not a vendored
# assets/openjiuwen/ snapshot. The vendored copy this constant used to point
# at was both CWD-fragile and confirmed stale (929 files in the real docs/
# vs. 834 in the old snapshot) -- see docs/agent_core_rsi_migration_risks.md.
# Walk from this file: rails -> extensions -> auto_research -> paper_opt ->
# artifact_rsi -> rsi -> openjiuwen -> repo root.
_DEFAULT_ASSETS_ROOT = Path(__file__).resolve().parents[7] / "docs"

_READ_NAME = "openjiuwen_ref_read_file"
_GLOB_NAME = "openjiuwen_ref_glob"
_LIST_NAME = "openjiuwen_ref_list_files"

_SCOPE_SUFFIX = {
    "cn": "（只读，范围限定于 OpenJiuwen 参考文档，不可用于其他目录）",
    "en": " (read-only, scoped to OpenJiuwen reference documentation only)",
}


def _scoped_description(base: dict[str, str], language: str) -> str:
    text = base.get(language, base["cn"])
    return text + _SCOPE_SUFFIX.get(language, _SCOPE_SUFFIX["en"])


class _RefReadFileProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return _READ_NAME

    def get_description(self, language: str = "cn") -> str:
        return _scoped_description(READ_FILE_DESCRIPTION, language)

    def get_input_params(self, language: str = "cn") -> dict[str, Any]:
        return get_read_file_input_params(language)


class _RefGlobProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return _GLOB_NAME

    def get_description(self, language: str = "cn") -> str:
        return _scoped_description(GLOB_DESCRIPTION, language)

    def get_input_params(self, language: str = "cn") -> dict[str, Any]:
        return get_glob_input_params(language)


class _RefListDirProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return _LIST_NAME

    def get_description(self, language: str = "cn") -> str:
        return _scoped_description(LIST_DIR_DESCRIPTION, language)

    def get_input_params(self, language: str = "cn") -> dict[str, Any]:
        return get_list_dir_input_params(language)


# Runtime registration is idempotent (register_tool_provider just overwrites
# the registry entry by name) and only needs to happen once per process, but
# doing it at import time keeps it colocated with the names it registers
# rather than requiring callers to remember a setup step.
for _provider in (_RefReadFileProvider(), _RefGlobProvider(), _RefListDirProvider()):
    register_tool_provider(_provider)


class ReferencePathError(ValueError):
    """Raised when a reference-tool path escapes the configured assets root."""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _strip_virtual_prefix(posix: str) -> str:
    """Strip a legacy prefix so old-style paths keep resolving after
    _DEFAULT_ASSETS_ROOT moved from a vendored assets/openjiuwen/{docs,examples}
    snapshot to agent-core-rsi's real docs/ directly. The real root *is*
    docs/ now, so a correct new-style path is just ``en/SUMMARY.md``, not
    ``docs/en/SUMMARY.md`` -- but both forms are accepted rather than
    breaking whatever already assumed the old convention (prompts, agent
    habit, etc.)."""
    text = posix.replace("\\", "/").lstrip("/")
    lowered = text.lower()
    if lowered in ("assets/openjiuwen", "assets/openjiuwen/docs"):
        return ""
    for prefix in ("assets/openjiuwen/docs/", "assets/openjiuwen/", "docs/"):
        if lowered.startswith(prefix):
            return text[len(prefix) :]
    return text


def normalize_reference_path(raw: str | None, assets_root: Path) -> Path:
    """Map a virtual or relative SDK path onto the configured assets root.

    Accepts a bare path relative to the real docs/ root (e.g.
    ``en/SUMMARY.md``), the legacy ``docs/...`` or ``assets/openjiuwen/...``
    forms (see `_strip_virtual_prefix`), and absolute paths already inside
    the assets root. Rejects traversal and anything outside.
    """
    text = (raw or "").strip()
    if not text:
        raise ReferencePathError("Access denied: empty reference path")
    root = assets_root.resolve()
    candidate = Path(text)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if not _is_relative_to(resolved, root):
            raise ReferencePathError(
                f"Access denied: Path {resolved} outside sandbox [{str(root)!r}]"
            )
        return resolved

    posix = text.replace("\\", "/")
    rel = _strip_virtual_prefix(posix)
    parts: list[str] = []
    for part in Path(rel).parts if rel else ():
        if part in (".", ""):
            continue
        if part == "..":
            raise ReferencePathError("Access denied: path traversal is not allowed")
        parts.append(part)
    resolved = (root.joinpath(*parts)).resolve()
    if not _is_relative_to(resolved, root):
        raise ReferencePathError(
            f"Access denied: Path {resolved} outside sandbox [{str(root)!r}]"
        )
    return resolved


def rewrite_glob_pattern(pattern: str) -> str:
    text = (pattern or "").replace("\\", "/").lstrip("/")
    stripped = _strip_virtual_prefix(text)
    return stripped or "*"


def rewrite_reference_inputs(
    inputs: dict[str, Any],
    assets_root: Path,
    *,
    path_keys: tuple[str, ...] = (),
    default_missing_path: bool = False,
    rewrite_pattern: bool = False,
) -> dict[str, Any]:
    rewritten = dict(inputs)
    for key in path_keys:
        value = rewritten.get(key)
        if value in (None, ""):
            if default_missing_path:
                rewritten[key] = str(assets_root.resolve())
            continue
        rewritten[key] = str(normalize_reference_path(str(value), assets_root))
    if rewrite_pattern and rewritten.get("pattern"):
        rewritten["pattern"] = rewrite_glob_pattern(str(rewritten["pattern"]))
    return rewritten


class _ReferencePathMixin:
    _assets_root: Path
    _path_keys: tuple[str, ...] = ()
    _default_missing_path = False
    _rewrite_pattern = False

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        try:
            rewritten = rewrite_reference_inputs(
                dict(inputs or {}),
                self._assets_root,
                path_keys=self._path_keys,
                default_missing_path=self._default_missing_path,
                rewrite_pattern=self._rewrite_pattern,
            )
        except ReferencePathError as exc:
            return ToolOutput(success=False, data=None, error=str(exc))
        return await super().invoke(rewritten, **kwargs)  # type: ignore[misc]


class _RefReadFileTool(_ReferencePathMixin, ReadFileTool):
    _path_keys = ("file_path",)

    def __init__(
        self,
        operation: SysOperation,
        language: str,
        agent_id: str | None,
        assets_root: Path,
    ):
        # Deliberately calls Tool.__init__ directly, skipping ReadFileTool's
        # own __init__ (which hardcodes build_tool_card("read_file", ...)) —
        # see module docstring for why the name has to differ.
        Tool.__init__(
            self,
            build_tool_card(
                _READ_NAME,
                "OpenJiuwenRefReadFileTool",
                language,
                agent_id=agent_id,
                options=ToolCardBuildOptions(parallel_safe=True),
            ),
        )
        self.operation = operation
        self.enable_image_multimodal = False
        self._assets_root = assets_root


class _RefGlobTool(_ReferencePathMixin, GlobTool):
    _path_keys = ("path",)
    _default_missing_path = True
    _rewrite_pattern = True

    def __init__(
        self,
        operation: SysOperation,
        language: str,
        agent_id: str | None,
        assets_root: Path,
    ):
        Tool.__init__(
            self,
            build_tool_card(
                _GLOB_NAME,
                "OpenJiuwenRefGlobTool",
                language,
                agent_id=agent_id,
                options=ToolCardBuildOptions(parallel_safe=True),
            ),
        )
        self.operation = operation
        self._assets_root = assets_root


class _RefListDirTool(_ReferencePathMixin, ListDirTool):
    _path_keys = ("path",)
    _default_missing_path = True

    def __init__(
        self,
        operation: SysOperation,
        language: str,
        agent_id: str | None,
        assets_root: Path,
    ):
        Tool.__init__(
            self,
            build_tool_card(
                _LIST_NAME,
                "OpenJiuwenRefListDirTool",
                language,
                agent_id=agent_id,
                options=ToolCardBuildOptions(parallel_safe=True),
            ),
        )
        self.operation = operation
        self._assets_root = assets_root


class OpenJiuwenReferenceRail(DeepAgentRail):
    """Gives the coding agent read-only read/glob/list access to
    agent-core-rsi's real docs/ directory on demand, independent of its main
    read-write workspace toolset. See module docstring for the design
    rationale."""

    priority = 100  # same as SysOperationRail — no ordering dependency between them, just registered up front like it

    def __init__(self, *, assets_root: Path = _DEFAULT_ASSETS_ROOT) -> None:
        super().__init__()
        self._assets_root = assets_root.resolve()
        self.tools: list[Any] | None = None

    def init(self, agent) -> None:
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)

        card = SysOperationCard(
            id=f"openjiuwen_ref_{agent_id or 'default'}",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(
                sandbox_root=[str(self._assets_root)],
                restrict_to_sandbox=True,
            ),
        )
        reference_operation = SysOperation(card)

        self.tools = [
            _RefReadFileTool(reference_operation, lang, agent_id, self._assets_root),
            _RefGlobTool(reference_operation, lang, agent_id, self._assets_root),
            _RefListDirTool(reference_operation, lang, agent_id, self._assets_root),
        ]
        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        if self.tools:
            for tool in self.tools:
                name = getattr(tool.card, "name", None)
                if name and hasattr(agent, "ability_manager"):
                    agent.ability_manager.remove_ability(name)


__all__ = [
    "OpenJiuwenReferenceRail",
    "ReferencePathError",
    "normalize_reference_path",
    "rewrite_glob_pattern",
    "rewrite_reference_inputs",
]
