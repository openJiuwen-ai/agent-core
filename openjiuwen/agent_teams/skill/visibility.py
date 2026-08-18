# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-scoped Skill visibility declarations.

Skills live in exactly one physical library on disk
(``openjiuwen.agent_teams.paths.global_skills_dir``). Inside a team, which
member may see which Skill is expressed as a *declaration* next to each
workspace instead of as a materialized directory view (no symlink farms, no
per-member copies).

Layout of one declaration file (``skills-visibility.json``)::

    {
      "version": 1,
      "scope": "member",
      "id": "reviewer",
      "bootstrapped_from": "config:agents.teammate.skills",
      "authority": 0,
      "allow": [],
      "deny": []
    }

Semantics:

* ``allow`` empty means "inherit the whole library". It is *not* "deny
  everything" — that matches the Skill rail's allow-list filter, which treats
  an empty allow-list as "no allow filtering".
* ``deny`` always wins over ``allow``.
* The file is the authority. Configuration (``config.agents.<role>.skills``)
  only seeds it once, through :func:`bootstrap_skill_visibility`; later config
  edits never overwrite an existing file.
* ``authority`` ranks *who* wrote the lists, so seeding is order-independent
  rather than first-writer-wins: a seed replaces a document written by a
  strictly lower authority and is otherwise a no-op. See
  :data:`AUTHORITY_SEED`, :data:`AUTHORITY_MIGRATION` and
  :data:`AUTHORITY_EXPLICIT`.

A member's effective view is composed from its own declaration, its team's
declaration and the globally disabled Skill names; see
:func:`compose_skill_visibility`.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Protocol, runtime_checkable

from openjiuwen.agent_teams.skill.file_lock import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    cross_process_file_lock,
)
from openjiuwen.core.common.logging import logger

SKILL_VISIBILITY_SCHEMA_VERSION = 1

SCOPE_MEMBER = "member"
SCOPE_TEAM = "team"

# Reported as the mtime of a declaration file that does not exist yet, so that
# creating or deleting the file changes the provider signature.
MISSING_METADATA_MTIME = -1.0

# Authority ranks recorded in a document, ordered from weakest to strongest.
# A seed only replaces a document whose recorded authority is *strictly lower*
# than its own, which makes the outcome independent of the order in which the
# seeding code paths happen to run.
#
# AUTHORITY_SEED
#     A default or configuration-derived seed (``config.agents.<role>.skills``,
#     the empty team seed written at assembly). It knows nothing about what the
#     workspace was actually allowed to see before.
# AUTHORITY_MIGRATION
#     A value derived from the workspace's own observed prior state — today the
#     legacy ``skills/`` view directory a workspace carried before Skills were
#     single-sourced. It describes reality, so it outranks any default seed no
#     matter which one reaches the file first.
# AUTHORITY_EXPLICIT
#     An explicit authorization call (:func:`set_skill_visibility`,
#     :func:`update_skill_visibility`). Never replaced by any seed.
AUTHORITY_SEED = 0
AUTHORITY_MIGRATION = 10
AUTHORITY_EXPLICIT = 100


class StatToken(NamedTuple):
    """Change-detection token for one declaration file.

    ``mtime`` alone is not enough: filesystems with coarse timestamp
    granularity (SMB shares, FAT-formatted volumes) can report an unchanged
    mtime for two writes inside the same tick, which would let a revocation go
    unnoticed. Size and inode are taken from the same ``stat`` call, so the
    stronger token costs nothing extra.

    Attributes:
        mtime_ns: Modification time in nanoseconds, or -1 when absent.
        size: File size in bytes, or -1 when absent.
        inode: Inode number, or -1 when absent. Detects an atomic replace that
            preserved both timestamp and length.
        mtime: Modification time in seconds, reported to signature consumers.
    """

    mtime_ns: int
    size: int
    inode: int
    mtime: float


