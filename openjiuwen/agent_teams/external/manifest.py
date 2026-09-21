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
    if runtime.provider_version != expected_version:
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
