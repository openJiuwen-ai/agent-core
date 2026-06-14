from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from openjiuwen.core.foundation.llm import BaseMessage, UserMessage


class ReinjectBuilder(Protocol):
    def __call__(self, ctx: "ReinjectContext") -> str | list[BaseMessage]:
        ...


@dataclass(frozen=True)
class ReinjectBuilderSpec:
    name: str
    label: str
    builder: ReinjectBuilder


@dataclass(frozen=True)
class ReinjectContext:
    session_state: dict[str, Any]
    source_messages: list[BaseMessage]
    messages_to_keep: list[BaseMessage]
    workspace_root: str | None
    config: Any
    state_marker: str
    truncate: Callable[[str], str]
    context: Any = None


class StateReinjector:
    def __init__(self) -> None:
        self._builders: list[ReinjectBuilderSpec] = []

    def register(self, name: str, label: str, builder: ReinjectBuilder) -> None:
        spec = ReinjectBuilderSpec(name=name, label=label, builder=builder)
        for index, existing in enumerate(self._builders):
            if existing.name == name:
                self._builders[index] = spec
                return
        self._builders.append(spec)

    def register_builder(self, *, name: str, label: str, builder: ReinjectBuilder) -> None:
        self.register(name=name, label=label, builder=builder)

    def iter_builders(self) -> tuple[ReinjectBuilderSpec, ...]:
        return tuple(self._builders)

    def build_messages(self, ctx: ReinjectContext, only: list[str] | None = None) -> list[BaseMessage]:
        active = set(only) if only is not None else None
        messages: list[BaseMessage] = []
        for spec in self._builders:
            if active is not None and spec.name not in active:
                continue
            content = spec.builder(ctx)
            if isinstance(content, list):
                messages.extend(content)
                continue
            if content:
                messages.append(UserMessage(content=f"{ctx.state_marker}\n[{spec.label}]\n{ctx.truncate(content)}"))
        return messages
