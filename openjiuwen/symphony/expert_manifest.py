"""Read the discovery fields of standalone AgentTemplate manifests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from openjiuwen.symphony.config import DirectoryScanConfig
from openjiuwen.symphony.models import CapabilityDescriptor
from openjiuwen.symphony.models._redaction import redact_sensitive_text
from openjiuwen.symphony.shared.fingerprint.safe_filesystem import open_regular_file_no_follow
from openjiuwen.symphony.shared.fingerprint.scanner import (
    ScanDiagnostic,
    ScanResult,
    _discover_entrypoints,
    _ScanBudget,
    _validated_root,
)


def scan_expert_manifests(
    root: Path, config: DirectoryScanConfig, *, excluded_paths: tuple[Path, ...],
) -> ScanResult:
    """Discover package roots without traversing into their bundled resources."""
    root = _validated_root(root)
    limits = config.limits
    manifests, diagnostics = _discover_entrypoints(
        root, config.max_depth,
        max_directories=limits.max_scan_directories,
        max_directory_entries=limits.max_directory_entries,
        max_entrypoints=min(limits.max_entrypoints, limits.max_scan_asset_files),
        budget=_ScanBudget(), excluded_paths=excluded_paths,
        filename="manifest.json", stop_at_entrypoint=True,
    )
    descriptors = []
    remaining = limits.max_scan_asset_bytes
    maximum = min(config.manifest.max_manifest_bytes, limits.max_asset_file_bytes)
    for manifest in manifests:
        relative = manifest.relative_to(root).as_posix()
        try:
            if remaining <= 0:
                diagnostics.append(ScanDiagnostic("expert_scan_limit", "Expert manifest byte budget exhausted",
                                                  path=relative))
                break
            if manifest.resolve(strict=True) != manifest:
                raise ValueError("manifest path changed")
            fd = open_regular_file_no_follow(manifest, root=root)
            if fd is None:
                raise ValueError("manifest is not a regular file")
            cap = min(maximum, remaining)
            with os.fdopen(fd, "rb") as handle:
                raw = handle.read(cap + 1)
            remaining -= len(raw)
            if len(raw) > cap:
                raise ValueError("manifest exceeds byte budget")
            value = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(value, dict) or not isinstance(value.get("package_type"), str):
                raise ValueError("manifest has no package type")
            if value["package_type"] != "agent_template":
                continue
            name, description = value.get("name"), value.get("description")
            if not isinstance(name, str) or not name.strip() or not isinstance(description, str) or not description.strip():
                raise ValueError("expert name and description must be non-empty strings")
            name, description = name.strip(), redact_sensitive_text(description.strip())
            if not name.isprintable():
                raise ValueError("expert name contains invalid characters")
            identity = json.dumps(("expert", name, description), ensure_ascii=False).encode()
            descriptors.append(CapabilityDescriptor(
                capability_type="expert", capability_id=name, name=name, description=description,
                source="expert-manifest", content_hash=hashlib.sha256(identity).hexdigest(),
                metadata={"entrypoint": relative},
            ))
        except (OSError, ValueError, RecursionError, RuntimeError):
            diagnostics.append(ScanDiagnostic("invalid_expert_manifest", "Expert manifest is invalid or unreadable",
                                              path=relative))
    return ScanResult(tuple(descriptors), tuple(diagnostics), "expert-manifest")
