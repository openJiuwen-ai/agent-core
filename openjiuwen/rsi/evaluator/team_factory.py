# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Factory helpers for building Agent Teams from team skill directories."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

from openjiuwen.agent_teams.models.pool import ModelRouterConfig
from openjiuwen.agent_teams.reliability.config import ReliabilityConfig
from openjiuwen.agent_teams.schema.blueprint import StorageSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
from openjiuwen.agent_teams.schema.team import TeamMemberSpec, TeamRole
from openjiuwen.agent_teams.team_workspace.models import TeamWorkspaceConfig
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.rsi.config import EvaluatorConfig
from openjiuwen.rsi.evaluator.trajectory_paths import (
    ROLE_TRAJECTORY_DIR_NAME,
    RoleFileTrajectoryStore,
)
from openjiuwen.rsi.member_optimizer.model_config import (
    load_model_config_ref,
    without_inner_sdk_retries,
)

DEFAULT_TEAM_SPEC_FILENAME = "team_agent_spec.yaml"
DEFAULT_SKILL_MD_FILENAME = "SKILL.md"
DEFAULT_TEAM_NAME = "default_team"
EVAL_TEAM_DB_FILENAME = "team.db"
_DEFAULT_REASONING_EXTRA_BODY = {
    "thinking": {"type": "disabled"},
    "enable_thinking": False,
    "chat_template_kwargs": {"enable_thinking": False},
}
_COORDINATOR_ROLE_KEYS = {
    "coordinator",
    "lead",
    "leader",
    "team",
    "team_coordinator",
    "team_leader",
}
_TEAM_SPEC_CUSTOMIZERS: dict[int, Callable[..., None]] = {}


class _DeferredExpertHarnessLoadRail(DeepAgentRail):
    """Load one member harness at the first async lifecycle boundary."""

    priority = 1000

    def __init__(self, harness_path: str, *, member_name: str, role: str) -> None:
        super().__init__()
        self.harness_path = harness_path
        self.member_name = member_name
        self.role = role
        self.agent: Any = None
        self.loaded = False

    def init(self, agent: Any) -> None:
        self.agent = agent

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        if self.loaded:
            return
        logger.info(
            "RSI member harness load start member={} role={} harness_path={}",
            self.member_name or "?",
            self.role or "?",
            self.harness_path,
        )
        await self.agent.load_plugin(self.harness_path)
        self.loaded = True

        # Rails loaded during this callback did not participate in the current
        # before_invoke dispatch. Prepare newly mounted skills explicitly before
        # the first model call.
        from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail

        for rail in self.agent.find_rails_by_type((SkillUseRail,)):
            await rail.refresh_skill_prompt(ctx)
        logger.info(
            "RSI member harness load success member={} role={} harness_path={}",
            self.member_name or "?",
            self.role or "?",
            self.harness_path,
        )


def get_team_spec_customizer(spec: TeamAgentSpec) -> Callable[..., None] | None:
    """Return the ACH runtime customizer registered for this case spec."""
    return _TEAM_SPEC_CUSTOMIZERS.get(id(spec))


def clear_team_spec_customizer(spec: TeamAgentSpec) -> None:
    """Drop the ACH runtime customizer registered for this case spec."""
    _TEAM_SPEC_CUSTOMIZERS.pop(id(spec), None)


def register_team_spec_customizer(
    spec: TeamAgentSpec,
    customizer: Callable[..., None],
) -> TeamAgentSpec:
    """Attach a case-scoped ACH customizer without mutating TeamAgentSpec schema."""
    _TEAM_SPEC_CUSTOMIZERS[id(spec)] = customizer
    return spec


