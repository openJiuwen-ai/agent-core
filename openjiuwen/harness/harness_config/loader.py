# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""HarnessConfigLoader: parse, validate, and resolve harness_config.yaml → ResolvedHarnessConfig."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import ValidationError

from openjiuwen.harness.harness_config.schema import HarnessConfig


def _normalize_content(
    content: Union[str, Dict[str, str], None],
) -> Dict[str, str]:
    """Normalize section content to ``{lang: text}`` dict."""
    if content is None:
        return {}
    if isinstance(content, str):
        return {"cn": content, "en": content}
    return dict(content)


def _render_template(text: str, params: Dict[str, Any]) -> str:
    """Render ``{{ var }}`` placeholders in *text* using *params*.

    Tries jinja2 first; falls back to simple regex substitution so that the
    harness_config module has no hard dependency on jinja2.
    """
    if not text or "{{" not in text:
        return text

    try:
        from jinja2 import Environment, Undefined  # type: ignore[import]

        env = Environment(undefined=Undefined)
        return env.from_string(text).render(**params)
    except ImportError:
        pass

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        key = m.group(1).strip()
        return str(params.get(key, m.group(0)))

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", _replace, text)


@dataclass
class ResolvedSection:
    """An inline (non-file) prompt section ready for ``add_section()``."""

    name: str
    priority: int
    content: Dict[str, str]


@dataclass
class ResolvedFileSection:
    """A file-backed prompt section: content to write into ``workspace/{filename}``."""

    filename: str  # e.g. "AGENT.md"
    content: Dict[str, str]  # {language: text}


@dataclass
class ResolvedHarnessConfig:
    """Output of HarnessConfigLoader.load().

    Attributes:
        config:         Parsed HarnessConfig (validated).
        system_prompt:  Content of the ``identity`` section (no ``file`` field).
                        Mapped to ``DeepAgentConfig.system_prompt``.
        extra_sections: Non-identity inline sections → ``builder.add_section()``.
        file_sections:  File-backed sections → written to workspace by HarnessConfigBuilder.
        source_path:    Absolute path of the harness_config.yaml file.
    """

    config: HarnessConfig
    system_prompt: Optional[str]
    extra_sections: List[ResolvedSection] = field(default_factory=list)
    file_sections: List[ResolvedFileSection] = field(default_factory=list)
    source_path: Path = field(default_factory=Path)


class HarnessConfigLoader:
    """Load, validate, and resolve a harness_config.yaml file."""

    @classmethod
    def load(
        cls,
        path: Union[str, Path],
        params: Optional[Dict[str, Any]] = None,
        *,
        workspace_root: Optional[Union[str, Path]] = None,
    ) -> ResolvedHarnessConfig:
        """Load and resolve *path*, returning a ``ResolvedHarnessConfig``.

        Args:
            path:           Path to the harness_config.yaml file.
            params:         Optional render parameters injected into
                            ``{{ var }}`` placeholders in section content.
            workspace_root: Overrides ``{{ workspace_root }}`` placeholder;
                            defaults to the directory containing the config file.

        Raises:
            HarnessConfigNotFoundError:    File does not exist.
            HarnessConfigValidationError:  Pydantic schema validation failed.
        """
        import yaml  # pyyaml — available as transitive dep of transformers

        path = Path(path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"HarnessConfig file not found: {path}")

        raw = path.read_text(encoding="utf-8")
        data: Dict[str, Any] = yaml.safe_load(raw) or {}

        try:
            config = HarnessConfig.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"HarnessConfig validation failed in '{path}': {exc}") from exc

        effective_params: Dict[str, Any] = dict(params or {})
        effective_params.setdefault(
            "workspace_root",
            str(workspace_root) if workspace_root is not None else str(path.parent),
        )

        language = config.language
        system_prompt: Optional[str] = None
        extra_sections: List[ResolvedSection] = []
        file_sections: List[ResolvedFileSection] = []

        for sec in config.prompts.sections if config.prompts else []:
            raw_content = _normalize_content(sec.content)

            rendered: Dict[str, str] = {
                lang: _render_template(text, effective_params) for lang, text in raw_content.items()
            }

            if sec.file is not None:
                # File-backed section → write to workspace; ContextEngineeringRail
                # reads it back at each model call.
                file_sections.append(ResolvedFileSection(filename=sec.file, content=rendered))
            elif sec.name == "identity":
                # Identity section → DeepAgentConfig.system_prompt
                system_prompt = rendered.get(language) or rendered.get("cn") or rendered.get("en")
            else:
                # Inline custom section → add_section() after configure()
                priority = sec.priority if sec.priority is not None else 30
                extra_sections.append(
                    ResolvedSection(
                        name=sec.name,
                        priority=priority,
                        content=rendered,
                    )
                )

        return ResolvedHarnessConfig(
            config=config,
            system_prompt=system_prompt,
            extra_sections=extra_sections,
            file_sections=file_sections,
            source_path=path,
        )


__all__ = [
    "HarnessConfigLoader",
    "ResolvedFileSection",
    "ResolvedHarnessConfig",
    "ResolvedSection",
]