def normalize_skill_names(names: Iterable[str] | None) -> list[str]:
    """Normalize a raw Skill-name collection into a sorted unique list.

    Blank entries and non-string entries are dropped so a hand-edited or
    RPC-supplied list can never inject empty names into the declaration file.

    Args:
        names: Raw Skill names, or None.

    Returns:
        Sorted list of unique, stripped, non-empty Skill names.
    """
    if names is None:
        return []
    collected: set[str] = set()
    for item in names:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            collected.add(text)
    return sorted(collected)


@dataclass
class SkillVisibility:
    """One workspace's Skill visibility declaration.

    Attributes:
        version: Schema version of the on-disk document.
        scope: Either ``member`` or ``team``.
        id: Member name for a member scope, team name for a team scope.
        bootstrapped_from: Provenance marker recorded when the file was seeded,
            e.g. ``config:agents.teammate.skills`` or ``migration:symlinks``.
            None when the file was created by an explicit authorization call.
            Purely informational: seeding decisions read ``authority``, never
            this string.
        authority: Rank of the writer that last set ``allow`` / ``deny``, one of
            :data:`AUTHORITY_SEED`, :data:`AUTHORITY_MIGRATION` or
            :data:`AUTHORITY_EXPLICIT`. A seed may only overwrite a document
            recording a strictly lower rank.
        allow: Allow-list of Skill names. Empty means "inherit everything".
        deny: Deny-list of Skill names. Always wins over ``allow``.
    """

    version: int = SKILL_VISIBILITY_SCHEMA_VERSION
    scope: str = SCOPE_MEMBER
    id: str = ""
    bootstrapped_from: str | None = None
    authority: int = AUTHORITY_SEED
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)

    @property
    def is_unrestricted(self) -> bool:
        """Return True when this document imposes no restriction at all."""
        return not self.allow and not self.deny

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serializable document for this declaration."""
        return {
            "version": self.version,
            "scope": self.scope,
            "id": self.id,
            "bootstrapped_from": self.bootstrapped_from,
            "authority": self.authority,
            "allow": list(self.allow),
            "deny": list(self.deny),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        scope: str,
        entity_id: str,
    ) -> "SkillVisibility":
        """Parse a declaration document, tolerating partial or odd values.

        Unknown or malformed fields degrade to their permissive defaults rather
        than raising: a damaged document must never strip an agent of every
        Skill.

        Args:
            payload: Decoded JSON object.
            scope: Expected scope, used when the document omits it.
            entity_id: Expected member/team id, used when the document omits it.

        Returns:
            The parsed declaration.
        """
        raw_version = payload.get("version")
        version = raw_version if isinstance(raw_version, int) else SKILL_VISIBILITY_SCHEMA_VERSION

        raw_scope = payload.get("scope")
        parsed_scope = raw_scope.strip() if isinstance(raw_scope, str) and raw_scope.strip() else scope

        raw_id = payload.get("id")
        parsed_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else entity_id

        raw_source = payload.get("bootstrapped_from")
        parsed_source = raw_source if isinstance(raw_source, str) and raw_source.strip() else None

        return cls(
            version=version,
            scope=parsed_scope,
            id=parsed_id,
            bootstrapped_from=parsed_source,
            authority=_parse_authority(payload.get("authority"), parsed_source),
            allow=normalize_skill_names(_as_name_iterable(payload.get("allow"))),
            deny=normalize_skill_names(_as_name_iterable(payload.get("deny"))),
        )


def _parse_authority(raw: Any, bootstrapped_from: str | None) -> int:
    """Derive the authority rank of a document being read.

    Documents written before the field existed carry no ``authority``. They are
    classified by their provenance marker: a seeded document ranks as
    :data:`AUTHORITY_SEED`, while a document with no marker was produced by an
    explicit authorization call and ranks as :data:`AUTHORITY_EXPLICIT`. That
    keeps an upgrade from silently making an existing authorization replaceable.

    Args:
        raw: Value of the ``authority`` key, possibly absent or malformed.
        bootstrapped_from: Provenance marker already parsed from the document.

    Returns:
        The document's authority rank.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        return AUTHORITY_SEED if bootstrapped_from is not None else AUTHORITY_EXPLICIT
    return raw


