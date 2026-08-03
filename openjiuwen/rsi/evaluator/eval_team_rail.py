# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Eval-scoped rail for agent team workspace management."""

from __future__ import annotations

import shutil
from pathlib import Path

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

_CANONICAL_DELIVERABLE_FILES = ("index.html", "styles.css", "content_brief.md")


def _harvest_workspace_artifacts(workspace_dir: Path, dest_dir: Path) -> None:
    """Copy ``workspace_dir/artifacts/`` into ``dest_dir`` before workspace is deleted.

    Called from ``EvalTeamRail.before_tool_call`` when the leader invokes
    ``clean_team``.  At that moment the workspace is still intact; after
    ``clean_team`` executes the workspace directory is gone.  This function
    grabs the artifacts before the window closes so ``CaseRunner`` can still
    find them in ``dest_dir``.

    No-ops silently if the source artifacts directory does not exist.
    """
    sources = _artifact_source_dirs(workspace_dir)
    if not sources:
        logger.info("[EvalTeamRail] no artifacts dir in workspace, skipping pre-harvest")
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    harvested = _copy_artifact_trees(sources, dest_dir)
    _normalize_deliverable_artifacts(dest_dir)
    harvested = sorted(
        {
            *harvested,
            *(p.relative_to(dest_dir).as_posix() for p in dest_dir.rglob("*") if p.is_file()),
        }
    )
    logger.info(
        "[EvalTeamRail] pre-harvest: {} artifact(s) from {} source(s) copied to {}",
        len(harvested),
        len(sources),
        dest_dir,
    )


def _artifact_source_dirs(workspace_dir: Path) -> list[Path]:
    sources: list[Path] = []
    src = workspace_dir / "artifacts"
    if src.is_dir():
        sources.append(src)
    team_name = workspace_dir.parent.name
    member_root = workspace_dir.parent / "workspaces"
    for member_src in sorted(member_root.glob(f"*_workspace/.team/{team_name}/artifacts")):
        if not member_src.is_dir():
            continue
        if _same_path(member_src, src):
            continue
        sources.append(member_src)
    return sources


def _copy_artifact_trees(sources: list[Path], dest_dir: Path) -> list[str]:
    harvested: list[str] = []
    for src in sources:
        for source in sorted(path for path in src.rglob("*") if path.is_file()):
            rel_path = source.relative_to(src)
            target = dest_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if _should_copy_artifact(source, target):
                    shutil.copy2(source, target)
            except FileNotFoundError:
                logger.warning("[EvalTeamRail] artifact disappeared before copy: {}", source)
                continue
            harvested.append(rel_path.as_posix())
    return harvested


def _normalize_deliverable_artifacts(dest_dir: Path) -> None:
    if not dest_dir.is_dir():
        return
    for filename in _CANONICAL_DELIVERABLE_FILES:
        root_target = dest_dir / filename
        if root_target.is_file():
            continue
        source = _find_nested_artifact(dest_dir, filename)
        if source is None:
            continue
        if _should_copy_artifact(source, root_target):
            shutil.copy2(source, root_target)


def _find_nested_artifact(dest_dir: Path, filename: str) -> Path | None:
    candidates = [
        source for source in sorted(dest_dir.rglob(filename)) if source.is_file() and source.parent != dest_dir
    ]
    if not candidates:
        return None
    return max(candidates, key=_artifact_quality_key)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left.resolve() == right.resolve()


def _should_copy_artifact(source: Path, target: Path) -> bool:
    if not target.exists():
        return True
    if source.name in _CANONICAL_DELIVERABLE_FILES:
        return _artifact_quality_key(source) > _artifact_quality_key(target)
    return True


def _artifact_quality_key(path: Path) -> tuple[int, int, int]:
    try:
        size = path.stat().st_size
    except OSError:
        return (0, 0, 0)
    if path.name == "index.html":
        try:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            text = ""
        complete = int("</body>" in text and "</html>" in text)
        return (complete, text.count("<section"), size)
    return (int(size > 0), 0, size)


