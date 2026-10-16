# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fingerprints of complete captured accessibility content, without transport noise."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

_SnapshotLoader = getattr(yaml, "CBaseLoader", yaml.BaseLoader)
_DESCRIPTOR = re.compile(
    r"(?P<role>[A-Za-z][\w-]*)"
    r'(?P<name>\s+"(?:[^"\\]|\\.)*")?'
    r"(?P<attributes>(?:\s+\[[^\]]+\])*)"
)
_ATTRIBUTE = re.compile(r"\[[^\]]+\]")
_GENERATED_REF = re.compile(r"\[ref=[A-Za-z0-9_.:-]+\]")
_SNAPSHOT_MARKER = re.compile(r"(?:###\s+(?:Page )?Snapshot|-\s+Page Snapshot:)\s*", re.IGNORECASE)
_ERROR_MARKER = re.compile(r"###\s+Error\s*", re.IGNORECASE)
_FENCE = re.compile(r"(`{3,}|~{3,})([A-Za-z]*)\s*")


def _snapshot_body(text: str) -> str:
    """Extract inline YAML while keeping event logs and other response sections out."""
    if not text.lstrip().startswith(("###", "- Page Snapshot:", "- Page URL:", "```", "~~~")):
        return text
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    body: list[str] | None = None
    fence = ""
    inside_snapshot = False
    expect_snapshot = False
    framed = False
    for line in lines:
        stripped = line.strip()
        if fence:
            if stripped == fence:
                fence = ""
                inside_snapshot = False
            elif inside_snapshot:
                body.append(line)
            continue
        if _ERROR_MARKER.fullmatch(stripped):
            raise ValueError("Snapshot response contains an error")
        if _SNAPSHOT_MARKER.fullmatch(stripped):
            if body is not None:
                raise ValueError("Ambiguous snapshot response")
            framed = True
            expect_snapshot = True
            continue
        match = _FENCE.fullmatch(stripped)
        if match:
            fence = match.group(1)
            if expect_snapshot or not text.strip().startswith(("###", "- Page")):
                if match.group(2).lower() not in {"", "yaml", "yml"} or body is not None:
                    raise ValueError("Unsupported snapshot fence")
                body = []
                inside_snapshot = True
                framed = True
                expect_snapshot = False
            continue
        if expect_snapshot and stripped:
            raise ValueError("Snapshot body is not inline YAML")
    if fence or expect_snapshot:
        raise ValueError("Incomplete snapshot response")
    if body is not None:
        return "\n".join(body)
    if framed or text.lstrip().startswith("###"):
        raise ValueError("Snapshot body is unavailable")
    return text


def _unwrap_snapshot(raw: Any) -> Any:
    """Accept the runtime's text payload and its supported MCP/JSON envelopes."""
    seen: dict[int, Any] = {}
    while True:
        if isinstance(raw, dict):
            if id(raw) in seen:
                raise ValueError("Recursive snapshot envelope")
            seen[id(raw)] = raw
            capture_failed = raw.get("isError") is True or raw.get("ok") is False or raw.get("success") is False
            if capture_failed or raw.get("error"):
                raise ValueError("Snapshot capture failed")
            if raw.get("role"):
                return raw
            if raw.get("__browser_compact_rpc__") is True:
                raw = raw.get("payload")
            elif isinstance(raw.get("content"), list):
                texts = [
                    item["text"]
                    for item in raw["content"]
                    if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
                ]
                if not texts:
                    raise ValueError("Snapshot text is missing")
                raw = "\n".join(texts)
            else:
                key = next((key for key in ("snapshot", "result", "text", "data", "root", "nodes") if key in raw), None)
                if key is None:
                    raise ValueError("Unknown snapshot envelope")
                raw = raw[key]
        elif isinstance(raw, str):
            text = raw.lstrip("\ufeff")
            if text.lstrip().startswith(("{", '"')):
                raw = json.loads(text)
            elif text.lstrip().startswith("["):
                # A raw JSON accessibility tree; YAML sequences are also supported.
                try:
                    raw = json.loads(text)
                except ValueError:
                    return _snapshot_body(text)
            else:
                return _snapshot_body(text)
        elif isinstance(raw, list):
            return raw
        else:
            raise ValueError("Snapshot content is missing")


def _descriptor(value: str) -> list[Any]:
    match = _DESCRIPTOR.fullmatch(value.strip())
    if match is None:
        raise ValueError("Invalid accessibility descriptor")
    raw_name = (match.group("name") or "").strip()
    name = json.loads(raw_name) if raw_name else ""
    attributes = [item for item in _ATTRIBUTE.findall(match.group("attributes")) if not _GENERATED_REF.fullmatch(item)]
    return ["descriptor", match.group("role"), name, attributes]


def _yaml_content(node: Node, seen: set[int], *, descriptor: bool = False) -> Any:
    if id(node) in seen:
        # Playwright emits no YAML aliases; never expand recursive/duplicated aliases.
        raise ValueError("Accessibility snapshots cannot contain YAML aliases")
    seen.add(id(node))
    if isinstance(node, ScalarNode):
        return _descriptor(node.value) if descriptor else ["text", node.value]
    if isinstance(node, SequenceNode):
        return ["children", [_yaml_content(child, seen, descriptor=True) for child in node.value]]
    if isinstance(node, MappingNode):
        entries = []
        for key, value in node.value:
            if not isinstance(key, ScalarNode):
                raise ValueError("Invalid accessibility property")
            if id(key) in seen:
                raise ValueError("Accessibility snapshots cannot contain YAML aliases")
            seen.add(id(key))
            label = ["property", key.value] if key.value.startswith("/") else _descriptor(key.value)
            entries.append([label, _yaml_content(value, seen)])
        return ["node", entries]
    raise ValueError("Unsupported accessibility content")


def _json_content(value: Any, active: set[int]) -> Any:
    if isinstance(value, (dict, list)):
        if id(value) in active:
            raise ValueError("Recursive accessibility tree")
        active.add(id(value))
        try:
            if isinstance(value, list):
                return [_json_content(child, active) for child in value]
            return {
                key: _json_content(item, active)
                for key, item in value.items()
                if not (key == "ref" and value.get("role"))
            }
        finally:
            active.remove(id(value))
    return value


def fingerprint_snapshot(raw: Any) -> str | None:
    """Hash full captured AX content; return None for missing or unusable captures.

    Serialization and generated node references are normalized. Text, links,
    hierarchy, child order and control states are retained without truncation.
    Empty successful snapshots are valid. Raw content is neither saved nor logged.
    """
    try:
        payload = _unwrap_snapshot(raw)
        if isinstance(payload, str):
            node = yaml.compose(payload, Loader=_SnapshotLoader)
            if node is None:
                if payload.strip():
                    return None
                normalized: Any = ["children", []]
            elif isinstance(node, SequenceNode):
                normalized = _yaml_content(node, set())
            else:
                return None
        else:
            roots = payload if isinstance(payload, list) else [payload]
            if not all(isinstance(node, dict) and node.get("role") for node in roots):
                return None
            normalized = ["json", _json_content(roots, set())] if roots else ["children", []]
        serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    except (ValueError, TypeError, RecursionError, yaml.YAMLError):
        return None