def _as_name_iterable(raw: Any) -> Iterable[str] | None:
    """Coerce a raw JSON value into an iterable of names, or None."""
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple, set)):
        return [item for item in raw if isinstance(item, str)]
    return None


def read_skill_visibility(
    path: str | Path,
    *,
    scope: str,
    entity_id: str,
) -> SkillVisibility:
    """Read one declaration file, degrading to a fully permissive document.

    Reads take no lock: writers land their bytes through ``os.replace``, so a
    reader either sees the previous document or the next one, never a partial
    file. A missing file, an unreadable file and a corrupt file all yield an
    empty (unrestricted) document, because losing the declaration must not
    leave an agent with zero Skills.

    Args:
        path: Declaration file path.
        scope: ``member`` or ``team``, used to fill an incomplete document.
        entity_id: Member or team id, used to fill an incomplete document.

    Returns:
        The parsed declaration, or an unrestricted document.
    """
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        return SkillVisibility(scope=scope, id=entity_id)

    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "[SkillVisibility] failed to read declaration '%s': %s; falling back to unrestricted visibility",
            resolved,
            exc,
        )
        return SkillVisibility(scope=scope, id=entity_id)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(
            "[SkillVisibility] corrupt declaration '%s': %s; falling back to unrestricted visibility",
            resolved,
            exc,
        )
        return SkillVisibility(scope=scope, id=entity_id)

    if not isinstance(payload, dict):
        logger.warning(
            "[SkillVisibility] declaration '%s' is not a JSON object; falling back to unrestricted visibility",
            resolved,
        )
        return SkillVisibility(scope=scope, id=entity_id)

    return SkillVisibility.from_dict(payload, scope=scope, entity_id=entity_id)


