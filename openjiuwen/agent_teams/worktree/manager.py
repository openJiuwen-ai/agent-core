# coding: utf-8

"""Worktree lifecycle manager.

Coordinates worktree creation, removal, session state, post-creation
setup, event publishing, and rail dispatch. This is the single business
logic entry point -- tools and spawn code delegate here.
"""

import fnmatch
import os
import shutil
import time
from collections.abc import Awaitable
from typing import (
    Any,
    Callable,
)

from openjiuwen.agent_teams.schema.events import (
    BaseEventMessage,
    TeamEvent,
    WorktreeCreatedEvent,
    WorktreeRemovedEvent,
)
from openjiuwen.agent_teams.worktree.backend import (
    create_backend,
    WorktreeBackend,
)
from openjiuwen.agent_teams.worktree.git import (
    _run_git,
    count_commits_since,
    find_canonical_git_root,
    get_current_branch,
    read_worktree_head_sha,
    status_porcelain,
    worktree_prune,
)
from openjiuwen.agent_teams.worktree.models import (
    WorktreeChangeSummary,
    WorktreeConfig,
    WorktreeCreateResult,
    WorktreeLifecyclePolicy,
    WorktreeSession,
)
from openjiuwen.agent_teams.worktree.session import (
    require_current_session,
    set_current_session,
)
from openjiuwen.agent_teams.worktree.slug import (
    validate_slug,
    worktree_path_for,
    worktrees_dir,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.sys_operation.cwd import get_cwd, get_workspace


class WorktreeManager:
    """Coordinates worktree lifecycle for a team.

    Responsibilities:
    - Create/remove worktrees via backend
    - Manage session state (ContextVar)
    - Post-creation setup (symlinks, config copy)
    - Publish events to messager
    - Change detection before removal
    - Rail dispatch for Phase 3 hooks
    """

    def __init__(
        self,
        config: WorktreeConfig,
        backend: WorktreeBackend | None = None,
        publish_event: Callable[[str, BaseEventMessage], Awaitable[None]] | None = None,
        rails: list[Any] | None = None,
        workspace_root: str | None = None,
    ):
        self._config = config
        self._backend = backend or create_backend("git", config)
        self._publish_event = publish_event
        self._rails = rails or []
        self._workspace_root = workspace_root

    @property
    def backend(self) -> WorktreeBackend:
        """Public accessor for the worktree backend."""
        return self._backend

    # -- Session-level worktree (tool calls) ----------------------------------

    async def enter(
        self,
        slug: str,
        *,
        member_name: str | None = None,
        team_name: str | None = None,
    ) -> WorktreeSession:
        """Create or recover a worktree and enter it.

        Sets the ContextVar session. Called by EnterWorktreeTool.

        Args:
            slug: Worktree name (validated for safety).
            member_name: Team member this worktree belongs to.
            team_name: Team this worktree belongs to.

        Returns:
            The active WorktreeSession.

        Raises:
            ValueError: If slug is invalid.
            RuntimeError: If not in a git repository.
        """
        validate_slug(slug)

        repo_root = await find_canonical_git_root(get_cwd())
        if not repo_root:
            raise RuntimeError("Cannot create worktree: not in a git repository")

        original_cwd = get_cwd()
        original_branch = await get_current_branch(repo_root)
        target_path = self._resolve_target_path(slug)

        start = time.monotonic()
        result = await self._backend.create(slug, repo_root, target_path)
        duration_ms = (time.monotonic() - start) * 1000

        if not result.existed:
            await self._post_creation_setup(repo_root, result.worktree_path)

        session = WorktreeSession(
            original_cwd=original_cwd,
            worktree_path=result.worktree_path,
            worktree_name=slug,
            worktree_branch=result.worktree_branch,
            original_branch=original_branch,
            original_head_commit=result.head_commit,
            member_name=member_name,
            team_name=team_name,
            hook_based=result.hook_based,
            creation_duration_ms=duration_ms,
            used_sparse_paths=bool(self._config.sparse_paths),
        )

        set_current_session(session)
        self._link_worktree_to_workspace(slug, result.worktree_path)

        team_logger.info(
            "Entered worktree '%s' at %s (%s, %.0fms)",
            slug,
            result.worktree_path,
            "recovered" if result.existed else "created",
            duration_ms,
        )

        if self._publish_event:
            await self._publish_event(
                TeamEvent.WORKTREE_CREATED,
                WorktreeCreatedEvent(
                    team_name=team_name or "",
                    member_name=member_name or "",
                    worktree_name=slug,
                    worktree_path=result.worktree_path,
                    existed=result.existed,
                ),
            )

        return session

    async def exit(
        self,
        action: str,
        *,
        discard_changes: bool = False,
    ) -> dict[str, str | None]:
        """Exit the current worktree session.

        Args:
            action: "keep" to preserve worktree, "remove" to delete it.
            discard_changes: Required True when action="remove" and
                worktree has uncommitted changes.

        Returns:
            Summary dict with action taken and metadata.

        Raises:
            RuntimeError: If no worktree session is active.
            ValueError: If action is "remove" and there are unsaved
                changes without discard_changes=True.
        """
        session = require_current_session()

        if action == "remove" and not discard_changes:
            summary = await self.count_changes(session)
            if summary and (summary.changed_files > 0 or summary.commits > 0):
                parts = []
                if summary.changed_files > 0:
                    parts.append(f"{summary.changed_files} uncommitted files")
                if summary.commits > 0:
                    parts.append(f"{summary.commits} commits on {session.worktree_branch}")
                raise ValueError(
                    f"Worktree has {' and '.join(parts)}. "
                    f"Set discard_changes=True to proceed."
                )

        repo_root = await find_canonical_git_root(session.original_cwd)

        if action == "keep":
            set_current_session(None)
            team_logger.info(
                "Kept worktree '%s' at %s",
                session.worktree_name,
                session.worktree_path,
            )
            return {
                "action": "keep",
                "original_cwd": session.original_cwd,
                "worktree_path": session.worktree_path,
                "worktree_branch": session.worktree_branch,
            }

        # action == "remove"
        if repo_root:
            await self._remove_worktree_and_unlink(
                session.worktree_path,
                repo_root,
                slug=session.worktree_name,
            )
        else:
            # No git root -- still drop the workspace symlink so it
            # doesn't dangle against the already-gone worktree path.
            self._unlink_worktree_from_workspace(session.worktree_name)

        set_current_session(None)

        if self._publish_event:
            await self._publish_event(
                TeamEvent.WORKTREE_REMOVED,
                WorktreeRemovedEvent(
                    team_name=session.team_name or "",
                    member_name=session.member_name or "",
                    worktree_name=session.worktree_name,
                    worktree_path=session.worktree_path,
                ),
            )

        team_logger.info(
            "Removed worktree '%s' at %s",
            session.worktree_name,
            session.worktree_path,
        )
        return {
            "action": "remove",
            "original_cwd": session.original_cwd,
            "worktree_path": session.worktree_path,
            "worktree_branch": session.worktree_branch,
        }

    # -- Agent worktree (member isolation) ------------------------------------

    async def create_agent_worktree(self, slug: str) -> WorktreeCreateResult:
        """Create a lightweight worktree for a team member.

        Unlike enter(), this does NOT modify the ContextVar session
        or change process cwd. The caller is responsible for passing
        the worktree_path to the spawned member.

        Used by spawn logic to give each subprocess member its own
        working copy.

        Args:
            slug: Worktree name (validated for safety).

        Returns:
            WorktreeCreateResult with path and metadata.

        Raises:
            ValueError: If slug is invalid.
            RuntimeError: If not in a git repository.
        """
        validate_slug(slug)

        repo_root = await find_canonical_git_root(get_cwd())
        if not repo_root:
            raise RuntimeError("Cannot create agent worktree: not in a git repository")

        target_path = self._resolve_target_path(slug)
        result = await self._backend.create(slug, repo_root, target_path)

        if not result.existed:
            await self._post_creation_setup(repo_root, result.worktree_path)
        else:
            # Touch mtime to prevent cleanup
            now = time.time()
            os.utime(result.worktree_path, (now, now))

        self._link_worktree_to_workspace(slug, result.worktree_path)
        return result

    # -- Workspace link management ---------------------------------------------

    def _link_worktree_to_workspace(self, slug: str, worktree_path: str) -> None:
        """Create .worktree/{slug} symlink in workspace if workspace_root is set.

        Replaces any pre-existing symlink at the target path -- stale
        links from previous sessions (e.g. broken links pointing at a
        removed worktree) would otherwise block ``os.symlink`` with
        ``FileExistsError``.
        """
        if not self._workspace_root:
            return
        wt_dir = os.path.join(self._workspace_root, ".worktree")
        os.makedirs(wt_dir, exist_ok=True)
        link = os.path.join(wt_dir, slug)
        if os.path.lexists(link):
            if os.path.islink(link):
                os.unlink(link)
            else:
                team_logger.warning(
                    "Workspace worktree link path '%s' exists and is not a symlink -- skipping",
                    link,
                )
                return
        os.symlink(worktree_path, link, target_is_directory=True)
        team_logger.debug("Linked worktree '%s' into workspace at %s", slug, link)

    def _unlink_worktree_from_workspace(self, slug: str) -> None:
        """Remove .worktree/{slug} symlink from workspace if it exists."""
        if not self._workspace_root:
            return
        link = os.path.join(self._workspace_root, ".worktree", slug)
        if os.path.islink(link):
            os.unlink(link)
            team_logger.debug("Unlinked worktree '%s' from workspace", slug)

    async def _remove_worktree_and_unlink(
        self,
        wt_path: str,
        repo_root: str,
        *,
        slug: str | None = None,
    ) -> bool:
        """Remove a worktree via backend and clean up its workspace symlink.

        Single choke point for worktree teardown: callers must use this
        method instead of calling ``backend.remove`` directly so that
        the ``{team_ws}/.worktree/{slug}`` symlink stays in lockstep
        with the worktree directory.

        Args:
            wt_path: Absolute path to the worktree directory.
            repo_root: Git root that owns the worktree.
            slug: Worktree slug; defaults to ``basename(wt_path)``.

        Returns:
            True if the backend successfully removed the worktree.
        """
        ok = await self._backend.remove(wt_path, repo_root)
        resolved_slug = slug or os.path.basename(wt_path.rstrip("/"))
        if resolved_slug:
            self._unlink_worktree_from_workspace(resolved_slug)
        return ok

    # -- Change detection -----------------------------------------------------

    async def count_changes(
        self,
        session: WorktreeSession,
    ) -> WorktreeChangeSummary | None:
        """Count uncommitted changes and new commits.

        Args:
            session: The worktree session to inspect.

        Returns:
            WorktreeChangeSummary, or None if state cannot be determined
            (fail-closed).
        """
        changes = await status_porcelain(session.worktree_path)
        changed_files = len(changes)

        if not session.original_head_commit:
            return None

        commits = await count_commits_since(
            session.original_head_commit,
            session.worktree_path,
        )
        if commits is None:
            return None

        return WorktreeChangeSummary(
            changed_files=changed_files,
            commits=commits,
        )

    # -- Post-creation setup --------------------------------------------------

    async def _post_creation_setup(
        self,
        repo_root: str,
        worktree_path: str,
    ) -> None:
        """Post-creation setup for new worktrees.

        1. Symlink configured directories
        2. Copy gitignored include files
        3. Configure git hooks path

        Args:
            repo_root: Repository root directory.
            worktree_path: Newly created worktree directory.
        """
        # 1. Symlink directories
        dirs = self._config.symlink_directories or []
        for d in dirs:
            if ".." in d or d.startswith("/"):
                team_logger.warning("Skipping symlink for '%s': path traversal detected", d)
                continue
            src = os.path.join(repo_root, d)
            dst = os.path.join(worktree_path, d)
            try:
                os.symlink(src, dst, target_is_directory=True)
                team_logger.debug("Symlinked %s to worktree", d)
            except FileExistsError:
                pass
            except FileNotFoundError:
                pass
            except OSError as e:
                team_logger.warning("Failed to symlink %s: %s", d, e)

        # 2. Copy gitignored include files
        patterns = self._config.include_patterns
        if patterns:
            await self._copy_include_files(repo_root, worktree_path, patterns)

        # 3. Configure hooks path
        await self._configure_hooks_path(repo_root, worktree_path)

    async def _copy_include_files(
        self,
        repo_root: str,
        worktree_path: str,
        patterns: list[str],
    ) -> list[str]:
        """Copy gitignored files matching include patterns to worktree.

        Uses ``git ls-files --others --ignored --exclude-standard --directory``
        for efficient listing, then applies pattern matching and copies.

        Args:
            repo_root: Repository root directory.
            worktree_path: Target worktree directory.
            patterns: Glob patterns for files to include.

        Returns:
            List of relative paths that were copied.
        """
        r = await _run_git(
            ["ls-files", "--others", "--ignored", "--exclude-standard", "--directory"],
            cwd=repo_root,
        )
        if not r.ok or not r.stdout:
            return []

        entries = [e for e in r.stdout.splitlines() if e]
        copied: list[str] = []

        for entry in entries:
            if entry.endswith("/"):
                continue
            if any(fnmatch.fnmatch(entry, p) for p in patterns):
                src = os.path.join(repo_root, entry)
                dst = os.path.join(worktree_path, entry)
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    copied.append(entry)
                except OSError as e:
                    team_logger.warning("Failed to copy %s: %s", entry, e)

        return copied

    async def _configure_hooks_path(
        self,
        repo_root: str,
        worktree_path: str,
    ) -> None:
        """Configure core.hooksPath to resolve relative hook paths.

        Husky and similar tools use relative paths that break in worktrees.
        Point to the main repo's hooks directory.

        Args:
            repo_root: Repository root directory.
            worktree_path: Worktree directory to configure.
        """
        for candidate in (
            os.path.join(repo_root, ".husky"),
            os.path.join(repo_root, ".git", "hooks"),
        ):
            if os.path.isdir(candidate):
                await _run_git(
                    ["config", "core.hooksPath", candidate],
                    cwd=worktree_path,
                )
                team_logger.debug("Configured worktree hooks path: %s", candidate)
                return

    # -- Persistent team recovery (Section 21.4) ------------------------------

    async def recover_worktree_for_member(
        self,
        member_name: str,
        team_name: str,
    ) -> WorktreeSession | None:
        """Recover an existing worktree session for a persistent team member.

        Looks up the member's worktree by slug pattern, validates it still
        exists, and restores the session state.

        Called during resume_persistent_team() for each re-launched member.

        Args:
            member_name: Team member identifier.
            team_name: Team identifier.

        Returns:
            Recovered WorktreeSession, or None if worktree was cleaned up.
        """
        slug = self._member_slug(member_name)
        repo_root = await find_canonical_git_root(get_cwd())
        if not repo_root:
            return None

        wt_path = self._resolve_target_path(slug)
        head_sha = await read_worktree_head_sha(wt_path)
        if not head_sha:
            return None

        branch = await get_current_branch(wt_path)
        return WorktreeSession(
            original_cwd=repo_root,
            worktree_path=wt_path,
            worktree_name=slug,
            worktree_branch=branch,
            original_head_commit=head_sha,
            member_name=member_name,
            team_name=team_name,
            lifecycle_policy=self._resolve_policy(),
        )

    # -- Team cleanup (Section 21.5) ------------------------------------------

    async def cleanup_team_worktrees(
        self,
        team_name: str,
        *,
        force: bool = False,
    ) -> list[str]:
        """Clean up all worktrees belonging to a team.

        Called by CleanTeamTool or TeamAgent shutdown for TEMPORARY teams.
        For DURABLE policy, requires force=True to proceed.

        Args:
            team_name: Team identifier.
            force: If True, remove even durable worktrees with changes.

        Returns:
            List of removed worktree paths.
        """
        policy = self._resolve_policy()
        if policy == WorktreeLifecyclePolicy.DURABLE and not force:
            team_logger.info(
                "Skipping worktree cleanup for team %s: durable policy active",
                team_name,
            )
            return []

        repo_root = await find_canonical_git_root(get_cwd())
        if not repo_root:
            return []

        workspace = get_workspace()
        if workspace is None:
            team_logger.info(
                "Skipping worktree cleanup for team %s: agent workspace not set",
                team_name,
            )
            return []
        wt_dir = worktrees_dir(workspace)
        try:
            entries = os.listdir(wt_dir)
        except FileNotFoundError:
            return []

        removed: list[str] = []
        for slug in entries:
            if not slug.startswith("teammate-"):
                continue
            wt_path = os.path.join(wt_dir, slug)

            if not force:
                summary = await self._check_changes(wt_path)
                if summary and (summary.changed_files > 0 or summary.commits > 0):
                    team_logger.warning("Skipping worktree '%s': has uncommitted changes", slug)
                    continue

            if await self._remove_worktree_and_unlink(wt_path, repo_root, slug=slug):
                removed.append(wt_path)

        if removed:
            await worktree_prune(repo_root)

        return removed

    async def remove_worktree(self, worktree_path: str, repo_root: str) -> bool:
        """Remove a single worktree by path.

        Args:
            worktree_path: Absolute path to the worktree directory.
            repo_root: Root of the repository that owns the worktree.

        Returns:
            True if the worktree was successfully removed.
        """
        return await self._remove_worktree_and_unlink(worktree_path, repo_root)

    # -- Internal helpers -----------------------------------------------------

    def _resolve_target_path(self, slug: str) -> str:
        """Compute the worktree filesystem path for ``slug``.

        Reads the agent workspace from the current ContextVar (set by
        ``DeepAgent._ensure_initialized``).  Worktrees live under the
        owning DeepAgent's workspace, not the source git repo.

        Args:
            slug: Validated worktree slug.

        Returns:
            Absolute path to the worktree directory.

        Raises:
            RuntimeError: If the agent workspace is not set in the
                current context.  Worktree creation requires a
                workspace to be configured on the DeepAgent.
        """
        workspace = get_workspace()
        if workspace is None:
            raise RuntimeError(
                "Cannot resolve worktree path: DeepAgent workspace is not set. "
                "Worktrees must be created from an agent that has a workspace "
                "configured (init_cwd was called with workspace=...)."
            )
        return worktree_path_for(workspace, slug)

    @staticmethod
    def _resolve_target_path(slug: str) -> str:
        """Compute the worktree filesystem path for ``slug``.

        Reads the agent workspace from the current ContextVar (set by
        ``DeepAgent._ensure_initialized``).  Worktrees live under the
        owning DeepAgent's workspace, not the source git repo.

        Args:
            slug: Validated worktree slug.

        Returns:
            Absolute path to the worktree directory.

        Raises:
            RuntimeError: If the agent workspace is not set in the
                current context.  Worktree creation requires a
                workspace to be configured on the DeepAgent.
        """
        workspace = get_workspace()
        if workspace is None:
            raise RuntimeError(
                "Cannot resolve worktree path: DeepAgent workspace is not set. "
                "Worktrees must be created from an agent that has a workspace "
                "configured (init_cwd was called with workspace=...)."
            )
        return worktree_path_for(workspace, slug)

    @staticmethod
    def _member_slug(member_name: str) -> str:
        """Derive worktree slug from member ID.

        Args:
            member_name: Team member identifier.

        Returns:
            Slug in the format "teammate-<first 8 chars>".
        """
        return f"teammate-{member_name[:8]}"

    def _resolve_policy(self) -> WorktreeLifecyclePolicy:
        """Resolve the effective lifecycle policy.

        Returns:
            The resolved WorktreeLifecyclePolicy.
        """
        if self._config.lifecycle_policy != WorktreeLifecyclePolicy.AUTO:
            return self._config.lifecycle_policy
        return WorktreeLifecyclePolicy.EPHEMERAL

    async def _fire_rail(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Invoke a rail hook method on all registered rails.

        Returns the last non-None result (for hooks that can modify values).

        Args:
            method: Hook method name to call.
            *args: Positional arguments for the hook.
            **kwargs: Keyword arguments for the hook.

        Returns:
            Last non-None result from any rail, or None.
        """
        result = None
        for rail in self._rails:
            handler = getattr(rail, method, None)
            if handler:
                r = await handler(*args, **kwargs)
                if r is not None:
                    result = r
        return result

    async def _check_changes(self, wt_path: str) -> WorktreeChangeSummary | None:
        """Check for uncommitted changes in a worktree path.

        Args:
            wt_path: Absolute path to the worktree.

        Returns:
            WorktreeChangeSummary, or None if check fails.
        """
        changes = await status_porcelain(wt_path)
        return WorktreeChangeSummary(changed_files=len(changes), commits=0)