class EvalTeamRail(DeepAgentRail):
    """Eval-scoped rail that manages shared workspace access for agent teams.

    Two responsibilities kept in one rail to avoid two separate rail objects
    per agent per case:

    1. **Prompt guidance** (``before_model_call``): injects a prompt section
       instructing every agent to use ``write_file``/``edit_file`` for
       ``.team/`` paths, and to ignore any absolute workspace path that the
       system prompt may expose.  Prevents the LLM from constructing invalid
       absolute paths by concatenating the shared workspace root with the
       relative mount prefix.

    2. **Artifact pre-harvest** (``before_tool_call``): intercepts the
       leader's ``clean_team`` call.  At that moment the shared workspace
       (``<case>/workspace``) is still intact.  The rail copies
       ``<case>/workspace/artifacts/`` → ``<case>/artifacts/`` before the
       tool runs.  After ``clean_team`` the workspace is deleted by the
       ``TeamBackend``, but the artifacts are already safe in
       ``<case>/artifacts/``.  ``CaseRunner._harvest_artifacts`` detects
       that ``<case>/artifacts/`` already has content and skips the
       (now-pointless) workspace scan.

    **Why intercept ``clean_team`` rather than changing ``lifecycle``:**

    ``lifecycle="temporary"`` is intentional — it scopes the team DB to the
    case run.  Switching to ``persistent`` would change memory behaviour and
    leave cross-run residue in ``team_home``.  Intercepting ``clean_team``
    is the minimal, non-invasive solution: the rail races ahead of the tool,
    not against the harness's own cleanup.

    **Scoped to eval only:** attached in ``TeamSkillTeamFactory._build_agent_customizer``
    only when ``workspace_dir is not None``.  No ``openjiuwen.agent_teams``
    core code is touched.
    """

    priority = 5
    _SECTION_NAME = "eval_team_workspace_guidance"
    _CLEAN_TEAM_TOOL = "clean_team"
    _ARTIFACT_WRITE_TOOLS = {"write_file", "edit_file"}

    def __init__(
        self,
        team_name: str = "",
        workspace_dir: Path | None = None,
        dest_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self._team_name = team_name
        self._workspace_dir = workspace_dir
        self._dest_dir = dest_dir
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self._SECTION_NAME)
        self.system_prompt_builder = None

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Pre-harvest artifacts when the leader is about to call ``clean_team``."""
        if ctx.inputs.tool_name != self._CLEAN_TEAM_TOOL:
            return
        if self._workspace_dir is None or self._dest_dir is None:
            logger.warning("[EvalTeamRail] no workspace or dest dir")
            return
        _harvest_workspace_artifacts(self._workspace_dir, self._dest_dir)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Snapshot artifacts after writes, before temporary-team cleanup can remove them."""
        if ctx.inputs.tool_name not in self._ARTIFACT_WRITE_TOOLS:
            return
        if self._workspace_dir is None or self._dest_dir is None:
            return
        _harvest_workspace_artifacts(self._workspace_dir, self._dest_dir)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject shared workspace access policy into the system prompt."""
        _ = ctx
        if self.system_prompt_builder is None:
            return

        mount = f".team/{self._team_name}/" if self._team_name else ".team/"
        content_cn = (
            "# 团队共享文件访问规范\n\n"
            f"共享工作区通过 `{mount}` 挂载在每个成员的工作目录下。\n\n"
            "**路径规则（最重要）：**\n"
            f"- 访问共享文件的**唯一正确方式**是使用相对挂载前缀 `{mount}`，"
            f"例如 `{mount}artifacts/docs/文件名.md`。\n"
            "- Prompt 中可能出现共享工作区的绝对路径（如 `/tmp/.../workspace` 或 "
            "`C:\\...\\workspace`），**必须忽略该绝对路径**，不得将其与挂载前缀拼接，"
            "否则会产生不存在的路径。\n\n"
            "**工具规则：**\n"
            f"- 写入 `{mount}` 下的共享文件 **必须使用 `write_file` 或 `edit_file`**。\n"
            "- **禁止** 用 `bash` 的 `cat >`、`echo >`、heredoc（`<<EOF`）等重定向方式写共享文件。\n"
            "  bash 重定向在部分平台上无法可靠写入共享目录，且会绕过版本控制与锁机制。\n"
            f"- 读取共享文件使用 `read_file`，路径形如 `{mount}artifacts/docs/文件名.md`。\n"
        )
        content_en = (
            "# Team Shared Workspace Access Policy\n\n"
            f"The shared workspace is mounted at `{mount}` inside each member's working directory.\n\n"
            "**Path rules (most important):**\n"
            f"- The **only correct way** to access shared files is via the relative mount prefix `{mount}`, "
            f"e.g. `{mount}artifacts/docs/filename.md`.\n"
            "- The prompt may show an absolute path to the shared workspace root "
            "(e.g. `/tmp/.../workspace` or `C:\\...\\workspace`). "
            "**Ignore that absolute path entirely.** Never concatenate it with the mount prefix — "
            "doing so produces a non-existent path.\n\n"
            "**Tool rules:**\n"
            f"- Writing files under `{mount}` **must** use `write_file` or `edit_file`.\n"
            "- **Never** use `bash` redirections (`cat >`, `echo >`, heredoc `<<EOF`) to write shared files.\n"
            "  Bash redirections are unreliable on some platforms and bypass version-control and lock mechanisms.\n"
            f"- Reading shared files uses `read_file`, with paths like `{mount}artifacts/docs/filename.md`.\n"
        )
        self.system_prompt_builder.add_section(
            PromptSection(
                name=self._SECTION_NAME,
                content={"cn": content_cn, "en": content_en},
                priority=66,
            )
        )


__all__ = ["EvalTeamRail"]