def write_skill_visibility(
    path: str | Path,
    visibility: SkillVisibility,
    *,
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> None:
    """Write one declaration file atomically under the cross-process lock.

    Args:
        path: Declaration file path.
        visibility: Document to persist.
        timeout: Seconds to wait for the file lock.

    Raises:
        FileLockTimeout: Another process held the lock past ``timeout``.
        OSError: The document could not be written.
    """
    resolved = Path(path).expanduser()
    with cross_process_file_lock(resolved, timeout=timeout):
        _write_atomic(resolved, visibility)


def bootstrap_skill_visibility(
    path: str | Path,
    *,
    scope: str,
    entity_id: str,
    allow: Iterable[str] | None,
    bootstrapped_from: str,
    authority: int = AUTHORITY_SEED,
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> SkillVisibility:
    """Seed a declaration file, never demoting what a stronger writer put there.

    The file is the authority for visibility. Configuration only provides the
    initial allow-list: an existing document written at the same or a higher
    rank is returned untouched, so a later authorization change is never
    reverted by a stale config value.

    A seed carrying a *higher* ``authority`` than the stored one replaces the
    allow-list instead of standing down. That is what makes the outcome
    independent of call order: a migration-derived allow-list wins over a
    default seed whether it lands first or second. The stored ``deny`` list is
    always preserved, because dropping a revocation could only ever widen
    access.

    Args:
        path: Declaration file path.
        scope: ``member`` or ``team``.
        entity_id: Member or team id.
        allow: Initial allow-list. Empty or None seeds an unrestricted document.
        bootstrapped_from: Provenance marker recorded in the seeded document,
            e.g. ``config:agents.teammate.skills``.
        authority: Rank of this seed; defaults to :data:`AUTHORITY_SEED`.
        timeout: Seconds to wait for the file lock.

    Returns:
        The stored document when it was written at an equal or higher rank,
        otherwise the freshly seeded one.
    """
    resolved = Path(path).expanduser()
    with cross_process_file_lock(resolved, timeout=timeout):
        deny: list[str] = []
        if resolved.is_file():
            current = read_skill_visibility(resolved, scope=scope, entity_id=entity_id)
            if current.authority >= authority:
                return current
            deny = list(current.deny)
            logger.info(
                "[SkillVisibility] '%s' reseeded from %s (authority %d -> %d)",
                resolved,
                bootstrapped_from,
                current.authority,
                authority,
            )

        seeded = SkillVisibility(
            scope=scope,
            id=entity_id,
            bootstrapped_from=bootstrapped_from,
            authority=authority,
            allow=normalize_skill_names(allow),
            deny=deny,
        )
        _write_atomic(resolved, seeded)
        return seeded


def set_skill_visibility(
    path: str | Path,
    *,
    scope: str,
    entity_id: str,
    allow: Iterable[str] | None,
    deny: Iterable[str] | None,
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> SkillVisibility:
    """Replace both lists of one declaration file atomically.

    The document is stamped with :data:`AUTHORITY_EXPLICIT`, which seals it
    against every later seed: an operator decision outranks any configuration
    or migration default. The ``bootstrapped_from`` marker is left as it is, so
    the document still shows where it originally came from.

    Args:
        path: Declaration file path.
        scope: ``member`` or ``team``.
        entity_id: Member or team id.
        allow: New allow-list; None or empty clears it (inherit everything).
        deny: New deny-list; None or empty clears it.
        timeout: Seconds to wait for the file lock.

    Returns:
        The persisted document.
    """
    resolved = Path(path).expanduser()
    with cross_process_file_lock(resolved, timeout=timeout):
        current = read_skill_visibility(resolved, scope=scope, entity_id=entity_id)
        current.allow = normalize_skill_names(allow)
        current.deny = normalize_skill_names(deny)
        current.authority = AUTHORITY_EXPLICIT
        _write_atomic(resolved, current)
        return current


def update_skill_visibility(
    path: str | Path,
    *,
    scope: str,
    entity_id: str,
    add_allow: Iterable[str] | None = None,
    remove_allow: Iterable[str] | None = None,
    add_deny: Iterable[str] | None = None,
    remove_deny: Iterable[str] | None = None,
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> SkillVisibility:
    """Apply incremental grants/revocations under a single lock.

    Read-modify-write happens inside one lock acquisition so two concurrent
    authorization calls cannot lose each other's change. As with
    :func:`set_skill_visibility`, the document is stamped with
    :data:`AUTHORITY_EXPLICIT` and is from then on out of reach of every seed.

    Args:
        path: Declaration file path.
        scope: ``member`` or ``team``.
        entity_id: Member or team id.
        add_allow: Skill names to add to the allow-list.
        remove_allow: Skill names to drop from the allow-list.
        add_deny: Skill names to add to the deny-list.
        remove_deny: Skill names to drop from the deny-list.
        timeout: Seconds to wait for the file lock.

    Returns:
        The persisted document after the deltas were applied.
    """
    resolved = Path(path).expanduser()
    with cross_process_file_lock(resolved, timeout=timeout):
        current = read_skill_visibility(resolved, scope=scope, entity_id=entity_id)
        allow = set(current.allow) | set(normalize_skill_names(add_allow))
        allow -= set(normalize_skill_names(remove_allow))
        deny = set(current.deny) | set(normalize_skill_names(add_deny))
        deny -= set(normalize_skill_names(remove_deny))
        current.allow = normalize_skill_names(allow)
        current.deny = normalize_skill_names(deny)
        current.authority = AUTHORITY_EXPLICIT
        _write_atomic(resolved, current)
        return current


def compose_skill_visibility(
    member: SkillVisibility,
    team: SkillVisibility | None,
    global_disabled: Iterable[str] | None,
) -> tuple[set[str], set[str]]:
    """Compose the effective allow/deny sets fed to the team's Skill rail.

    Rules::

        enabled  = member.allow UNION team.allow
        disabled = member.deny UNION team.deny UNION global_disabled

    An empty ``enabled`` set is returned as-is on purpose: the Skill rail reads
    an empty allow-list as "do not filter by allow-list", i.e. the whole library
    stays visible. Turning it into the full Skill-name set here would freeze the
    view against later library additions.

    Args:
        member: The member's declaration.
        team: The team's declaration, or None for a member outside a team.
        global_disabled: Skill names disabled process-wide (platform kill
            switch), or None.

    Returns:
        The ``(enabled_skills, disabled_skills)`` pair.
    """
    enabled = set(member.allow)
    disabled = set(member.deny)
    if team is not None:
        enabled |= set(team.allow)
        disabled |= set(team.deny)
    disabled |= set(normalize_skill_names(global_disabled))
    return enabled, disabled


@runtime_checkable
class SkillVisibilityProvider(Protocol):
    """Recomputes a rail's effective Skill allow/deny sets on demand."""

    def __call__(self) -> tuple[set[str], set[str]]:
        """Return the current ``(enabled_skills, disabled_skills)`` pair."""
        ...

    def metadata_signature(self) -> tuple[tuple[str, float], ...]:
        """Return ``(path, mtime)`` pairs for the files backing this provider."""
        ...


class FileSkillVisibilityProvider:
    """Provider backed by member/team declarations plus a global deny loader.

    Every call re-stats the declaration files, so an authorization change lands
    on the next agent turn without restarting anything. The composition result
    is memoized against the file mtimes and the global deny-list, so the common
    "nothing changed" path costs two ``stat`` calls instead of two JSON parses.
    """

    def __init__(
        self,
        *,
        member_path: str | Path,
        member_id: str,
        team_path: str | Path | None = None,
        team_id: str = "",
        global_disabled_loader: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        """Initialize the provider.

        Args:
            member_path: Path of the member declaration file.
            member_id: Member name recorded in a seeded/repaired document.
            team_path: Path of the team declaration file, or None when the agent
                belongs to no team.
            team_id: Team name recorded in a seeded/repaired document.
            global_disabled_loader: Zero-arg callable returning the process-wide
                disabled Skill names, or None for "nothing globally disabled".
        """
        self._member_path = Path(member_path).expanduser()
        self._member_id = member_id
        self._team_path = Path(team_path).expanduser() if team_path is not None else None
        self._team_id = team_id
        self._global_disabled_loader = global_disabled_loader
        self._cache_key: tuple[Any, ...] | None = None
        self._cached_enabled: frozenset[str] = frozenset()
        self._cached_disabled: frozenset[str] = frozenset()

    @property
    def member_path(self) -> Path:
        """Return the member declaration file path."""
        return self._member_path

    @property
    def team_path(self) -> Path | None:
        """Return the team declaration file path, or None."""
        return self._team_path

    def _stat_tokens(self) -> tuple[tuple[str, StatToken], ...]:
        """Return the ``(path, token)`` pair of every backing declaration file."""
        entries: list[tuple[str, StatToken]] = [
            (str(self._member_path), _stat_token(self._member_path)),
        ]
        if self._team_path is not None:
            entries.append((str(self._team_path), _stat_token(self._team_path)))
        return tuple(entries)

    def metadata_signature(self) -> tuple[tuple[str, float], ...]:
        """Return ``(path, mtime)`` pairs for the backing declaration files.

        A missing file reports :data:`MISSING_METADATA_MTIME` so that creating
        or deleting it still moves the signature. This is a coarse public view
        of the internal :class:`StatToken`; correctness of the memoization does
        not depend on it.
        """
        return tuple((path, token.mtime) for path, token in self._stat_tokens())

    def __call__(self) -> tuple[set[str], set[str]]:
        """Recompute the effective ``(enabled_skills, disabled_skills)`` pair."""
        global_disabled = tuple(normalize_skill_names(self._load_global_disabled()))
        cache_key = (self._stat_tokens(), global_disabled)
        if cache_key == self._cache_key:
            return set(self._cached_enabled), set(self._cached_disabled)

        member = read_skill_visibility(
            self._member_path,
            scope=SCOPE_MEMBER,
            entity_id=self._member_id,
        )
        team = None
        if self._team_path is not None:
            team = read_skill_visibility(
                self._team_path,
                scope=SCOPE_TEAM,
                entity_id=self._team_id,
            )

        enabled, disabled = compose_skill_visibility(member, team, global_disabled)
        self._cached_enabled = frozenset(enabled)
        self._cached_disabled = frozenset(disabled)
        self._cache_key = cache_key
        return enabled, disabled

    def _load_global_disabled(self) -> list[str]:
        """Load the process-wide disabled Skill names, never raising."""
        if self._global_disabled_loader is None:
            return []
        try:
            return list(self._global_disabled_loader())
        except Exception as exc:
            logger.warning(
                "[SkillVisibility] global disabled-Skill loader failed: %s; treating it as empty",
                exc,
            )
            return []


class StaticSkillVisibilityProvider:
    """Fixed-value provider, for tests and callers with no declaration files."""

    def __init__(
        self,
        *,
        enabled: Iterable[str] | None = None,
        disabled: Iterable[str] | None = None,
    ) -> None:
        """Initialize the provider with the sets it will always return.

        Args:
            enabled: Allow-list; empty means "inherit everything".
            disabled: Deny-list.
        """
        self._enabled = frozenset(normalize_skill_names(enabled))
        self._disabled = frozenset(normalize_skill_names(disabled))

    @staticmethod
    def metadata_signature() -> tuple[tuple[str, float], ...]:
        """Return an empty signature: nothing on disk backs this provider."""
        return ()

    def __call__(self) -> tuple[set[str], set[str]]:
        """Return the configured ``(enabled_skills, disabled_skills)`` pair."""
        return set(self._enabled), set(self._disabled)


def build_skill_visibility_provider(
    *,
    member_path: str | Path,
    member_id: str,
    team_path: str | Path | None = None,
    team_id: str = "",
    global_disabled_loader: Callable[[], Iterable[str]] | None = None,
) -> FileSkillVisibilityProvider:
    """Build the provider a team Skill rail uses to refresh its Skill view.

    Args:
        member_path: Path of the member declaration file.
        member_id: Member name.
        team_path: Path of the team declaration file, or None.
        team_id: Team name.
        global_disabled_loader: Zero-arg callable returning process-wide
            disabled Skill names, or None.

    Returns:
        A callable provider that also exposes ``metadata_signature()``.
    """
    return FileSkillVisibilityProvider(
        member_path=member_path,
        member_id=member_id,
        team_path=team_path,
        team_id=team_id,
        global_disabled_loader=global_disabled_loader,
    )


def _stat_token(path: Path) -> StatToken:
    """Return the change-detection token of a declaration file.

    A missing or unreadable file yields the all-absent token, so creating or
    deleting the file still moves the value.
    """
    try:
        info = path.stat()
    except OSError:
        return StatToken(mtime_ns=-1, size=-1, inode=-1, mtime=MISSING_METADATA_MTIME)
    return StatToken(
        mtime_ns=info.st_mtime_ns,
        size=info.st_size,
        inode=info.st_ino,
        mtime=info.st_mtime,
    )


def _write_atomic(path: Path, visibility: SkillVisibility) -> None:
    """Serialize the document to a sibling temp file, then rename it into place.

    ``os.replace`` is atomic on POSIX and on Windows, so a concurrent reader
    always observes one complete document. The temp file is created in the same
    directory to keep the rename on one filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(visibility.to_dict(), ensure_ascii=False, indent=2) + "\n"

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    fd_handed_off = False
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        fd_handed_off = True
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if not fd_handed_off:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


__all__ = [
    "AUTHORITY_EXPLICIT",
    "AUTHORITY_MIGRATION",
    "AUTHORITY_SEED",
    "MISSING_METADATA_MTIME",
    "SCOPE_MEMBER",
    "SCOPE_TEAM",
    "SKILL_VISIBILITY_SCHEMA_VERSION",
    "FileSkillVisibilityProvider",
    "SkillVisibility",
    "SkillVisibilityProvider",
    "StatToken",
    "StaticSkillVisibilityProvider",
    "bootstrap_skill_visibility",
    "build_skill_visibility_provider",
    "compose_skill_visibility",
    "normalize_skill_names",
    "read_skill_visibility",
    "set_skill_visibility",
    "update_skill_visibility",
    "write_skill_visibility",
]