@contextmanager
def apply_team_spec_customizer_during_configure(spec: TeamAgentSpec) -> Iterator[None]:
    """Apply ACH case customizer to TeamAgents configured with ``spec``.

    Newer agent-core builds no longer expose ``TeamAgentSpec.agent_customizer``.
    The evaluator still needs to mount case-scoped Team Skill, member harnesses,
    trajectories, and artifact rails.  Keep that adaptation in ACH by wrapping
    TeamAgent.configure only for the current evaluation run.
    """
    customizer = get_team_spec_customizer(spec)
    if customizer is None:
        yield
        return

    from openjiuwen.agent_teams.agent.team_agent import TeamAgent

    original_configure = TeamAgent.configure

    def configure_with_ach_customizer(
        agent: Any,
        configured_spec: TeamAgentSpec,
        context: Any,
        *,
        member_runtime: Any = None,
    ) -> Any:
        result = original_configure(
            agent,
            configured_spec,
            context,
            member_runtime=member_runtime,
        )
        if configured_spec is spec and member_runtime is None:
            _apply_customizer_to_configured_agent(customizer, agent, context)
        return result

    TeamAgent.configure = configure_with_ach_customizer  # type: ignore[method-assign]
    try:
        yield
    finally:
        TeamAgent.configure = original_configure  # type: ignore[method-assign]


def _apply_customizer_to_configured_agent(
    customizer: Callable[..., None],
    agent: Any,
    context: Any,
) -> None:
    configurator = getattr(agent, "_configurator", None)
    harness = getattr(configurator, "harness", None)
    target_agent = getattr(harness, "inner_agent", None) or harness
    if target_agent is None:
        return
    role = getattr(context, "role", None)
    role_value = getattr(role, "value", role)
    member_name = getattr(context, "member_name", None)
    try:
        customizer(target_agent, member_name, role_value)
    except Exception as exc:
        logger.warning(
            "ACH team agent customizer failed for member {} role {}: {}",
            member_name or "?",
            role_value or "?",
            exc,
        )


def is_team_coordinator_role(*values: str | None) -> bool:
    """Return whether a Team Skill role describes the Team coordinator.

    The coordinator is the Agent Team leader. It consumes Team Skill workflow
    instructions, creates tasks, and coordinates members, but it is not a
    business ExpertHarness member to optimize.
    """
    for value in values:
        key = _role_key(value)
        if key in _COORDINATOR_ROLE_KEYS:
            return True
    return False


