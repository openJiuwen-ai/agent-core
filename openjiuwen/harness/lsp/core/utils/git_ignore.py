"""Git ignore filtering for LSP navigation results."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openjiuwen.harness.lsp.core.utils.file_uri import file_uri_to_path

logger = logging.getLogger(__name__)


def uri_to_file_path(uri: str) -> str:
    """Extract filesystem path from a file:// URI."""
    return file_uri_to_path(uri)


async def filter_git_ignored_locations(
    locations: list[dict[str, Any]],
    cwd: str,
) -> list[dict[str, Any]]:
    """
    Filter out LSP location results that are inside gitignored directories.

    Uses `git check-ignore --stdin` for batch queries (50 paths per batch).

    Args:
        locations: List of LSP Location or SymbolInformation dicts.
        cwd: Git repository root directory.

    Returns:
        Filtered location list excluding gitignored files.
    """
    if not locations:
        return locations

    # Extract URIs and deduplicate
    uri_to_path: dict[str, str] = {}
    for loc in locations:
        uri = (
            loc.get("uri")
            or loc.get("targetUri")
            or (
                loc.get("location", {}).get("uri")
                if isinstance(loc.get("location"), dict)
                else None
            )
        )
        if uri and uri not in uri_to_path:
            uri_to_path[uri] = uri_to_file_path(uri)

    unique_paths = list(set(uri_to_path.values()))
    if not unique_paths:
        return locations

    ignored_paths: set[str] = set()
    batch_size = 50
    timeout = 5.0

    for i in range(0, len(unique_paths), batch_size):
        batch = unique_paths[i: i + batch_size]
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "check-ignore",
                "--stdin",
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdin_bytes = "\n".join(batch).encode("utf-8")
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes), timeout=timeout
            )
            if proc.returncode == 0 and stdout:
                for line in stdout.decode("utf-8", errors="replace").split("\n"):
                    line = line.strip()
                    if line:
                        ignored_paths.add(line)
        except (OSError, asyncio.TimeoutError) as exc:
            logger.debug("git check-ignore batch failed (ignored): %s", exc)

    if not ignored_paths:
        return locations

    def is_not_ignored(loc: dict[str, Any]) -> bool:
        uri = loc.get("uri") or loc.get("targetUri")
        if not uri:
            return True
        file_path = uri_to_path.get(uri, "")
        return file_path not in ignored_paths

    return [loc for loc in locations if is_not_ignored(loc)]
