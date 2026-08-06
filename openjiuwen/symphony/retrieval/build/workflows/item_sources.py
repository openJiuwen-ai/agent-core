from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Sequence

from openjiuwen.symphony.retrieval.build.io import normalize_item_paths
from openjiuwen.symphony.retrieval.build.io.items_jsonl import (
    download_http_object_to_path,
    is_http_uri,
    is_passthrough_item_uri,
    load_items_jsonl_text,
    parse_jsonl_scanned_items,
)
from openjiuwen.symphony.retrieval.build.scanners import get_scanner_class
from openjiuwen.symphony.shared.storage import is_s3_uri, materialize_s3_dir


@dataclass(frozen=True)
class ResolvedItemPath:
    source_path: str
    source_type: str
    materialized_dir: Path


def load_pre_scanned_items(
    *,
    item_jsonl_path: str | None,
    default_paths: Sequence[str],
) -> tuple[Dict[str, dict] | None, list[str]]:
    jsonl_text = load_items_jsonl_text(item_jsonl_path=item_jsonl_path)
    if not str(jsonl_text or "").strip():
        return None, list(default_paths)
    scanned_items, manifest_paths = parse_jsonl_scanned_items(jsonl_text)
    return scanned_items, normalize_item_paths(manifest_paths)


def resolve_item_paths_or_error(
    *,
    item_paths: Sequence[str] | None,
    item_jsonl_path: str | None,
    operation: str,
) -> list[str]:
    normalized_item_paths = normalize_item_paths(item_paths or ())
    if normalized_item_paths:
        return normalized_item_paths
    if str(item_jsonl_path or "").strip():
        return []
    raise ValueError(f"IndexBuilder.{operation}: item_paths is empty and item_jsonl_path is not provided")


def materialize_existing_index_dir(base_index_dir: str | Path, *, cache_namespace: str) -> Path:
    raw = str(base_index_dir).strip()
    if is_s3_uri(raw):
        return materialize_s3_dir(raw, cache_namespace=cache_namespace)
    return Path(base_index_dir).resolve()


def resolve_materialized_item_paths(
    item_paths: Sequence[str],
    *,
    work_dir: Path,
    item_type: str,
) -> list[ResolvedItemPath]:
    resolved: list[ResolvedItemPath] = []
    extracted_root = work_dir / "materialized"
    extracted_root.mkdir(parents=True, exist_ok=True)
    scanner_cls = get_scanner_class(item_type)

    for index, raw_path in enumerate(item_paths):
        raw_text = str(raw_path).strip()
        if not raw_text:
            continue
        if is_s3_uri(raw_text) or is_http_uri(raw_text):
            archive_path = download_remote_zip(raw_text, extracted_root / f"item-{index}.zip")
            item_dir = extract_item_zip(archive_path, extracted_root / f"item-{index}", scanner_cls=scanner_cls)
            source_type = "s3_zip" if is_s3_uri(raw_text) else "http_zip"
            resolved.append(ResolvedItemPath(source_path=raw_text, source_type=source_type, materialized_dir=item_dir))
            continue

        local_path = Path(raw_text).expanduser().resolve()
        if not local_path.exists():
            raise FileNotFoundError(f"Item path not found: {local_path}")
        if local_path.is_dir():
            candidate = scanner_cls.detect_item_root(local_path)
            if candidate is None:
                continue
            resolved.append(
                ResolvedItemPath(
                    source_path=str(local_path),
                    source_type="local_dir",
                    materialized_dir=candidate,
                )
            )
            continue
        if local_path.is_file() and local_path.suffix.lower() == ".zip":
            item_dir = extract_item_zip(local_path, extracted_root / f"item-{index}", scanner_cls=scanner_cls)
            resolved.append(
                ResolvedItemPath(
                    source_path=str(local_path),
                    source_type="local_zip",
                    materialized_dir=item_dir,
                )
            )
            continue
        raise ValueError(
            f"Unsupported item path: {raw_text}. Only local dir/zip, s3://...zip, and http(s)://...zip are supported"
        )

    names: set[str] = set()
    for item in resolved:
        if item.materialized_dir.name in names:
            raise ValueError(f"Duplicate skill directory name detected: {item.materialized_dir.name}")
        names.add(item.materialized_dir.name)
    return resolved


