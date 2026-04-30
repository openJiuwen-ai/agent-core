"""File URI <-> filesystem path conversion utilities."""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path


def path_to_file_uri(file_path: str) -> str:
    """
    Convert a filesystem path to a file:// URI.

    Handles Windows paths (including UNC paths) and Unix paths,
    and applies percent-encoding for special characters.
    """
    if sys.platform == "win32":
        abs_path = Path(file_path).resolve()
        posix_path = abs_path.as_posix()
        return "file:///" + posix_path.replace("\\", "/")
    else:
        return "file://" + urllib.parse.quote(Path(file_path).resolve().as_posix(), safe="/:")


def file_uri_to_path(uri: str) -> str:
    """
    Convert a file:// URI to a filesystem path.

    Handles percent-encoded characters (including %3A for Windows drive
    colons) and produces a native path for each platform:

    - Windows: ``file:///C:/foo`` or ``file:///d%3A/foo`` → ``C:\\foo``
    - Linux/macOS: ``file:///home/user/foo`` → ``/home/user/foo``
    """
    if not uri.startswith("file://"):
        return uri

    path = uri[7:]  # strip "file://"

    # Decode percent-encoded characters first so that %3A → : before any
    # Windows drive-letter check (pyright sends file:///d%3A/... on Windows).
    try:
        path = urllib.parse.unquote(path)
    except ValueError:
        pass  # malformed percent-encoding; keep raw

    if sys.platform == "win32":
        # After unquoting, Windows URIs look like /C:/... or /d:/...
        # Strip the leading slash before the drive letter.
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        # Normalise to backslashes and capitalise the drive letter.
        path = path.replace("/", "\\")
        if len(path) >= 2 and path[1] == ":":
            path = path[0].upper() + path[1:]

    return path
