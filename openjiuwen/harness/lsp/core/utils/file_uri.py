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

    Handles percent-encoded characters and Windows path formats.
    """
    if not uri.startswith("file://"):
        return uri

    path = uri[7:]

    # Windows: file:///C:/... -> C:/...
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]

    try:
        path = urllib.parse.unquote(path)
    except ValueError:
        pass  # malformed percent-encoding; return raw path

    return path
