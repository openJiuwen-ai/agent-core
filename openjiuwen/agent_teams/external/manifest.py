# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Map a parsed AgentTemplate runtime onto the existing external CLI config."""

from __future__ import annotations

from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec
from openjiuwen.harness_providers.factory import resolve_provider

_PROVIDER_TO_CLI = {"codex": "codex", "claudecode": "claude"}
_CONFIG_FIELDS = {
    "skill_conflict",
    "system_prompt_mode",
    "mcp_default_tools_approval_mode",
    "codex_bypass_approvals_and_sandbox",
    "codex_turn_idle_timeout_s",
    "codex_turn_idle_retries",
    "claude_turn_idle_timeout_s",
    "claude_max_buffer_size",
}


def cli_agent_for_provider(provider: str | None) -> str | None:
    """The CLI kind an AgentTemplate runtime provider runs as, or ``None`` when unsupported."""
    return _PROVIDER_TO_CLI.get(provider or "")


def external_cli_agent_spec_from_template(
    template: AgentTemplateSpec,
) -> ExternalCliAgentSpec | None:
    """Translate ``manifest.runtime`` to the Team external-CLI configuration."""

    runtime = template.runtime
    if runtime is None:
        return None
    provider = runtime.provider_name
    if provider not in _PROVIDER_TO_CLI:
        raise ValueError("runtime.provider_name must be 'codex' or 'claudecode'")
    expected_version = resolve_provider(provider).card.implementation_version
    if runtime.provider_version is not None and runtime.provider_version != expected_version:
        raise ValueError(
            f"runtime requires {provider} {runtime.provider_version!r}; "
            f"installed version is {expected_version!r}"
        )
    if runtime.sdk_paths:
        raise ValueError(
            "runtime.sdk_paths is not supported for built-in Codex/Claude Code providers"
        )
    unknown_config = sorted(set(runtime.config) - _CONFIG_FIELDS)
    if unknown_config:
        raise ValueError(f"runtime.config cannot set: {', '.join(unknown_config)}")
    unsupported = [
        name
        for name in ("tools", "rails", "subagents", "mcps")
        if getattr(template, name)
    ]
    if unsupported:
        raise ValueError(
            "external runtime does not support: " + ", ".join(unsupported)
        )

    return ExternalCliAgentSpec.model_validate(
        {
            "cli_agent": _PROVIDER_TO_CLI[provider],
            "skill_conflict": "append",
            **runtime.config,
            "skills": [skill.model_dump(mode="json") for skill in template.skills],
        }
    )


def merge_external_cli_config(
    declared: ExternalCliAgentSpec,
    template_config: ExternalCliAgentSpec,
) -> ExternalCliAgentSpec:
    """Launch config of an external-CLI member spawned from an expert template.

    The team's declared config for the CLI kind is the base — it holds the
    host deployment knowledge (command, cwd, env, SSH transport, built-in
    model catalog ...). The template adds only what belongs to the expert:
    its skills (appended after the declared ones), ``skill_conflict``, and the
    ``runtime.config`` keys it sets to a non-default value.

    Raises:
        ValueError: The two configs are for different CLI kinds.
    """
    if declared.cli_agent != template_config.cli_agent:
        raise ValueError(
            f"template runs on cli_agent {template_config.cli_agent!r}, "
            f"declared config is for {declared.cli_agent!r}"
        )
    overlay: dict[str, object] = {"skill_conflict": template_config.skill_conflict}
    for name in _CONFIG_FIELDS - {"skill_conflict"}:
        value = getattr(template_config, name)
        if value != ExternalCliAgentSpec.model_fields[name].get_default():
            overlay[name] = value
    skills = list(declared.skills)
    for skill in template_config.skills:
        if skill not in skills:
            skills.append(skill)
    overlay["skills"] = skills
    return declared.model_copy(update=overlay)