def _role_key(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _build_spec_from_config(
    config: EvaluatorConfig,
    team_name: str = DEFAULT_TEAM_NAME,
    output_dir: str | Path = None,
    team_roles: list["TeamSkillRoleDefinition"] | None = None,
) -> TeamAgentSpec:
    """Build a TeamAgentSpec directly from EvaluatorConfig fields.

    Structural values (spawn_mode, lifecycle, transport) are fixed constants
    suitable for inprocess evaluation runs.  Model identity and routing come
    from ``model_config_ref`` when present.  ``team_name`` is resolved from the Team
    Skill ref path by the caller, not stored on the config.

    * The team's static/dynamic tables are pinned to a case-scoped sqlite file
      (``<output_dir>/team.db``) instead of the process-global home, keeping
      every run's ``team_in_db`` probe isolated and leaving no cross-run residue.
    * Each agent role gets ``workspace.stable_base=True`` so
      ``AgentConfigurator.setup_agent`` auto-derives per-member workspace paths
      under ``team_home(team_name)/workspaces/{member}_workspace/`` and mounts
      the shared ``.team/{team_name}`` symlink into each one.  The caller
      (``CaseRunner``) is responsible for calling ``configure_openjiuwen_home``
      before invoking the team so that ``team_home`` resolves inside
      ``<output_dir>`` rather than the global ``~/.openjiuwen/.agent_teams``.
    * The shared ``TeamWorkspaceConfig`` is enabled with ``version_control=False``
      and no explicit ``root_path`` — it auto-derives to
      ``team_home(team_name)/team-workspace/`` via the redirected home.
    """
    _model_block: dict[str, Any] = {}
    model_name = ""
    client_config: Any = None
    raw_client_meta: dict[str, Any] = {}
    if str(config.model_config_ref or "").strip():
        model_config = resolve_evaluator_model_config(config.model_config_ref)
        raw_client_meta = _raw_model_client_meta(config.model_config_ref)
        _model_block["model"] = model_config.model_dump(
            mode="python",
            by_alias=True,
            exclude_none=True,
        )
        _apply_reasoning_disabled_defaults_to_model_block(_model_block)
        model_request = model_config.model_request_config
        model_name = model_request.model_name if model_request is not None else ""
        client_config = model_config.model_client_config
        if model_name:
            _model_block["model_name"] = model_name

    # Each role gets an independent dict so pydantic validation doesn't share state.
    # stable_base=True lets AgentConfigurator.setup_agent derive the per-member
    # workspace path from the (caller-redirected) team_home rather than requiring
    # explicit root_path values — each spawned member automatically lands in its
    # own directory without any customizer patching.
    leader_block: dict[str, Any] = deepcopy(_model_block)
    teammate_block: dict[str, Any] = deepcopy(_model_block)
    if output_dir is not None:
        leader_block["workspace"] = {"stable_base": True}
        teammate_block["workspace"] = {"stable_base": True}

    spec_dict: dict[str, Any] = {
        "team_name": team_name,
        "spawn_mode": "inprocess",
        "lifecycle": "temporary",
        "agents": {"leader": leader_block, "teammate": teammate_block},
        "transport": {"type": "inprocess"},
        # Evaluation members intentionally have an unbounded ReAct ceiling so
        # long implementation tasks can finish. The result-aware reliability
        # guard redirects early loops, suppresses proven call/target variants,
        # and only force-finishes after atomically terminating active claims.
        "reliability": ReliabilityConfig.model_validate(
            {
                "enabled": True,
                "detectors": {
                    "tool_error": {"enabled": False},
                    "repeat_tool": {
                        "history_size": 36,
                        "repeat_warn": 3,
                        "pingpong_warn": 6,
                        "loop_block": 6,
                        "global_stop": 12,
                    },
                    "model_error": {"enabled": False},
                    # A provider-truncated response without tool calls can leave
                    # a claimed task stranded. The detector only steers the
                    # member back into the ReAct loop; it never cancels work.
                    "output_length": {"enabled": True},
                    "compaction": {"enabled": False},
                    "pingpong": {"enabled": False},
                },
                "policy": {
                    "severity_actions": {
                        "low": ["local_steer", "observe_only"],
                        "medium": ["local_steer"],
                        "high": ["local_steer"],
                        "critical": ["local_steer"],
                    }
                },
            }
        ),
    }
    predefined_members = _build_predefined_members(team_roles or [])
    if predefined_members:
        spec_dict["predefined_members"] = predefined_members
        if any(member.role_type == TeamRole.HUMAN_AGENT for member in predefined_members):
            spec_dict["enable_hitt"] = True
    if output_dir is not None:
        spec_dict["workspace"] = TeamWorkspaceConfig(
            enabled=True,
            version_control=False,
        )
        _ensure_sys_operation_rail(leader_block)
        _ensure_sys_operation_rail(teammate_block)
    if output_dir is not None:
        db_path = Path(output_dir).expanduser().resolve() / EVAL_TEAM_DB_FILENAME
        spec_dict["storage"] = StorageSpec(
            type="sqlite",
            params={"connection_string": str(db_path)},
        )
    if model_name and client_config is not None and client_config.api_base:
        provider = client_config.client_provider
        provider_name = provider.value if hasattr(provider, "value") else str(provider)
        client_meta: dict[str, Any] = {"verify_ssl": False}
        if client_config.timeout is not None:
            client_meta["timeout"] = client_config.timeout
        elif raw_client_meta.get("timeout") is not None:
            client_meta["timeout"] = raw_client_meta["timeout"]
        if raw_client_meta.get("max_retries") is not None:
            client_meta["max_retries"] = raw_client_meta["max_retries"]
        elif client_config.max_retries is not None:
            client_meta["max_retries"] = client_config.max_retries
        request_meta = _request_meta_from_model_block(_model_block)
        spec_dict["model_router"] = ModelRouterConfig(
            api_base_url=client_config.api_base,
            api_key=client_config.api_key,
            api_provider=provider_name,
            model_names=[model_name],
            metadata={"client": client_meta, "request": request_meta},
        )
        spec_dict["model_pool"] = []
        spec_dict["model_pool_strategy"] = "router"
    return TeamAgentSpec.model_validate(spec_dict)


def _raw_model_client_meta(model_config_ref: str) -> dict[str, Any]:
    """Return client metadata from the source model config before retry normalization."""
    try:
        raw = load_model_config_ref(model_config_ref)
    except Exception:
        return {}
    model_data = raw.get("model", raw)
    if not isinstance(model_data, dict):
        return {}
    client_data = model_data.get("model_client_config")
    if not isinstance(client_data, dict):
        return {}
    return dict(client_data)


def _apply_reasoning_disabled_defaults_to_model_block(model_block: dict[str, Any]) -> None:
    """Default evaluator execution models to provider-side reasoning off.

    The OpenAI SDK-compatible path accepts provider-specific JSON body fields
    through ``extra_body``. Keep user-provided values when present, and only
    fill missing knobs that common GLM/Qwen-compatible backends use to disable
    thinking output.
    """
    model_data = model_block.get("model")
    if not isinstance(model_data, dict):
        return
    request_data = model_data.setdefault("model_request_config", {})
    if isinstance(request_data, dict):
        _merge_reasoning_disabled_extra_body(request_data)


def _request_meta_from_model_block(model_block: dict[str, Any]) -> dict[str, Any]:
    """Extract request metadata for router materialization."""
    model_data = model_block.get("model")
    if not isinstance(model_data, dict):
        return {}
    request_data = model_data.get("model_request_config")
    if not isinstance(request_data, dict):
        return {}
    return deepcopy(request_data)


def _merge_reasoning_disabled_extra_body(request_data: dict[str, Any]) -> None:
    extra_body = dict(request_data.get("extra_body") or {})
    default_extra_body = deepcopy(_DEFAULT_REASONING_EXTRA_BODY)
    for key, value in default_extra_body.items():
        if key == "chat_template_kwargs":
            existing_kwargs = extra_body.get("chat_template_kwargs")
            if isinstance(existing_kwargs, dict):
                merged_kwargs = dict(existing_kwargs)
            else:
                merged_kwargs = {}
            for kwarg_key, kwarg_value in value.items():
                merged_kwargs.setdefault(kwarg_key, kwarg_value)
            extra_body["chat_template_kwargs"] = merged_kwargs
            continue
        extra_body.setdefault(key, value)
    request_data["extra_body"] = extra_body


def _ensure_sys_operation_rail(agent_block: dict[str, Any]) -> None:
    """Mount standard file/shell tools for team agents with workspaces."""
    rails = list(agent_block.get("rails") or [])
    if not any(isinstance(rail, dict) and rail.get("type") == "core.sys_operation" for rail in rails):
        rails.append({"type": "core.sys_operation", "params": {}})
    agent_block["rails"] = rails


def _resolve_team_skill_dir(team_skill_ref_path: str | Path) -> str:
    """Derive the concrete Team Skill directory from a ref path."""
    ref_path = Path(team_skill_ref_path).expanduser().resolve()
    if ref_path.name.lower() == DEFAULT_SKILL_MD_FILENAME.lower():
        return str(ref_path.parent)
    if ref_path.is_dir():
        return str(ref_path)
    return str(ref_path.parent)


def _resolve_team_skill_md_path(team_skill_ref_path: str | Path) -> Path:
    """Resolve SKILL.md from a Team Skill directory ref path."""
    ref_path = Path(team_skill_ref_path).expanduser().resolve()
    if ref_path.is_file() and ref_path.name.lower() == DEFAULT_SKILL_MD_FILENAME.lower():
        return ref_path
    return Path(_resolve_team_skill_dir(ref_path)) / DEFAULT_SKILL_MD_FILENAME


@dataclass(frozen=True, slots=True)
class TeamSkillRailConfig:
    """SkillUseRail mount configuration for one concrete Team Skill."""

    skills_root: str
    enabled_skill: str


def resolve_team_skill_rail_config(
    team_skill_ref_path: str | Path,
) -> TeamSkillRailConfig:
    """Resolve SkillUseRail root/filter values from a Team Skill ref.

    SkillUseRail scans child directories of ``skills_dir`` for ``SKILL.md``.
    A Team Skill ref points at one concrete skill directory or its SKILL.md, so
    the rail receives the parent root plus an allow-list for that skill name.
    """
    team_skill_dir = Path(_resolve_team_skill_dir(team_skill_ref_path)).resolve()
    return TeamSkillRailConfig(
        skills_root=str(team_skill_dir.parent),
        enabled_skill=team_skill_dir.name,
    )


def _read_team_name_from_skill_md(skill_md_path: Path) -> str | None:
    """Extract team name from SKILL.md YAML frontmatter ``name`` field."""
    data = _read_team_skill_frontmatter(skill_md_path)
    team_name = data.get("name")
    if team_name is None:
        return None
    normalized = str(team_name).strip()
    return normalized or None


def _read_team_skill_frontmatter(skill_md_path: Path) -> dict[str, Any]:
    """Read SKILL.md YAML frontmatter as a mapping."""
    if not skill_md_path.is_file():
        return {}
    raw = skill_md_path.read_text(encoding="utf-8")
    if not raw.lstrip().startswith("---"):
        return {}
    try:
        _, yaml_block, _ = raw.split("---", 2)
    except ValueError:
        return {}
    data = yaml.safe_load(yaml_block) or {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True, slots=True)
class TeamSkillRoleDefinition:
    """One role declared by a Team Skill frontmatter block."""

    role_id: str
    member_name: str
    kind: str
    purpose: str
    skills: list[str]
    tools: list[str]
    role_file_path: str
    role_file_content: str
    prompt_hint: str


def load_team_skill_role_definitions(
    team_skill_ref_path: str | Path | None,
) -> list[TeamSkillRoleDefinition]:
    """Load concrete runtime roles declared by a Team Skill."""
    if not team_skill_ref_path:
        return []
    try:
        skill_md_path = _resolve_team_skill_md_path(team_skill_ref_path)
    except Exception:
        return []
    frontmatter = _read_team_skill_frontmatter(skill_md_path)
    raw_roles = frontmatter.get("roles", [])
    if not isinstance(raw_roles, list):
        return []

    roles: list[TeamSkillRoleDefinition] = []
    seen: set[str] = set()
    for raw_role in raw_roles:
        if not isinstance(raw_role, dict):
            continue
        role_id = str(raw_role.get("id") or raw_role.get("role") or "").strip()
        if not role_id or role_id in seen:
            continue
        seen.add(role_id)
        kind = str(raw_role.get("kind") or "ai_agent").strip() or "ai_agent"
        purpose = str(raw_role.get("purpose") or "").strip()
        role_file_path = skill_md_path.parent / "roles" / f"{role_id}.md"
        role_file_content = role_file_path.read_text(encoding="utf-8") if role_file_path.is_file() else ""
        roles.append(
            TeamSkillRoleDefinition(
                role_id=role_id,
                member_name=role_id,
                kind=kind,
                purpose=purpose or f"Perform the {role_id} role.",
                skills=_string_items(raw_role.get("skills")),
                tools=_string_items(raw_role.get("tools")),
                role_file_path=str(role_file_path) if role_file_path.exists() else "",
                role_file_content=role_file_content,
                prompt_hint=_extract_inline_persona(role_file_content) or _default_role_prompt_hint(role_id, purpose),
            )
        )
    return roles


def _build_predefined_members(
    team_roles: list[TeamSkillRoleDefinition],
) -> list[TeamMemberSpec]:
    members: list[TeamMemberSpec] = []
    for role in team_roles:
        if is_team_coordinator_role(role.role_id, role.member_name):
            continue
        role_type = (
            TeamRole.HUMAN_AGENT if role.kind.strip().lower() == TeamRole.HUMAN_AGENT.value else TeamRole.TEAMMATE
        )
        members.append(
            TeamMemberSpec(
                member_name=role.member_name,
                display_name=role.member_name,
                role_type=role_type,
                desc=role.purpose,
                prompt=role.prompt_hint,
            )
        )
    return members


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _extract_inline_persona(role_file_content: str) -> str:
    marker = "## Inline Persona for Teammate"
    index = role_file_content.find(marker)
    if index < 0:
        return ""
    persona_start = index + len(marker)
    tail = role_file_content[persona_start:].strip()
    fence_start = tail.find("```")
    if fence_start >= 0:
        fenced_start = fence_start + 3
        fenced = tail[fenced_start:]
        fence_end = fenced.find("```")
        if fence_end >= 0:
            return fenced[:fence_end].strip()
    next_heading = tail.find("\n## ")
    return (tail[:next_heading] if next_heading >= 0 else tail).strip()


def _default_role_prompt_hint(role_id: str, purpose: str) -> str:
    return (
        f"ROLE: {role_id}.\n"
        f"Purpose: {purpose or f'Perform the {role_id} role.'}\n"
        "Use the mounted Team Skill workflow and stay within this role boundary."
    )


def resolve_team_name_from_skill_path(team_skill_ref_path: str | Path | None) -> str:
    """Resolve the Team name from a Team Skill ref path, falling back to a default.

    The Team name is an attribute of the Team Skill (``SKILL.md`` frontmatter
    ``name``), not of EvaluatorConfig.  Empty paths or unreadable SKILL.md
    files resolve to ``DEFAULT_TEAM_NAME`` so callers always receive a stable
    non-empty name suitable for TeamAgentSpec and workspace scoping.
    """
    if not team_skill_ref_path:
        return DEFAULT_TEAM_NAME
    try:
        skill_md_path = _resolve_team_skill_md_path(team_skill_ref_path)
        name = _read_team_name_from_skill_md(skill_md_path)
    except Exception:
        return DEFAULT_TEAM_NAME
    return name or DEFAULT_TEAM_NAME


def resolve_evaluator_model_config(
    ref_path: str,
) -> TeamModelConfig:
    """Resolve ``EvaluatorConfig.model_config_ref`` into a TeamModelConfig."""
    if not ref_path:
        message = "evaluator.model_config_ref is required"
        raise ValueError(message)

    try:
        raw = load_model_config_ref(ref_path)
    except FileNotFoundError as exc:
        raise ValueError(f"evaluator.model_config_ref not found: {ref_path}") from exc
    except Exception as exc:
        raise ValueError(f"failed to load evaluator.model_config_ref {ref_path}: {exc}") from exc
    model_data = raw.get("model", raw)
    if not isinstance(model_data, dict):
        raise ValueError(f"evaluator model config must be a mapping: {ref_path}")
    return TeamModelConfig.model_validate(without_inner_sdk_retries(model_data))


@dataclass(frozen=True, slots=True)
class TeamSkillTeamFactory:
    """Create case-scoped TeamAgentSpec from config ref + runtime skill ref."""

    config: EvaluatorConfig

    def build_base_spec(self) -> TeamAgentSpec:
        """Build a bare TeamAgentSpec from config with no customizer attached."""
        return _build_spec_from_config(self.config)

    @staticmethod
    def load_team_name_from_skill_path(team_skill_ref_path: str | Path) -> str | None:
        """Resolve Team name from Team Skill SKILL.md frontmatter at the given ref path."""
        skill_md_path = _resolve_team_skill_md_path(team_skill_ref_path)
        return _read_team_name_from_skill_md(skill_md_path)

    def create_team_spec(
        self,
        team_skill_ref_path: str | Path | None = None,
        harness_refs: dict[str, str] | None = None,
        output_dir: str | Path | None = None,
    ) -> TeamAgentSpec:
        """Assemble a case-scoped TeamAgentSpec and register its ACH customizer."""
        case_dir = Path(output_dir) if output_dir is not None else None
        trajectory_dir = Path(output_dir) / ROLE_TRAJECTORY_DIR_NAME if output_dir is not None else None
        team_name = resolve_team_name_from_skill_path(team_skill_ref_path)
        base_spec = _build_spec_from_config(
            self.config,
            team_name,
            output_dir=output_dir,
            team_roles=load_team_skill_role_definitions(team_skill_ref_path),
        )
        customizer = self._build_agent_customizer(
            team_skill_ref_path=team_skill_ref_path,
            harness_refs=harness_refs or {},
            trajectory_dir=trajectory_dir,
            case_dir=case_dir,
            team_name=team_name,
        )
        return register_team_spec_customizer(base_spec, customizer)

    @staticmethod
    def _build_agent_customizer(
        *,
        team_skill_ref_path: str | Path | None,
        harness_refs: dict[str, str],
        trajectory_dir: str | Path | None,
        case_dir: Path | None,
        team_name: str = DEFAULT_TEAM_NAME,
    ) -> Callable[..., None]:
        skill_rail_config = resolve_team_skill_rail_config(team_skill_ref_path) if team_skill_ref_path else None
        trace_root = Path(trajectory_dir).expanduser().resolve() if trajectory_dir else None

        def customizer(
            agent: Any,
            member_name: str | None = None,
            role: str | None = None,
        ) -> None:
            is_coordinator = is_team_coordinator_role(member_name, role)
            if skill_rail_config is not None:
                from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail

                agent.add_rail(
                    SkillUseRail(
                        skills_dir=skill_rail_config.skills_root,
                        skill_mode=SkillUseRail.SKILL_MODE_ALL,
                        enabled_skills=[skill_rail_config.enabled_skill],
                        include_tools=not is_coordinator,
                    )
                )

            harness_path = "" if is_coordinator else harness_refs.get(member_name or "")
            if harness_path:
                agent.add_rail(
                    _DeferredExpertHarnessLoadRail(
                        harness_path,
                        member_name=member_name or "",
                        role=role or "",
                    )
                )

            if trace_root is not None:
                from openjiuwen.harness.rails.evolution.trajectory_rail import (
                    TrajectoryRail,
                )

                member_key = member_name or role or "unknown"
                store = RoleFileTrajectoryStore(trace_root, member_key)
                agent.add_rail(TrajectoryRail(trajectory_store=store))

            # Attach write-tool guidance rail when a shared team workspace is configured.
            # workspace_dir now points to team_home(team_name)/team-workspace (resolved
            # via the caller-redirected openjiuwen_home), and dest_dir is the stable
            # case-level artifacts directory that survives clean_team's rmtree.
            # The rail pre-harvests artifacts/ into dest_dir before clean_team fires so
            # they are not lost when the team_home subtree is deleted.
            if case_dir is not None:
                from openjiuwen.agent_teams.paths import team_home
                from openjiuwen.rsi.evaluator.eval_team_rail import (
                    EvalTeamRail,
                )

                shared_ws = team_home(team_name) / "team-workspace"
                agent.add_rail(
                    EvalTeamRail(
                        team_name=team_name,
                        workspace_dir=shared_ws,
                        dest_dir=case_dir / "artifacts",
                    )
                )

        return customizer


def create_team_agent_spec_from_team_skill(team_skill_ref_path: str | Path) -> TeamAgentSpec:
    """Create a TeamAgentSpec from a direct Team Skill path.

    Deprecated compatibility helper kept for callers that predate
    ``EvaluatorConfig``-based team construction.
    """
    config = EvaluatorConfig()
    return TeamSkillTeamFactory(config=config).create_team_spec(team_skill_ref_path=team_skill_ref_path)


def create_team_from_team_skill(team_skill_ref_path: str | Path) -> Any:
    """Create a Team runtime from a direct Team Skill path.

    Deprecated compatibility helper kept for existing imports.
    """
    return create_team_agent_spec_from_team_skill(team_skill_ref_path).build()


__all__ = [
    "TeamSkillTeamFactory",
    "create_team_agent_spec_from_team_skill",
    "create_team_from_team_skill",
    "is_team_coordinator_role",
    "load_team_skill_role_definitions",
    "resolve_team_skill_rail_config",
    "resolve_team_name_from_skill_path",
]
