"""Durable storage for legacy in-memory offloads, loaded one handle at a time."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, SystemMessage, ToolMessage, UserMessage


def _write_json(stream, value, depth=0):
    if depth > 64:
        raise ValueError("Offload JSON nesting exceeds the budget")
    if isinstance(value, str):
        stream.write('"')
        for index in range(0, len(value), 16384):
            stream.write(json.dumps(value[index:index + 16384], ensure_ascii=False)[1:-1])
        stream.write('"')
    elif isinstance(value, dict):
        stream.write("{")
        for index, (key, item) in enumerate(value.items()):
            if index:
                stream.write(",")
            _write_json(stream, key, depth + 1)
            stream.write(":")
            _write_json(stream, item, depth + 1)
        stream.write("}")
    elif isinstance(value, (list, tuple)):
        stream.write("[")
        for index, item in enumerate(value):
            if index:
                stream.write(",")
            _write_json(stream, item, depth + 1)
        stream.write("]")
    else:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False))


class FileOffloadStore:
    """Keep handles out of checkpoints without eagerly reloading their contents."""

    def __init__(self, directory: str):
        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("Offload storage directory must be absolute")
        self.directory = path

    def path(self, handle: str, version: str | None = None) -> Path:
        if not isinstance(handle, str) or not handle:
            raise ValueError("Offload handle must be a nonempty string")
        if version is not None and (not isinstance(version, str) or len(version) != 64
                                    or any(char not in "0123456789abcdef" for char in version)):
            raise ValueError("Invalid offload content version")
        return self.directory / ((version or hashlib.sha256(handle.encode("utf-8")).hexdigest()) + ".json")

    def put(self, handle: str, messages: list[BaseMessage]) -> str:
        path = self.path(handle)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        digest = hashlib.sha256()
        try:
            with temporary.open("xb") as raw:
                class Writer:
                    def write(self, value):
                        chunk = value.encode("utf-8")
                        digest.update(chunk)
                        raw.write(chunk)

                stream = Writer()
                stream.write("[")
                for index, message in enumerate(messages):
                    if index:
                        stream.write(",")
                    _write_json(stream, message.model_dump(mode="json"))
                stream.write("]")
                raw.flush()
                os.fsync(raw.fileno())
            version = digest.hexdigest()
            path = self.path(handle, version)
            os.replace(temporary, path)
            return version
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, handle: str, *, version: str | None = None, max_bytes: int = 8 * 1024 * 1024) -> list[BaseMessage]:
        path = self.path(handle, version)
        if not path.exists():
            if version is not None:
                raise ValueError("Offloaded content version is missing")
            return []
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("Offloaded messages exceed the reload budget; read the artifact in chunks")
        if version is not None and hashlib.sha256(raw).hexdigest() != version:
            raise ValueError("Offloaded messages failed the content checksum")
        rows = json.loads(raw)
        if not isinstance(rows, list) or len(rows) > 8192:
            raise ValueError("Invalid offloaded message list")
        types = {"user": UserMessage, "assistant": AssistantMessage, "tool": ToolMessage, "system": SystemMessage}
        return [types.get(row.get("role"), BaseMessage).model_validate(row) for row in rows]