def download_remote_zip(uri: str, destination_path: Path) -> Path:
    if not str(uri).lower().endswith(".zip"):
        raise ValueError(f"Remote item path must point to a zip file: {uri}")
    if is_http_uri(uri):
        return download_http_object_to_path(str(uri), destination_path)

    from openjiuwen.symphony.retrieval.build.workflows import index_builder as public_module

    return public_module.download_s3_object_to_path(str(uri), destination_path)


def extract_item_zip(zip_path: Path, target_dir: Path, *, scanner_cls) -> Path:
    if zip_path.suffix.lower() != ".zip":
        raise ValueError(f"Zip path expected, got: {zip_path}")
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        safe_extract_zip(archive, target_dir)

    direct_candidate = scanner_cls.detect_item_root(target_dir)
    if direct_candidate is not None:
        return direct_candidate

    child_dirs: list[Path] = []
    for path in sorted(target_dir.iterdir()):
        if path.is_dir() and not path.name.startswith("."):
            child_dirs.append(path)
    if len(child_dirs) == 1:
        nested_candidate = scanner_cls.detect_item_root(child_dirs[0])
        if nested_candidate is not None:
            return nested_candidate

    candidate_roots: set[Path] = set()
    for path in target_dir.rglob("*"):
        if not path.is_dir():
            continue
        if "__MACOSX" in path.parts:
            continue
        if scanner_cls.detect_item_root(path) is not None:
            candidate_roots.add(path.resolve())
    unique_parents = sorted(candidate_roots)
    if len(unique_parents) == 1:
        return unique_parents[0]
    if len(unique_parents) > 1:
        pretty = ", ".join(str(path.relative_to(target_dir)) for path in unique_parents[:5])
        raise ValueError(f"Zip archive contains multiple item roots; unable to choose one: {pretty}")
    raise ValueError(f"Zip archive does not contain a valid {scanner_cls.item_type} root: {zip_path}")


def safe_extract_zip(archive: zipfile.ZipFile, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    for member in archive.infolist():
        member_name = str(member.filename or "").replace("\\", "/")
        try:
            (target_root / member_name).resolve().relative_to(target_root)
        except ValueError as exc:
            raise ValueError(f"Unsafe zip member path: {member.filename}") from exc
    archive.extractall(target_root)


def validate_item_dir(path: Path, *, scanner_cls) -> Path:
    candidate = scanner_cls.detect_item_root(path)
    if candidate is None:
        raise ValueError(f"Item directory does not contain a valid {scanner_cls.item_type} root: {path}")
    return candidate


def normalize_manifest_item_path(value: str | Path) -> str:
    raw = str(value).strip()
    if is_passthrough_item_uri(raw):
        return raw
    return str(Path(raw).expanduser().resolve())


def materialize_item_dirs_for_scan(
    item_paths: Sequence[str],
    *,
    item_type: str,
) -> tuple[tempfile.TemporaryDirectory, list[ResolvedItemPath]]:
    tmpdir = tempfile.TemporaryDirectory(prefix="retriever-index-added-items-")
    try:
        resolved = resolve_materialized_item_paths(
            item_paths,
            work_dir=Path(tmpdir.name),
            item_type=item_type,
        )
    except Exception:
        tmpdir.cleanup()
        raise
    return tmpdir, resolved


# Backward-compatible private names for existing tests and local scripts.
_download_remote_zip = download_remote_zip
_extract_item_zip = extract_item_zip
_load_pre_scanned_items = load_pre_scanned_items
_materialize_existing_index_dir = materialize_existing_index_dir
_normalize_manifest_item_path = normalize_manifest_item_path
_resolve_item_paths_or_error = resolve_item_paths_or_error
_resolve_materialized_item_paths = resolve_materialized_item_paths
_safe_extract_zip = safe_extract_zip
_validate_item_dir = validate_item_dir
