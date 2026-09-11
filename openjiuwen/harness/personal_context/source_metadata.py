"""Program-owned Markdown metadata for atomic PersonalContext sources."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit, urlunsplit

from openjiuwen.harness.personal_context.models import RawChangeItem
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

_SOURCE_ID = re.compile(r"^src_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIELD = re.compile(r"^- ([a-z_]+): (.+)$")
_FIELDS = (
    "source_id",
    "source_type",
    "title",
    "locator",
    "provider",
    "service",
    "first_seen",
    "last_seen",
    "latest_revision",
    "latest_hash",
)
_MAX_METADATA_BYTES = 64 * 1024


def _source_error(message: str, *, cause: BaseException | None = None) -> NoReturn:
    raise build_error(StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR, error_msg=message, cause=cause) from None


def _assert_no_symlink_chain(path: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            _source_error("atomic source metadata path must not traverse a symlink")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _required_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _source_error(f"atomic source metadata {name} is invalid")
    return value.strip()


def _display_title(value: str) -> str:
    title = " ".join(value.split())
    return title[:512] or "Atomic source"


def normalize_source_locator(original_ref: str) -> str:
    """Return the stable locator without persisting URL credentials."""

    locator = _required_text(original_ref, name="locator")
    parsed = urlsplit(locator)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return locator
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return f"{parsed.scheme.casefold()}://[redacted]"
    if not host:
        return f"{parsed.scheme.casefold()}://[redacted]"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    safe_netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.casefold(), safe_netloc, parsed.path, "", ""))


def source_id_for_locator(locator: str) -> str:
    """Return the stable 128-bit digest ID for one normalized locator."""

    normalized = normalize_source_locator(locator)
    return f"src_{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:32]}"


def _latest_hash(item: RawChangeItem) -> str:
    value: str | bytes
    if isinstance(item.raw_snapshot, bytes):
        value = item.raw_snapshot
    elif isinstance(item.raw_snapshot, str):
        value = item.raw_snapshot
    elif item.content is not None:
        value = item.content
    else:
        value = item.revision_id
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _source_type(item: RawChangeItem, provider: str) -> str:
    resource = item.metadata.get("resource")
    return _required_text(resource if isinstance(resource, str) else provider, name="source_type")


def _render_source_metadata(metadata: Mapping[str, str]) -> bytes:
    title = _display_title(metadata["title"])
    fields = "\n".join(f"- {name}: {json.dumps(metadata[name], ensure_ascii=False)}" for name in _FIELDS)
    return f"# {title}\n\n{fields}\n".encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            _source_error("atomic source metadata path must not be a symlink")
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        _source_error("atomic source metadata could not be written", cause=exc)
    finally:
        if temporary is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()


def read_source_metadata(path: Path) -> dict[str, object]:
    """Read and validate one PersonalContext-owned metadata Markdown."""

    _assert_no_symlink_chain(path)
    if not _SOURCE_ID.fullmatch(path.stem) or path.suffix.casefold() != ".md":
        _source_error("atomic source metadata filename is invalid")
    try:
        if not path.is_file() or path.stat().st_size > _MAX_METADATA_BYTES:
            _source_error("atomic source metadata file is missing or too large")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _source_error("atomic source metadata could not be read", cause=exc)
    lines = text.splitlines()
    if len(lines) != len(_FIELDS) + 2 or not lines[0].startswith("# ") or lines[1] != "":
        _source_error("atomic source metadata Markdown is invalid")
    values: dict[str, str] = {}
    for line in lines[2:]:
        match = _FIELD.fullmatch(line)
        if match is None or match.group(1) in values:
            _source_error("atomic source metadata field is invalid")
        try:
            value = json.loads(match.group(2))
        except json.JSONDecodeError as exc:
            _source_error("atomic source metadata field is invalid", cause=exc)
        values[match.group(1)] = _required_text(value, name=match.group(1))
    if tuple(values) != _FIELDS:
        _source_error("atomic source metadata fields are invalid")
    source_id = values["source_id"]
    locator = values["locator"]
    if source_id != path.stem or source_id != source_id_for_locator(locator):
        _source_error("atomic source metadata identity does not match its filename")
    if lines[0][2:] != _display_title(values["title"]):
        _source_error("atomic source metadata title does not match its heading")
    if _SHA256.fullmatch(values["latest_hash"]) is None:
        _source_error("atomic source metadata latest_hash is invalid")
    return dict(values)


def read_source_detail(source_root: Path, source_id: str) -> dict[str, object]:
    """Read one source by stable ID and expose only the public detail fields."""

    if not isinstance(source_id, str) or _SOURCE_ID.fullmatch(source_id) is None:
        _source_error("atomic source ID is invalid")
    _assert_no_symlink_chain(source_root)
    metadata = read_source_metadata(source_root / f"{source_id}.md")
    return {
        "source_id": metadata["source_id"],
        "title": metadata["title"],
        "source_type": metadata["source_type"],
        "locator": metadata["locator"],
        "provider": metadata["provider"],
        "service_id": metadata["service"],
        "first_seen": metadata["first_seen"],
        "last_seen": metadata["last_seen"],
    }


def upsert_source_metadata(
    source_root: Path,
    item: RawChangeItem,
    *,
    provider: str,
    service_id: str,
    observed_at: str,
) -> str:
    """Create or update one program-owned metadata Markdown and return its ID."""

    locator = normalize_source_locator(item.original_ref)
    source_id = source_id_for_locator(locator)
    provider_value = _required_text(provider, name="provider")
    service_value = _required_text(service_id, name="service")
    observed_value = _required_text(observed_at, name="observed_at")
    _assert_no_symlink_chain(source_root)
    try:
        source_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _source_error("atomic source metadata directory could not be created", cause=exc)
    _assert_no_symlink_chain(source_root)
    if not source_root.is_dir():
        _source_error("atomic source metadata root is invalid")
    target = source_root / f"{source_id}.md"
    existing: dict[str, object] | None = None
    if target.exists() or target.is_symlink():
        existing = read_source_metadata(target)
    previous_title = existing.get("title") if existing is not None else None
    title_value = item.title if isinstance(item.title, str) and item.title.strip() else previous_title or locator
    metadata = {
        "source_id": source_id,
        "source_type": _source_type(item, provider_value),
        "title": _display_title(str(title_value)),
        "locator": locator,
        "provider": provider_value,
        "service": service_value,
        "first_seen": str(existing["first_seen"]) if existing is not None else observed_value,
        "last_seen": observed_value,
        "latest_revision": _required_text(item.revision_id, name="latest_revision"),
        "latest_hash": _latest_hash(item),
    }
    _atomic_write(target, _render_source_metadata(metadata))
    return source_id


__all__ = [
    "normalize_source_locator",
    "read_source_detail",
    "read_source_metadata",
    "source_id_for_locator",
    "upsert_source_metadata",
]
